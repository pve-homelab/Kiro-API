use crate::config::Config;
use crate::cursor::CursorBackend;
use crate::log_buffer::LogBuffer;
use crate::usage::UsageTracker;
use chrono::{DateTime, Local};
use parking_lot::RwLock;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::sync::watch;
use tokio::task::JoinHandle;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<RwLock<Config>>,
    pub config_path: Arc<RwLock<std::path::PathBuf>>,
    pub logs: LogBuffer,
    pub usage: UsageTracker,
    pub backend: CursorBackend,
    pub server_running: Arc<AtomicBool>,
    pub server_healthy: Arc<AtomicBool>,
    pub agent_version: Arc<RwLock<Option<String>>>,
    pub stop_tx: Arc<RwLock<Option<watch::Sender<bool>>>>,
    pub server_handle: Arc<RwLock<Option<JoinHandle<()>>>>,
    pub started_at: Arc<RwLock<Option<DateTime<Local>>>>,
    pub last_error: Arc<RwLock<Option<String>>>,
}

impl AppState {
    pub fn new(config: Config, config_path: std::path::PathBuf) -> Self {
        let backend = CursorBackend::new(
            config.cursor.clone(),
            config.server.max_concurrency,
            config.server.reject_when_busy,
            config.server.queue_wait_secs,
        );
        let logs = LogBuffer::new(config.logging.ring_capacity, config.logging.redact_secrets);
        Self {
            config: Arc::new(RwLock::new(config)),
            config_path: Arc::new(RwLock::new(config_path)),
            logs,
            usage: UsageTracker::new(200),
            backend,
            server_running: Arc::new(AtomicBool::new(false)),
            server_healthy: Arc::new(AtomicBool::new(false)),
            agent_version: Arc::new(RwLock::new(None)),
            stop_tx: Arc::new(RwLock::new(None)),
            server_handle: Arc::new(RwLock::new(None)),
            started_at: Arc::new(RwLock::new(None)),
            last_error: Arc::new(RwLock::new(None)),
        }
    }

    pub fn is_running(&self) -> bool {
        self.server_running.load(Ordering::SeqCst)
    }

    pub fn is_healthy(&self) -> bool {
        self.server_healthy.load(Ordering::SeqCst)
    }

    pub fn uptime_secs(&self) -> u64 {
        self.started_at
            .read()
            .map(|t| (Local::now() - t).num_seconds().max(0) as u64)
            .unwrap_or(0)
    }

    pub fn set_last_error(&self, err: Option<String>) {
        *self.last_error.write() = err;
    }

    pub fn reload_backend_from_config(&self) {
        let cfg = self.config.read().clone();
        self.backend.update_config(
            cfg.cursor,
            cfg.server.max_concurrency,
            cfg.server.reject_when_busy,
            cfg.server.queue_wait_secs,
        );
    }
}
