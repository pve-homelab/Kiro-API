mod config;
mod cursor;
mod log_buffer;
mod openai;
mod prompt;
mod response;
mod server;
mod state;
mod tui;
mod usage;

use anyhow::Result;
use clap::{Parser, Subcommand};
use config::Config;
use state::AppState;
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(
    name = "kiro-api",
    about = "Kiro-API — OpenAI-compatible /v1 bridge to the Kiro CLI with a management TUI",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Optional path to config.toml
    #[arg(long, global = true)]
    config: Option<std::path::PathBuf>,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Launch the management TUI (default). HTTP API starts automatically.
    Tui {
        /// Open the TUI without starting the HTTP server (press `s` later)
        #[arg(long)]
        no_autostart: bool,
    },
    /// Run the HTTP server without the TUI (for systemd / launchd / NSSM / etc.)
    Serve,
    /// Print resolved config path and defaults
    ConfigPath,
    /// Check that the Kiro CLI binary is reachable
    Doctor {
        /// Run a live smoke completion through the bridge backend
        #[arg(long)]
        full: bool,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    init_tracing();

    let (config, config_path) = match &cli.config {
        Some(path) => {
            let mut cfg = if path.exists() {
                Config::load(path)?
            } else {
                let cfg = Config::default();
                cfg.save(path)?;
                cfg
            };
            cfg.apply_env_overrides();
            (cfg, path.clone())
        }
        None => Config::load_or_create()?,
    };

    let state = AppState::new(config, config_path.clone());

    match cli.command.unwrap_or(Commands::Tui {
        no_autostart: false,
    }) {
        Commands::Tui { no_autostart } => tui::run_tui(state, !no_autostart).await?,
        Commands::Serve => {
            state.logs.info("starting headless server");
            println!(
                "tip: prefer `kiro-api` (TUI + API). `serve` is headless-only — no dashboard."
            );
            server::start_in_background(state.clone()).await?;
            tokio::signal::ctrl_c().await?;
            server::stop_server(&state).await?;
        }
        Commands::ConfigPath => {
            println!("{}", config_path.display());
        }
        Commands::Doctor { full } => {
            run_doctor(&state, &config_path, full).await?;
        }
    }

    Ok(())
}

async fn run_doctor(state: &AppState, config_path: &std::path::Path, full: bool) -> Result<()> {
    let cfg = state.config.read().clone();
    let mut ok = true;

    match state.backend.resolve_launch() {
        Ok(launch) => {
            println!("OK  launch={}", launch.display);
            match cursor::probe_health_launch(&launch).await {
                Ok(ver) => {
                    println!("OK  version={ver}");
                }
                Err(err) => {
                    eprintln!("FAIL probe: {err:#}");
                    ok = false;
                }
            }
        }
        Err(err) => {
            eprintln!("FAIL locate agent: {err:#}");
            ok = false;
        }
    }

    println!("OK  config={}", config_path.display());
    println!("OK  listen={}", cfg.listen_addr());
    println!("OK  v1={}", cfg.v1_url());
    println!("OK  model={}", cfg.cursor.default_model);
    println!("OK  mode={}", cfg.cursor.mode);
    println!("OK  profile={}", cfg.cursor.profile);
    println!(
        "OK  json_mode={} flatten={} timeout={}s concurrency={} reject_when_busy={}",
        cfg.cursor.json_mode,
        cfg.cursor.message_flatten_mode,
        cfg.server.request_timeout_secs,
        cfg.server.max_concurrency,
        cfg.server.reject_when_busy
    );

    if cfg.auth.require_auth && !cfg.auth.api_key.is_empty() {
        println!("OK  auth=enabled (Bearer token required on /v1)");
    } else {
        println!("WARN auth=disabled (set auth.api_key for non-local use)");
    }

    if full {
        print!("..  smoke test (agent completion) ");
        let timeout = cfg.server.request_timeout_secs.min(120);
        match cursor::smoke_test(&state.backend, timeout).await {
            Ok(text) => {
                println!("OK");
                println!("OK  smoke_reply={text}");
            }
            Err(err) => {
                println!("FAIL");
                eprintln!("FAIL smoke: {err:#}");
                ok = false;
            }
        }
    }

    if !ok {
        std::process::exit(1);
    }
    Ok(())
}

fn init_tracing() {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .try_init();
}
