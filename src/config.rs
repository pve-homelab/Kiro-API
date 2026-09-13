use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub server: ServerConfig,
    pub cursor: CursorConfig,
    pub auth: AuthConfig,
    pub logging: LoggingConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    /// Max concurrent Kiro CLI processes.
    pub max_concurrency: usize,
    /// Per-request timeout in seconds.
    pub request_timeout_secs: u64,
    /// When true, return HTTP 429 immediately if max_concurrency slots are full.
    pub reject_when_busy: bool,
    /// Seconds to wait for a concurrency slot before returning 429 (0 = no wait).
    pub queue_wait_secs: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct CursorConfig {
    /// Path to the `agent` / `cursor-agent` binary. Empty = auto-detect.
    pub binary: String,
    /// Default model id passed to `--model`.
    pub default_model: String,
    /// Workspace directory for agent runs. Empty = process cwd.
    pub workspace: String,
    /// ask | agent | plan
    pub mode: String,
    /// Pass `--trust` for headless runs.
    pub trust: bool,
    /// Pass `--force` (allows writes/shell). Keep false for chat-only proxies.
    pub force: bool,
    /// Extra CLI args appended to every invocation.
    pub extra_args: Vec<String>,
    /// API key forwarded to Kiro CLI (`KIRO_API_KEY`).
    pub cursor_api_key: String,
    /// Append JSON-only instruction and normalize JSON in responses.
    pub json_mode: bool,
    /// flat | fold_system | system_last
    pub message_flatten_mode: String,
    /// Prepended to every prompt.
    pub prompt_prefix: String,
    /// Appended to every prompt.
    pub prompt_suffix: String,
    /// Preset profile: chat | json_api | long_running (applied on load/save if set).
    pub profile: String,
    /// Advertised context budget (tokens). Soft warn when prompts exceed this.
    pub max_context_tokens: u32,
    /// When true, truncate prompts that exceed max_context_tokens (default false).
    pub truncate_over_context: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AuthConfig {
    /// If set, require `Authorization: Bearer <key>` on /v1 routes.
    pub api_key: String,
    /// Bind advice shown in TUI; server honors host.
    pub require_auth: bool,
    /// Generate a random bridge API key when creating a new config file.
    pub generate_api_key_on_first_run: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct LoggingConfig {
    pub level: String,
    /// Max in-memory log lines for the TUI.
    pub ring_capacity: usize,
    /// Optional log file path.
    pub file: String,
    /// Redact bearer tokens and API keys in log messages.
    pub redact_secrets: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            server: ServerConfig::default(),
            cursor: CursorConfig::default(),
            auth: AuthConfig::default(),
            logging: LoggingConfig::default(),
        }
    }
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".into(),
            port: 8788,
            max_concurrency: 2,
            request_timeout_secs: 600,
            // Queue by default so concurrent /v1 callers wait instead of getting 429.
            reject_when_busy: false,
            queue_wait_secs: 0,
        }
    }
}

impl Default for CursorConfig {
    fn default() -> Self {
        Self {
            binary: String::new(),
            default_model: "auto".into(),
            workspace: String::new(),
            mode: "ask".into(),
            trust: true,
            force: false,
            extra_args: Vec::new(),
            cursor_api_key: String::new(),
            json_mode: false,
            message_flatten_mode: "flat".into(),
            prompt_prefix: String::new(),
            prompt_suffix: String::new(),
            profile: "chat".into(),
            max_context_tokens: 128_000,
            truncate_over_context: false,
        }
    }
}

impl Default for AuthConfig {
    fn default() -> Self {
        Self {
            api_key: String::new(),
            require_auth: false,
            generate_api_key_on_first_run: true,
        }
    }
}

impl Default for LoggingConfig {
    fn default() -> Self {
        Self {
            level: "info".into(),
            ring_capacity: 2000,
            file: String::new(),
            redact_secrets: true,
        }
    }
}

impl Config {
    pub fn config_dir() -> Result<PathBuf> {
        let base = dirs::config_dir().context("could not resolve config directory")?;
        Ok(base.join("kiro-api"))
    }

    /// Prefer new config dir; migrate from legacy `cursor-v1-bridge` on first run if needed.
    pub fn default_path() -> Result<PathBuf> {
        let path = Self::config_dir()?.join("config.toml");
        if !path.exists() {
            if let Some(legacy) = legacy_config_path() {
                if legacy.exists() {
                    if let Some(parent) = path.parent() {
                        let _ = fs::create_dir_all(parent);
                    }
                    let _ = fs::copy(&legacy, &path);
                }
            }
        }
        Ok(path)
    }

