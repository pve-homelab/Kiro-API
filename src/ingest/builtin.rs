//! Built-in ingest adapters (scaffold — wire into router in a follow-up).

use crate::ingest::adapter::{IngestAdapter, RouteSpec};
use crate::ingest::{AdapterRegistry, IngestRequest};
use anyhow::{bail, Context, Result};
use axum::http::Method;
use serde_json::Value;
use std::sync::Arc;

pub fn register_builtins(registry: &mut AdapterRegistry) {
    registry.register(Arc::new(OpenaiChatAdapter));
    registry.register(Arc::new(RawIngestAdapter));
    registry.register(Arc::new(OpenaiCompletionsAdapter));
    registry.register(Arc::new(AnthropicMessagesAdapter));
}

struct OpenaiChatAdapter;
struct RawIngestAdapter;
struct OpenaiCompletionsAdapter;
struct AnthropicMessagesAdapter;

impl IngestAdapter for OpenaiChatAdapter {
    fn id(&self) -> &'static str {
        "openai_chat"
    }
    fn routes(&self) -> &[RouteSpec] {
        static R: &[RouteSpec] = &[RouteSpec {
            method: Method::POST,
            path: "/v1/chat/completions",
        }];
        R
    }
    fn ingest(&self, _method: &Method, _path: &str, body: &[u8], _headers: &Value) -> Result<IngestRequest> {
        let v: Value = serde_json::from_slice(body).context("openai_chat JSON")?;
        let model = v
            .get("model")
            .and_then(|m| m.as_str())
            .unwrap_or("auto")
            .to_string();
        let stream = v.get("stream").and_then(|s| s.as_bool()).unwrap_or(false);
        let json_mode = v
            .pointer("/response_format/type")
            .and_then(|t| t.as_str())
            .is_some_and(|t| t.contains("json"));
        let prompt = if let Some(messages) = v.get("messages").and_then(|m| m.as_array()) {
            let mut parts = Vec::new();
            for msg in messages {
                let role = msg.get("role").and_then(|r| r.as_str()).unwrap_or("user");
                let content = message_content_text(msg.get("content"));
                if !content.is_empty() {
                    parts.push(format!("{role}:\n{content}"));
                }
            }
            parts.join("\n\n")
        } else if let Some(p) = v.get("prompt").and_then(|p| p.as_str()) {
            p.to_string()
        } else if let Some(p) = v.get("input").and_then(|p| p.as_str()) {
            p.to_string()
        } else {
            bail!("openai_chat: need messages, prompt, or input");
        };
        Ok(IngestRequest {
            model,
            prompt,
            stream,
            json_mode,
            meta: serde_json::json!({"adapter": "openai_chat"}),
        })
    }
}

impl IngestAdapter for RawIngestAdapter {
    fn id(&self) -> &'static str {
        "raw_ingest"
    }
    fn routes(&self) -> &[RouteSpec] {
        static R: &[RouteSpec] = &[RouteSpec {
            method: Method::POST,
            path: "/v1/ingest",
        }];
        R
    }
    fn ingest(&self, _method: &Method, _path: &str, body: &[u8], _headers: &Value) -> Result<IngestRequest> {
        if let Ok(v) = serde_json::from_slice::<Value>(body) {
            let model = v
                .get("model")
                .and_then(|m| m.as_str())
                .unwrap_or("auto")
                .to_string();
            let stream = v.get("stream").and_then(|s| s.as_bool()).unwrap_or(false);
            let prompt = v
                .get("prompt")
                .or_else(|| v.get("text"))
                .or_else(|| v.get("input"))
                .and_then(|p| p.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| String::from_utf8_lossy(body).into_owned());
            return Ok(IngestRequest {
                model,
                prompt,
                stream,
                json_mode: false,
                meta: serde_json::json!({"adapter": "raw_ingest"}),
            });
        }
        Ok(IngestRequest {
            model: "auto".into(),
            prompt: String::from_utf8_lossy(body).into_owned(),
            stream: false,
            json_mode: false,
            meta: serde_json::json!({"adapter": "raw_ingest", "raw": true}),
        })
    }
}

impl IngestAdapter for OpenaiCompletionsAdapter {
    fn id(&self) -> &'static str {
        "openai_completions"
    }
    fn routes(&self) -> &[RouteSpec] {
        static R: &[RouteSpec] = &[RouteSpec {
            method: Method::POST,
            path: "/v1/completions",
        }];
        R
    }
    fn ingest(&self, _method: &Method, _path: &str, body: &[u8], _headers: &Value) -> Result<IngestRequest> {
        let v: Value = serde_json::from_slice(body).context("completions JSON")?;
        let model = v
            .get("model")
            .and_then(|m| m.as_str())
            .unwrap_or("auto")
            .to_string();
        let prompt = match v.get("prompt") {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Array(arr)) => arr
                .iter()
                .filter_map(|x| x.as_str())
                .collect::<Vec<_>>()
                .join("\n"),
            _ => bail!("completions: prompt required"),
        };
        Ok(IngestRequest {
            model,
            prompt,
            stream: v.get("stream").and_then(|s| s.as_bool()).unwrap_or(false),
            json_mode: false,
            meta: serde_json::json!({"adapter": "openai_completions"}),
        })
    }
}

impl IngestAdapter for AnthropicMessagesAdapter {
    fn id(&self) -> &'static str {
        "anthropic_messages"
    }
    fn routes(&self) -> &[RouteSpec] {
        static R: &[RouteSpec] = &[RouteSpec {
            method: Method::POST,
            path: "/v1/messages",
        }];
        R
    }
    fn ingest(&self, _method: &Method, _path: &str, body: &[u8], _headers: &Value) -> Result<IngestRequest> {
        let v: Value = serde_json::from_slice(body).context("anthropic JSON")?;
        let model = v
            .get("model")
            .and_then(|m| m.as_str())
            .unwrap_or("auto")
            .to_string();
        let mut parts = Vec::new();
        if let Some(sys) = v.get("system").and_then(|s| s.as_str()) {
            parts.push(format!("system:\n{sys}"));
        }
        if let Some(messages) = v.get("messages").and_then(|m| m.as_array()) {
            for msg in messages {
                let role = msg.get("role").and_then(|r| r.as_str()).unwrap_or("user");
                let content = message_content_text(msg.get("content"));
                if !content.is_empty() {
                    parts.push(format!("{role}:\n{content}"));
                }
            }
        }
        if parts.is_empty() {
            bail!("anthropic_messages: empty body");
        }
        Ok(IngestRequest {
            model,
            prompt: parts.join("\n\n"),
            stream: v.get("stream").and_then(|s| s.as_bool()).unwrap_or(false),
            json_mode: false,
            meta: serde_json::json!({"adapter": "anthropic_messages"}),
        })
    }
}

fn message_content_text(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(parts)) => parts
            .iter()
            .filter_map(|p| {
                p.get("text")
                    .and_then(|t| t.as_str())
                    .or_else(|| p.as_str())
            })
            .collect::<Vec<_>>()
            .join(""),
        _ => String::new(),
    }
}
