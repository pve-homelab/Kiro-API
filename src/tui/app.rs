use super::cli_term::CliTerminal;
use crate::server::{start_in_background, stop_server};
use crate::state::AppState;
use anyhow::Result;
use crossterm::event::{Event, EventStream, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use crossterm::ExecutableCommand;
use futures_util::StreamExt;
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;
use std::io::stdout;
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Dashboard,
    Config,
    Logs,
    Usage,
    Agent,
    Help,
    Cli,
}

impl Tab {
    pub const ALL: [Tab; 7] = [
        Tab::Dashboard,
        Tab::Config,
        Tab::Logs,
        Tab::Usage,
        Tab::Agent,
        Tab::Help,
        Tab::Cli,
    ];

    pub fn title(self) -> &'static str {
        match self {
            Tab::Dashboard => "Dashboard",
            Tab::Config => "Config",
            Tab::Logs => "Logs",
            Tab::Usage => "Usage",
            Tab::Agent => "Agent",
            Tab::Help => "Help",
            Tab::Cli => "CLI",
        }
    }

    pub fn next(self) -> Self {
        match self {
            Tab::Dashboard => Tab::Config,
            Tab::Config => Tab::Logs,
            Tab::Logs => Tab::Usage,
            Tab::Usage => Tab::Agent,
            Tab::Agent => Tab::Help,
            Tab::Help => Tab::Cli,
            Tab::Cli => Tab::Dashboard,
        }
    }

    pub fn prev(self) -> Self {
        match self {
            Tab::Dashboard => Tab::Cli,
            Tab::Config => Tab::Dashboard,
            Tab::Logs => Tab::Config,
            Tab::Usage => Tab::Logs,
            Tab::Agent => Tab::Usage,
            Tab::Help => Tab::Agent,
            Tab::Cli => Tab::Help,
        }
    }

    pub fn from_index(i: usize) -> Option<Self> {
        Self::ALL.get(i).copied()
    }

    pub fn is_pty(self) -> bool {
        matches!(self, Tab::Agent | Tab::Cli)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfigField {
    Host,
    Port,
    Profile,
    Model,
    Mode,
    Workspace,
    FlattenMode,
    JsonMode,
    MaxContextTokens,
    ApiKey,
    CursorApiKey,
    MaxConcurrency,
    Timeout,
    RejectWhenBusy,
}

impl ConfigField {
    pub const ALL: [ConfigField; 14] = [
        ConfigField::Host,
        ConfigField::Port,
        ConfigField::Profile,
        ConfigField::Model,
        ConfigField::Mode,
        ConfigField::Workspace,
        ConfigField::FlattenMode,
        ConfigField::JsonMode,
        ConfigField::MaxContextTokens,
        ConfigField::ApiKey,
        ConfigField::CursorApiKey,
        ConfigField::MaxConcurrency,
        ConfigField::Timeout,
        ConfigField::RejectWhenBusy,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Self::Host => "Bind host",
            Self::Port => "Port",
            Self::Profile => "Profile (chat/json_api/long_running)",
            Self::Model => "Default model",
            Self::Mode => "Mode (ask/agent/plan)",
            Self::Workspace => "Workspace",
            Self::FlattenMode => "Flatten mode",
            Self::JsonMode => "JSON mode",
            Self::MaxContextTokens => "Max context tokens",
            Self::ApiKey => "Bridge API key",
            Self::CursorApiKey => "Kiro API key",
            Self::MaxConcurrency => "Max concurrency",
            Self::Timeout => "Timeout (secs)",
            Self::RejectWhenBusy => "Reject when busy (429)",
        }
    }

    pub fn next(self) -> Self {
        let idx = Self::ALL.iter().position(|f| *f == self).unwrap_or(0);
        Self::ALL[(idx + 1) % Self::ALL.len()]
    }

    pub fn prev(self) -> Self {
        let idx = Self::ALL.iter().position(|f| *f == self).unwrap_or(0);
        Self::ALL[(idx + Self::ALL.len() - 1) % Self::ALL.len()]
    }
}

pub struct TuiApp {
    pub state: AppState,
    pub tab: Tab,
    pub config_field: ConfigField,
    pub editing: bool,
    pub edit_buffer: String,
    pub log_scroll: u16,
    pub usage_scroll: u16,
    pub help_scroll: u16,
    pub status_message: String,
    pub should_quit: bool,
    pub smoke_running: bool,
    pub cli: Option<CliTerminal>,
    pub agent: Option<CliTerminal>,
    pub cli_cols: u16,
    pub cli_rows: u16,
}

