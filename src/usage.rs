use chrono::{DateTime, Local};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;

/// Rough estimate used when the Kiro CLI does not report billing tokens.
pub fn estimate_tokens(text: &str) -> u32 {
    let chars = text.chars().count() as u32;
    (chars / 4).max(1)
}

pub fn preview_text(text: &str, max_chars: usize) -> String {
    let trimmed = text.trim();
    if trimmed.chars().count() <= max_chars {
        return trimmed.to_string();
    }
    let mut out: String = trimmed.chars().take(max_chars).collect();
    out.push('…');
    out
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RequestRecord {
    pub id: String,
    pub request_id: String,
    pub user: Option<String>,
    pub started_at: DateTime<Local>,
    pub finished_at: Option<DateTime<Local>>,
    pub model: String,
    pub stream: bool,
    pub status: String,
    pub prompt_tokens: u32,
    pub completion_tokens: u32,
    pub duration_ms: u64,
    pub error: Option<String>,
    pub response_preview: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct UsageTotals {
    pub requests: u64,
    pub successes: u64,
    pub failures: u64,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub total_tokens: u64,
    pub avg_latency_ms: u64,
}

#[derive(Clone)]
pub struct UsageTracker {
    inner: Arc<Mutex<Inner>>,
}

struct Inner {
    totals: UsageTotals,
    recent: VecDeque<RequestRecord>,
    capacity: usize,
    active: usize,
    latency_sum_ms: u64,
    latency_count: u64,
}

impl UsageTracker {
    pub fn new(capacity: usize) -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner {
                totals: UsageTotals::default(),
                recent: VecDeque::with_capacity(capacity.min(500)),
                capacity: capacity.max(50),
                active: 0,
                latency_sum_ms: 0,
                latency_count: 0,
            })),
        }
    }

    pub fn begin(
        &self,
        id: String,
        request_id: String,
        user: Option<String>,
        model: String,
        stream: bool,
        prompt_tokens: u32,
    ) {
        let mut g = self.inner.lock();
        g.active += 1;
        g.totals.requests += 1;
        if g.recent.len() >= g.capacity {
            g.recent.pop_front();
        }
        g.recent.push_back(RequestRecord {
            id,
            request_id,
            user,
            started_at: Local::now(),
            finished_at: None,
            model,
            stream,
            status: "running".into(),
            prompt_tokens,
            completion_tokens: 0,
            duration_ms: 0,
            error: None,
            response_preview: None,
        });
    }

    pub fn finish_ok(
        &self,
        id: &str,
        completion_tokens: u32,
        duration_ms: u64,
        response_text: &str,
    ) {
        let mut g = self.inner.lock();
        g.active = g.active.saturating_sub(1);
        g.totals.successes += 1;
        let mut prompt_tokens = 0u32;
        if let Some(rec) = g.recent.iter_mut().rev().find(|r| r.id == id) {
            rec.finished_at = Some(Local::now());
            rec.status = "ok".into();
            rec.completion_tokens = completion_tokens;
            rec.duration_ms = duration_ms;
            rec.response_preview = Some(preview_text(response_text, 120));
            prompt_tokens = rec.prompt_tokens;
        }
        g.totals.prompt_tokens += prompt_tokens as u64;
        g.totals.completion_tokens += completion_tokens as u64;
        g.totals.total_tokens += (prompt_tokens + completion_tokens) as u64;
        g.latency_sum_ms += duration_ms;
        g.latency_count += 1;
        g.totals.avg_latency_ms = if g.latency_count > 0 {
            g.latency_sum_ms / g.latency_count
        } else {
            0
        };
    }

    pub fn finish_err(&self, id: &str, error: String, duration_ms: u64) {
        let mut g = self.inner.lock();
        g.active = g.active.saturating_sub(1);
        g.totals.failures += 1;
        let mut prompt_tokens = 0u32;
        if let Some(rec) = g.recent.iter_mut().rev().find(|r| r.id == id) {
            rec.finished_at = Some(Local::now());
            rec.status = "error".into();
            rec.duration_ms = duration_ms;
            rec.error = Some(error);
            prompt_tokens = rec.prompt_tokens;
        }
        g.totals.prompt_tokens += prompt_tokens as u64;
        g.totals.total_tokens += prompt_tokens as u64;
        g.latency_sum_ms += duration_ms;
        g.latency_count += 1;
        g.totals.avg_latency_ms = if g.latency_count > 0 {
            g.latency_sum_ms / g.latency_count
        } else {
            0
        };
    }

    pub fn totals(&self) -> UsageTotals {
        self.inner.lock().totals.clone()
    }

    pub fn recent(&self) -> Vec<RequestRecord> {
        self.inner.lock().recent.iter().cloned().rev().collect()
    }

    pub fn active(&self) -> usize {
        self.inner.lock().active
    }

    pub fn reset(&self) {
        let mut g = self.inner.lock();
        g.totals = UsageTotals::default();
        g.recent.clear();
        g.active = 0;
        g.latency_sum_ms = 0;
        g.latency_count = 0;
    }
}