    pub fn load_or_create() -> Result<(Self, PathBuf, EnvOverrideReport)> {
        let path = Self::default_path()?;
        if path.exists() {
            let mut cfg = Self::load(&path)?;
            let report = cfg.apply_env_overrides();
            Ok((cfg, path, report))
        } else {
            let mut cfg = Self::default();
            if cfg.auth.generate_api_key_on_first_run {
                cfg.auth.api_key = generate_bridge_api_key();
                cfg.auth.require_auth = true;
            }
            cfg.apply_profile();
            let report = cfg.apply_env_overrides();
            cfg.save(&path)?;
            Ok((cfg, path, report))
        }
    }

    pub fn load(path: &Path) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("failed to read config {}", path.display()))?;
        let mut cfg: Config = toml::from_str(&raw)
            .with_context(|| format!("failed to parse config {}", path.display()))?;
        cfg.apply_profile();
        Ok(cfg)
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        let raw = toml::to_string_pretty(self).context("serialize config")?;
        fs::write(path, raw).with_context(|| format!("failed to write {}", path.display()))?;
        Ok(())
    }

    pub fn listen_addr(&self) -> String {
        format!("{}:{}", self.server.host, self.server.port)
    }

    pub fn base_url(&self) -> String {
        format!("http://{}:{}", self.server.host, self.server.port)
    }

    pub fn v1_url(&self) -> String {
        format!("{}/v1", self.base_url())
    }

    /// Apply named profile presets (chat, json_api, long_running).
    pub fn apply_profile(&mut self) {
        match self.cursor.profile.trim().to_lowercase().as_str() {
            "json_api" | "json-api" | "json" => {
                self.cursor.mode = "ask".into();
                self.cursor.json_mode = true;
                self.cursor.message_flatten_mode = "fold_system".into();
                if self.cursor.prompt_suffix.is_empty() {
                    self.cursor.prompt_suffix =
                        "Respond with valid JSON only. No markdown fences.".into();
                }
                self.cursor.force = false;
            }
            "long_running" | "long-running" | "long" => {
                self.server.request_timeout_secs = 900;
                self.server.max_concurrency = 1;
                // Queue instead of immediate 429 when the single slot is busy.
                self.server.reject_when_busy = false;
                self.server.queue_wait_secs = 0;
                self.cursor.mode = "ask".into();
                self.cursor.force = false;
            }
            _ => {
                // chat — keep defaults unless user customized
            }
        }
    }

    pub fn set_profile(&mut self, profile: &str) {
        self.cursor.profile = profile.into();
        self.apply_profile();
    }

    /// Apply product-specific `KIRO_API_*` env overrides.
    ///
    /// Shared `BRIDGE_*` names are **not** applied (they collide when Cursor-API and
    /// Kiro-API run on the same machine). A leftover `BRIDGE_PORT=8788` must not
    /// silently steal Cursor-API's bind, and vice versa.
    pub fn apply_env_overrides(&mut self) -> EnvOverrideReport {
        let mut report = EnvOverrideReport::default();

        warn_ignored_shared_bridge_env();

        if let Some(host) = non_empty_env("KIRO_API_HOST") {
            self.server.host = host;
            report.host_source = "env:KIRO_API_HOST";
        }
        if let Some(port) = non_empty_env("KIRO_API_PORT") {
            if let Ok(p) = port.parse() {
                self.server.port = p;
                report.port_source = "env:KIRO_API_PORT";
            }
        }
        // Forwarded to kiro-cli (product API key), not the HTTP bridge auth key.
        if let Some(key) = non_empty_env("KIRO_API_KEY") {
            self.cursor.cursor_api_key = key;
        }
        if let Some(key) = non_empty_env("KIRO_API_AUTH_KEY") {
            self.auth.api_key = key;
            self.auth.require_auth = true;
        }
        if let Some(ws) = non_empty_env("KIRO_API_WORKSPACE") {
            self.cursor.workspace = ws;
        } else if let Some(ws) = non_empty_env("CURSOR_WORKSPACE") {
            // Legacy workspace override still honored (not a port/host collision risk).
            self.cursor.workspace = ws;
        }
        if let Some(model) = non_empty_env("KIRO_API_DEFAULT_MODEL") {
            self.cursor.default_model = model;
        }
        if let Some(timeout) = non_empty_env("KIRO_API_TIMEOUT_SECS") {
            if let Ok(t) = timeout.parse() {
                self.server.request_timeout_secs = t;
            }
        }
        if let Some(v) = non_empty_env("KIRO_API_JSON_MODE") {
            self.cursor.json_mode = env_truthy(&v);
        }
        if let Some(v) = non_empty_env("KIRO_API_MAX_CONTEXT_TOKENS") {
            if let Ok(n) = v.parse::<u32>() {
                if n > 0 {
                    self.cursor.max_context_tokens = n;
                }
            }
        }
        if let Some(v) = non_empty_env("KIRO_API_TRUNCATE_OVER_CONTEXT") {
            self.cursor.truncate_over_context = env_truthy(&v);
        }
        if let Some(v) = non_empty_env("KIRO_API_REJECT_WHEN_BUSY") {
            self.server.reject_when_busy = env_truthy(&v);
        }
        if let Some(v) = non_empty_env("KIRO_API_QUEUE_WAIT_SECS") {
            if let Ok(n) = v.parse() {
                self.server.queue_wait_secs = n;
            }
        }
        if let Some(v) = non_empty_env("KIRO_API_MAX_CONCURRENCY") {
            if let Ok(n) = v.parse::<usize>() {
                if n > 0 {
                    self.server.max_concurrency = n;
                }
            }
        }

        report
    }
}