impl TuiApp {
    pub fn new(state: AppState) -> Self {
        Self {
            state,
            tab: Tab::Dashboard,
            config_field: ConfigField::Host,
            editing: false,
            edit_buffer: String::new(),
            log_scroll: 0,
            usage_scroll: 0,
            help_scroll: 0,
            status_message: "Kiro-API · s start/stop · 5 Agent · 7 CLI · q quit".into(),
            should_quit: false,
            smoke_running: false,
            cli: None,
            agent: None,
            cli_cols: 80,
            cli_rows: 24,
        }
    }

    fn workspace_cwd(&self) -> Option<String> {
        let cfg = self.state.config.read();
        if cfg.cursor.workspace.is_empty() {
            None
        } else {
            Some(cfg.cursor.workspace.clone())
        }
    }

    pub fn ensure_cli(&mut self) {
        if self.cli.as_ref().is_some_and(|c| c.alive()) {
            return;
        }
        let cwd = self.workspace_cwd();
        match CliTerminal::start(self.cli_cols, self.cli_rows, cwd.as_deref()) {
            Ok(term) => {
                self.status_message =
                    "CLI ready — generic shell. F1–F7 or Ctrl+←/→ switch tabs.".into();
                self.cli = Some(term);
            }
            Err(err) => {
                self.status_message = format!("CLI start failed: {err:#}");
                self.state.logs.error(format!("CLI pty failed: {err:#}"));
            }
        }
    }

    pub fn ensure_agent(&mut self) {
        if self.agent.as_ref().is_some_and(|c| c.alive()) {
            return;
        }
        let cwd = self.workspace_cwd();
        let (model, trust) = {
            let cfg = self.state.config.read();
            (cfg.cursor.default_model.clone(), cfg.cursor.trust)
        };
        match self.state.backend.resolve_launch() {
            Ok(launch) => {
                match CliTerminal::start_agent(
                    self.cli_cols,
                    self.cli_rows,
                    cwd.as_deref(),
                    &launch.program,
                    &launch.prefix_args,
                    &model,
                    trust,
                ) {
                    Ok(term) => {
                        self.status_message = format!(
                            "Agent chat ready ({}) — separate from /v1. F1–F7 / Ctrl+←→ leave.",
                            launch.display
                        );
                        self.agent = Some(term);
                    }
                    Err(err) => {
                        self.status_message = format!("Agent start failed: {err:#}");
                        self.state.logs.error(format!("Agent pty failed: {err:#}"));
                    }
                }
            }
            Err(err) => {
                self.status_message = format!("Agent missing: {err:#}");
            }
        }
    }

    pub fn tick_cli(&mut self) {
        if let Some(cli) = self.cli.as_mut() {
            cli.poll();
            if !cli.alive() && self.tab == Tab::Cli {
                self.status_message =
                    "CLI shell exited — press r or Shift+R on CLI tab to restart.".into();
            }
        }
        if let Some(agent) = self.agent.as_mut() {
            agent.poll();
            if !agent.alive() && self.tab == Tab::Agent {
                self.status_message =
                    "Agent exited — press r or Shift+R on Agent tab to restart.".into();
            }
        }
    }

    fn begin_edit(&mut self) {
        let cfg = self.state.config.read().clone();
        self.edit_buffer = match self.config_field {
            ConfigField::Host => cfg.server.host,
            ConfigField::Port => cfg.server.port.to_string(),
            ConfigField::Profile => cfg.cursor.profile,
            ConfigField::Model => cfg.cursor.default_model,
            ConfigField::Mode => cfg.cursor.mode,
            ConfigField::Workspace => cfg.cursor.workspace,
            ConfigField::FlattenMode => cfg.cursor.message_flatten_mode,
            ConfigField::JsonMode => cfg.cursor.json_mode.to_string(),
            ConfigField::MaxContextTokens => cfg.cursor.max_context_tokens.to_string(),
            ConfigField::ApiKey => cfg.auth.api_key,
            ConfigField::CursorApiKey => cfg.cursor.cursor_api_key,
            ConfigField::MaxConcurrency => cfg.server.max_concurrency.to_string(),
            ConfigField::Timeout => cfg.server.request_timeout_secs.to_string(),
            ConfigField::RejectWhenBusy => cfg.server.reject_when_busy.to_string(),
        };
        self.editing = true;
    }

