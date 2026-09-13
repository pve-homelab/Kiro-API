//! Normalize-first ingest hub: adapters → IngestRequest → Kiro runner.

mod adapter;
mod builtin;
mod external;

pub use adapter::{AdapterInfo, AdapterRegistry, IngestAdapter, RouteSpec};
pub use builtin::register_builtins;
pub use external::load_external_adapters;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Canonical job produced by every adapter (built-in or external).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestRequest {
    pub model: String,
    pub prompt: String,
    #[serde(default)]
    pub stream: bool,
    #[serde(default)]
    pub json_mode: bool,
    #[serde(default)]
    pub meta: Value,
}

impl IngestRequest {
    pub fn new(model: impl Into<String>, prompt: impl Into<String>) -> Self {
        Self {
            model: model.into(),
            prompt: prompt.into(),
            stream: false,
            json_mode: false,
            meta: Value::Null,
        }
    }
}

/// Soft context budget check. Returns possibly truncated prompt.
pub fn apply_context_budget(
    prompt: String,
    prompt_tokens: u32,
    max_context_tokens: u32,
    truncate: bool,
) -> (String, bool) {
    if max_context_tokens == 0 || prompt_tokens <= max_context_tokens {
        return (prompt, false);
    }
    tracing::warn!(
        target: "ingest",
        "prompt_tokens≈{prompt_tokens} exceeds max_context_tokens={max_context_tokens}"
    );
    if !truncate {
        return (prompt, false);
    }
    // Rough char budget (~4 chars/token) keeping the tail (usually the user ask).
    let char_budget = (max_context_tokens as usize).saturating_mul(4);
    if prompt.len() <= char_budget {
        return (prompt, false);
    }
    let start = prompt.len() - char_budget;
    let truncated = format!(
        "[truncated to max_context_tokens={max_context_tokens}]\n\n{}",
        &prompt[start..]
    );
    (truncated, true)
}
