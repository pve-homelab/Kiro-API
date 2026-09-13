use crate::config::redact_secrets;
use chrono::{DateTime, Local};
use parking_lot::Mutex;
use std::collections::VecDeque;
use std::sync::Arc;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogLevel {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
}

impl LogLevel {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Trace => "TRACE",
            Self::Debug => "DEBUG",
            Self::Info => "INFO",
            Self::Warn => "WARN",
            Self::Error => "ERROR",
        }
    }
}

#[derive(Debug, Clone)]
pub struct LogEntry {
    pub time: DateTime<Local>,
    pub level: LogLevel,
    pub message: String,
}

#[derive(Clone)]
pub struct LogBuffer {
    inner: Arc<Mutex<Inner>>,
}

struct Inner {
    entries: VecDeque<LogEntry>,
    capacity: usize,
    redact_secrets: bool,
}

impl LogBuffer {
    pub fn new(capacity: usize, redact_secrets_enabled: bool) -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner {
                entries: VecDeque::with_capacity(capacity.min(4096)),
                capacity: capacity.max(100),
                redact_secrets: redact_secrets_enabled,
            })),
        }
    }

    pub fn push(&self, level: LogLevel, message: impl Into<String>) {
        let mut guard = self.inner.lock();
        if guard.entries.len() >= guard.capacity {
            guard.entries.pop_front();
        }
        let raw = message.into();
        let message = redact_secrets(&raw, guard.redact_secrets);
        guard.entries.push_back(LogEntry {
            time: Local::now(),
            level,
            message,
        });
    }

    pub fn info(&self, message: impl Into<String>) {
        self.push(LogLevel::Info, message);
    }

    pub fn warn(&self, message: impl Into<String>) {
        self.push(LogLevel::Warn, message);
    }

    pub fn error(&self, message: impl Into<String>) {
        self.push(LogLevel::Error, message);
    }

    pub fn debug(&self, message: impl Into<String>) {
        self.push(LogLevel::Debug, message);
    }

    pub fn snapshot(&self) -> Vec<LogEntry> {
        self.inner.lock().entries.iter().cloned().collect()
    }

    pub fn clear(&self) {
        self.inner.lock().entries.clear();
    }

    pub fn len(&self) -> usize {
        self.inner.lock().entries.len()
    }
}