    fn apply_edit(&mut self) -> Result<()> {
        {
            let mut cfg = self.state.config.write();
            match self.config_field {
                ConfigField::Host => cfg.server.host = self.edit_buffer.trim().to_string(),
                ConfigField::Port => {
                    cfg.server.port = self.edit_buffer.trim().parse().unwrap_or(cfg.server.port);
                }
                ConfigField::Profile => {
                    cfg.set_profile(self.edit_buffer.trim());
                }
                ConfigField::Model => {
                    cfg.cursor.default_model = self.edit_buffer.trim().to_string();
                }
                ConfigField::Mode => {
                    cfg.cursor.mode = self.edit_buffer.trim().to_string();
                }
                ConfigField::Workspace => {
                    cfg.cursor.workspace = self.edit_buffer.trim().to_string();
                }
                ConfigField::FlattenMode => {
                    cfg.cursor.message_flatten_mode = self.edit_buffer.trim().to_string();
                }
                ConfigField::JsonMode => {
                    cfg.cursor.json_mode = matches!(
                        self.edit_buffer.trim().to_lowercase().as_str(),
                        "1" | "true" | "yes" | "on"
                    );
                }
                ConfigField::MaxContextTokens => {
                    let parsed: u32 = self
                        .edit_buffer
                        .trim()
                        .parse()
                        .unwrap_or(cfg.cursor.max_context_tokens);
                    if parsed > 0 {
                        cfg.cursor.max_context_tokens = parsed;
                    }
                }
                ConfigField::ApiKey => {
                    cfg.auth.api_key = self.edit_buffer.trim().to_string();
                    cfg.auth.require_auth = !cfg.auth.api_key.is_empty();
                }
                ConfigField::CursorApiKey => {
                    cfg.cursor.cursor_api_key = self.edit_buffer.trim().to_string();
                }
                ConfigField::MaxConcurrency => {
                    cfg.server.max_concurrency = self
                        .edit_buffer
                        .trim()
                        .parse()
                        .unwrap_or(cfg.server.max_concurrency);
                }
                ConfigField::Timeout => {
                    cfg.server.request_timeout_secs = self
                        .edit_buffer
                        .trim()
                        .parse()
                        .unwrap_or(cfg.server.request_timeout_secs);
                }
                ConfigField::RejectWhenBusy => {
                    cfg.server.reject_when_busy = matches!(
                        self.edit_buffer.trim().to_lowercase().as_str(),
                        "1" | "true" | "yes" | "on"
                    );
                }
            }
            let path = self.state.config_path.read().clone();
            cfg.save(&path)?;
        }
        self.state.reload_backend_from_config();
        self.editing = false;
        self.status_message = format!("Saved {}", self.config_field.label());
        self.state
            .logs
            .info(format!("config updated: {}", self.config_field.label()));
        Ok(())
    }

    fn cycle_profile(&mut self) {
        let mut cfg = self.state.config.write();
        let next = match cfg.cursor.profile.as_str() {
            "chat" => "json_api",
            "json_api" | "json-api" | "json" => "long_running",
            _ => "chat",
        };
        cfg.set_profile(next);
        let path = self.state.config_path.read().clone();
        let _ = cfg.save(&path);
        self.status_message = format!("Profile set to {next}");
        drop(cfg);
        self.state.reload_backend_from_config();
    }

    async fn run_smoke_test(&mut self) {
        if self.smoke_running {
            self.status_message = "Smoke test already running".into();
            return;
        }
        self.smoke_running = true;
        self.status_message = "Running smoke test…".into();
        let backend = self.state.backend.clone();
        let timeout = self
            .state
            .config
            .read()
            .server
            .request_timeout_secs
            .min(120);
        match crate::cursor::smoke_test(&backend, timeout).await {
            Ok(text) => {
                let preview: String = text.chars().take(80).collect();
                self.status_message = format!("Smoke OK: {preview}");
                self.state.logs.info(format!("smoke test ok: {preview}"));
            }
            Err(err) => {
                self.status_message = format!("Smoke failed: {err:#}");
                self.state.logs.error(format!("smoke test failed: {err:#}"));
            }
        }
        self.smoke_running = false;
    }

    fn switch_tab(&mut self, tab: Tab) {
        self.tab = tab;
        match tab {
            Tab::Cli => self.ensure_cli(),
            Tab::Agent => self.ensure_agent(),
            _ => {}
        }
    }