/// Where the effective bind host/port came from after env overlay.
#[derive(Debug, Clone)]
pub struct EnvOverrideReport {
    pub host_source: &'static str,
    pub port_source: &'static str,
}

impl Default for EnvOverrideReport {
    fn default() -> Self {
        Self {
            host_source: "config",
            port_source: "config",
        }
    }
}

fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.is_empty())
}

fn env_truthy(v: &str) -> bool {
    matches!(v.to_lowercase().as_str(), "1" | "true" | "yes" | "on")
}

fn warn_ignored_shared_bridge_env() {
    const SHARED: &[&str] = &[
        "BRIDGE_HOST",
        "BRIDGE_PORT",
        "BRIDGE_API_KEY",
        "BRIDGE_TIMEOUT_SECS",
        "BRIDGE_JSON_MODE",
        "BRIDGE_DEFAULT_MODEL",
        "BRIDGE_MAX_CONTEXT_TOKENS",
        "BRIDGE_TRUNCATE_OVER_CONTEXT",
    ];
    let found: Vec<&str> = SHARED
        .iter()
        .copied()
        .filter(|name| non_empty_env(name).is_some())
        .collect();
    if found.is_empty() {
        return;
    }
    tracing::warn!(
        "ignoring shared env [{}] — use KIRO_API_* instead (avoids Cursor-API / Kiro-API collisions)",
        found.join(", ")
    );
}

/// Human-readable ready banner for logs / stdout / TUI status.
pub fn ready_banner(cfg: &Config, agent_version: Option<String>) -> String {
    let agent = agent_version.unwrap_or_else(|| "unknown".into());
    let auth = if cfg.auth.require_auth && !cfg.auth.api_key.is_empty() {
        "Bearer auth ON"
    } else {
        "auth off"
    };
    format!(
        "Kiro-API ready · {} · /health → 200 · model={} · mode={} · profile={} · kiro={} · {}",
        cfg.v1_url(),
        cfg.cursor.default_model,
        cfg.cursor.mode,
        cfg.cursor.profile,
        agent,
        auth,
    )
}

fn legacy_config_path() -> Option<PathBuf> {
    let base = dirs::config_dir()?;
    Some(base.join("cursor-v1-bridge").join("config.toml"))
}

pub fn generate_bridge_api_key() -> String {
    format!("bridge-{}", uuid::Uuid::new_v4())
}

pub fn redact_secrets(text: &str, enabled: bool) -> String {
    if !enabled {
        return text.to_string();
    }
    let mut out = text.to_string();
    for prefix in ["Bearer ", "bearer ", "bridge-", "sk-"] {
        if let Some(idx) = out.find(prefix) {
            let start = idx + prefix.len();
            let end = out[start..]
                .find(|c: char| c.is_whitespace() || c == '"' || c == '\'')
                .map(|i| start + i)
                .unwrap_or(out.len());
            if end > start {
                out.replace_range(start..end, "***");
            }
        }
    }
    out
}