    async fn handle_key(&mut self, key: crossterm::event::KeyEvent) {
        if key.kind != KeyEventKind::Press && key.kind != KeyEventKind::Repeat {
            return;
        }

        // Global quit always available with Ctrl+Q (even on CLI tab).
        if key.code == KeyCode::Char('q') && key.modifiers.contains(KeyModifiers::CONTROL) {
            if self.state.is_running() {
                let _ = stop_server(&self.state).await;
            }
            self.should_quit = true;
            return;
        }

        // Tab navigation that works while Agent/CLI have focus.
        if matches!(key.code, KeyCode::F(1)) {
            self.switch_tab(Tab::Dashboard);
            return;
        }
        if matches!(key.code, KeyCode::F(2)) {
            self.switch_tab(Tab::Config);
            return;
        }
        if matches!(key.code, KeyCode::F(3)) {
            self.switch_tab(Tab::Logs);
            return;
        }
        if matches!(key.code, KeyCode::F(4)) {
            self.switch_tab(Tab::Usage);
            return;
        }
        if matches!(key.code, KeyCode::F(5)) {
            self.switch_tab(Tab::Agent);
            return;
        }
        if matches!(key.code, KeyCode::F(6)) {
            self.switch_tab(Tab::Help);
            return;
        }
        if matches!(key.code, KeyCode::F(7)) {
            self.switch_tab(Tab::Cli);
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL)
            && matches!(key.code, KeyCode::Left | KeyCode::Right)
        {
            if key.code == KeyCode::Right {
                self.switch_tab(self.tab.next());
            } else {
                self.switch_tab(self.tab.prev());
            }
            return;
        }

        if self.editing {
            match key.code {
                KeyCode::Esc => {
                    self.editing = false;
                    self.status_message = "Edit cancelled".into();
                }
                KeyCode::Enter => {
                    if let Err(err) = self.apply_edit() {
                        self.status_message = format!("Save failed: {err:#}");
                    }
                }
                KeyCode::Backspace => {
                    self.edit_buffer.pop();
                }
                KeyCode::Char(c) => {
                    self.edit_buffer.push(c);
                }
                _ => {}
            }
            return;
        }

        // Agent / CLI tabs: forward keys into the PTY (except nav handled above).
        if self.tab.is_pty() {
            let restart = key.code == KeyCode::Char('R')
                && key.modifiers.contains(KeyModifiers::SHIFT);
            let soft_restart = key.code == KeyCode::Char('r');
            if self.tab == Tab::Agent {
                if restart {
                    self.agent = None;
                    self.ensure_agent();
                    return;
                }
                if let Some(term) = self.agent.as_mut() {
                    if term.alive() {
                        term.handle_key(key);
                    } else if soft_restart {
                        self.agent = None;
                        self.ensure_agent();
                    }
                } else {
                    self.ensure_agent();
                }
            } else {
                if restart {
                    self.cli = None;
                    self.ensure_cli();
                    return;
                }
                if let Some(term) = self.cli.as_mut() {
                    if term.alive() {
                        term.handle_key(key);
                    } else if soft_restart {
                        self.cli = None;
                        self.ensure_cli();
                    }
                } else {
                    self.ensure_cli();
                }
            }
            return;
        }

        match key.code {
            KeyCode::Char('q') | KeyCode::Esc => {
                if self.state.is_running() {
                    let _ = stop_server(&self.state).await;
                }
                self.should_quit = true;
            }
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                if self.state.is_running() {
                    let _ = stop_server(&self.state).await;
                }
                self.should_quit = true;
            }
            KeyCode::Tab | KeyCode::Right => self.switch_tab(self.tab.next()),
            KeyCode::BackTab | KeyCode::Left => self.switch_tab(self.tab.prev()),
            KeyCode::Char('1') => self.switch_tab(Tab::Dashboard),
            KeyCode::Char('2') => self.switch_tab(Tab::Config),
            KeyCode::Char('3') => self.switch_tab(Tab::Logs),
            KeyCode::Char('4') => self.switch_tab(Tab::Usage),
            KeyCode::Char('5') => self.switch_tab(Tab::Agent),
            KeyCode::Char('6') => self.switch_tab(Tab::Help),
            KeyCode::Char('7') => self.switch_tab(Tab::Cli),
            KeyCode::Char('s') if self.tab == Tab::Dashboard => {
                if self.state.is_running() {
                    match stop_server(&self.state).await {
                        Ok(()) => self.status_message = "Server stopped".into(),
                        Err(err) => self.status_message = format!("Stop failed: {err:#}"),
                    }
                } else {
                    match start_in_background(self.state.clone()).await {
                        Ok(()) => {
                            let cfg = self.state.config.read().clone();
                            self.status_message = crate::config::ready_banner(
                                &cfg,
                                self.state.agent_version.read().clone(),
                            );
                        }
                        Err(err) => self.status_message = format!("Start failed: {err:#}"),
                    }
                }
            }
            KeyCode::Char('r') if self.tab == Tab::Dashboard => {
                match self.state.backend.resolve_launch() {
                    Ok(launch) => match crate::cursor::probe_health_launch(&launch).await {
                        Ok(ver) => {
                            *self.state.agent_version.write() = Some(ver.clone());
                            self.status_message = format!("Agent OK: {ver} ({})", launch.display);
                            self.state.logs.info(format!("health check: {ver}"));
                        }
                        Err(err) => {
                            self.status_message = format!("Agent unhealthy: {err:#}");
                            self.state.logs.warn(format!("health check failed: {err:#}"));
                        }
                    },
                    Err(err) => {
                        self.status_message = format!("Agent missing: {err:#}");
                    }
                }
            }
            KeyCode::Char('t') if self.tab == Tab::Dashboard => {
                self.run_smoke_test().await;
            }
            KeyCode::Char('c') if self.tab == Tab::Logs => {
                self.state.logs.clear();
                self.status_message = "Logs cleared".into();
            }
            KeyCode::Char('x') if self.tab == Tab::Usage => {
                self.state.usage.reset();
                self.status_message = "Usage counters reset".into();
            }
            KeyCode::Up if self.tab == Tab::Config => {
                self.config_field = self.config_field.prev();
            }
            KeyCode::Down if self.tab == Tab::Config => {
                self.config_field = self.config_field.next();
            }
            KeyCode::Enter if self.tab == Tab::Config => self.begin_edit(),
            KeyCode::Char('e') if self.tab == Tab::Config => self.begin_edit(),
            KeyCode::Char('p') if self.tab == Tab::Config => self.cycle_profile(),
            KeyCode::Up if self.tab == Tab::Logs => {
                self.log_scroll = self.log_scroll.saturating_add(1);
            }
            KeyCode::Down if self.tab == Tab::Logs => {
                self.log_scroll = self.log_scroll.saturating_sub(1);
            }
            KeyCode::PageUp if self.tab == Tab::Logs => {
                self.log_scroll = self.log_scroll.saturating_add(10);
            }
            KeyCode::PageDown if self.tab == Tab::Logs => {
                self.log_scroll = self.log_scroll.saturating_sub(10);
            }
            KeyCode::Up if self.tab == Tab::Usage => {
                self.usage_scroll = self.usage_scroll.saturating_add(1);
            }
            KeyCode::Down if self.tab == Tab::Usage => {
                self.usage_scroll = self.usage_scroll.saturating_sub(1);
            }
            KeyCode::Up if self.tab == Tab::Help => {
                self.help_scroll = self.help_scroll.saturating_sub(1);
            }
            KeyCode::Down if self.tab == Tab::Help => {
                self.help_scroll = self.help_scroll.saturating_add(1);
            }
            KeyCode::PageUp if self.tab == Tab::Help => {
                self.help_scroll = self.help_scroll.saturating_sub(10);
            }
            KeyCode::PageDown if self.tab == Tab::Help => {
                self.help_scroll = self.help_scroll.saturating_add(10);
            }
            KeyCode::Home if self.tab == Tab::Help => {
                self.help_scroll = 0;
            }
            KeyCode::End if self.tab == Tab::Help => {
                let max = super::help::help_lines().len().saturating_sub(5) as u16;
                self.help_scroll = max;
            }
            KeyCode::Char('w') if self.tab == Tab::Config => {
                {
                    let mut cfg = self.state.config.write();
                    cfg.cursor.force = !cfg.cursor.force;
                    let path = self.state.config_path.read().clone();
                    let _ = cfg.save(&path);
                    self.status_message = format!(
                        "force={}",
                        if cfg.cursor.force { "on" } else { "off" }
                    );
                }
                self.state.reload_backend_from_config();
            }
            KeyCode::Char('j') if self.tab == Tab::Config => {
                {
                    let mut cfg = self.state.config.write();
                    cfg.cursor.json_mode = !cfg.cursor.json_mode;
                    let path = self.state.config_path.read().clone();
                    let _ = cfg.save(&path);
                    self.status_message = format!(
                        "json_mode={}",
                        if cfg.cursor.json_mode { "on" } else { "off" }
                    );
                }
                self.state.reload_backend_from_config();
            }
            KeyCode::Char('k') if self.tab == Tab::Config => {
                {
                    let mut cfg = self.state.config.write();
                    cfg.cursor.trust = !cfg.cursor.trust;
                    let path = self.state.config_path.read().clone();
                    let _ = cfg.save(&path);
                    self.status_message =
                        format!("trust={}", if cfg.cursor.trust { "on" } else { "off" });
                }
                self.state.reload_backend_from_config();
            }
            _ => {}
        }
    }
}

pub async fn run_tui(state: AppState, autostart: bool) -> Result<()> {
    // Classic cmd.exe often starts without VT processing — colors look monochrome.
    enable_windows_vt_processing();
    enable_raw_mode()?;
    let mut stdout = stdout();
    stdout.execute(EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = TuiApp::new(state);
    if autostart {
        match start_in_background(app.state.clone()).await {
            Ok(()) => {
                let cfg = app.state.config.read().clone();
                app.status_message =
                    crate::config::ready_banner(&cfg, app.state.agent_version.read().clone());
            }
            Err(err) => app.status_message = format!("Autostart failed: {err:#}"),
        }
    }

    let mut events = EventStream::new();
    let mut ticker = tokio::time::interval(Duration::from_millis(50));

    let result = loop {
        terminal.draw(|frame| {
            let area = frame.area();
            // Inner CLI size ≈ content area minus chrome.
            let cli_cols = area.width.saturating_sub(4).max(40);
            let cli_rows = area.height.saturating_sub(8).max(10);
            if app.tab.is_pty() {
                if app.cli_cols != cli_cols || app.cli_rows != cli_rows {
                    app.cli_cols = cli_cols;
                    app.cli_rows = cli_rows;
                    if let Some(cli) = app.cli.as_mut() {
                        cli.resize(cli_cols, cli_rows);
                    }
                    if let Some(agent) = app.agent.as_mut() {
                        agent.resize(cli_cols, cli_rows);
                    }
                }
            }
            super::ui::draw(frame, &app);
        })?;

        tokio::select! {
            maybe = events.next() => {
                match maybe {
                    Some(Ok(Event::Key(key))) => {
                        app.handle_key(key).await;
                        if app.should_quit {
                            break Ok(());
                        }
                    }
                    Some(Ok(Event::Resize(_, _))) => {}
                    Some(Err(err)) => break Err(err.into()),
                    None => break Ok(()),
                    _ => {}
                }
            }
            _ = ticker.tick() => {
                app.tick_cli();
            }
        }
    };

    if app.state.is_running() {
        let _ = stop_server(&app.state).await;
    }
    app.cli = None;
    app.agent = None;
    disable_raw_mode()?;
    terminal.backend_mut().execute(LeaveAlternateScreen)?;
    result
}

/// Enable ANSI/VT color sequences on Windows console hosts (cmd.exe / conhost).
/// Without this, Start-Process into classic Command Prompt often renders monochrome.
fn enable_windows_vt_processing() {
    #[cfg(windows)]
    {
        use std::ffi::c_void;

        #[link(name = "kernel32")]
        extern "system" {
            fn GetStdHandle(n_std_handle: u32) -> *mut c_void;
            fn GetConsoleMode(h_console_handle: *mut c_void, lp_mode: *mut u32) -> i32;
            fn SetConsoleMode(h_console_handle: *mut c_void, dw_mode: u32) -> i32;
        }

        const STD_OUTPUT_HANDLE: u32 = 0xFFFFFFF5; // (u32)-11
        const STD_ERROR_HANDLE: u32 = 0xFFFFFFF4; // (u32)-12
        const ENABLE_PROCESSED_OUTPUT: u32 = 0x0001;
        const ENABLE_VIRTUAL_TERMINAL_PROCESSING: u32 = 0x0004;
        const ENABLE_WRAP_AT_EOL_OUTPUT: u32 = 0x0002;

        unsafe {
            for handle_id in [STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
                let handle = GetStdHandle(handle_id);
                if handle.is_null() || handle == (-1isize as *mut c_void) {
                    continue;
                }
                let mut mode = 0u32;
                if GetConsoleMode(handle, &mut mode) == 0 {
                    continue;
                }
                let new_mode = mode
                    | ENABLE_PROCESSED_OUTPUT
                    | ENABLE_WRAP_AT_EOL_OUTPUT
                    | ENABLE_VIRTUAL_TERMINAL_PROCESSING;
                let _ = SetConsoleMode(handle, new_mode);
            }
        }
    }
}
