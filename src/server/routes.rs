use crate::cursor::{ChatRequest, CompleteError, StreamEvent, StreamHandle, StreamStartError};
use crate::openai::{
    error_json, error_json_code, ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse,
    Choice, ChunkChoice, ChunkDelta, ModelCard, ModelsResponse, ResponseMessage, Usage,
};
use crate::prompt::{build_prompt, FlattenMode};
use crate::response::normalize_output;
use crate::state::AppState;
use crate::usage::estimate_tokens;
use axum::body::Body;
use axum::extract::State;
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::Utc;
use futures_util::stream::{Stream, StreamExt};
use std::convert::Infallible;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Instant;
use tokio_stream::wrappers::ReceiverStream;
use uuid::Uuid;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health", get(health))
        .route("/healthz", get(health))
        .route("/v1/models", get(list_models))
        .route("/v1/chat/completions", post(chat_completions))
}

async fn health(State(state): State<AppState>) -> impl IntoResponse {
    let running = state.is_running();
    let healthy = state.is_healthy();
    let agent = state.agent_version.read().clone();
    let cfg = state.config.read().clone();
    let last_error = state.last_error.read().clone();
    let body = serde_json::json!({
        "status": if healthy { "ok" } else { "degraded" },
        "server_running": running,
        "healthy": healthy,
        "uptime_secs": state.uptime_secs(),
        "base_url": cfg.base_url(),
        "v1_url": cfg.v1_url(),
        "config_path": state.config_path.read().display().to_string(),
        "agent_version": agent,
        "profile": cfg.cursor.profile,
        "default_model": cfg.cursor.default_model,
        "mode": cfg.cursor.mode,
        "json_mode": cfg.cursor.json_mode,
        "flatten_mode": cfg.cursor.message_flatten_mode,
        "max_context_tokens": cfg.cursor.max_context_tokens,
        "truncate_over_context": cfg.cursor.truncate_over_context,
        "max_concurrency": cfg.server.max_concurrency,
        "available_concurrency": state.backend.available_permits(),
        "reject_when_busy": cfg.server.reject_when_busy,
        "queue_wait_secs": cfg.server.queue_wait_secs,
        "request_timeout_secs": cfg.server.request_timeout_secs,
        "bind_host_source": state.bind_provenance.read().host_source,
        "bind_port_source": state.bind_provenance.read().port_source,
        "active_requests": state.usage.active(),
        "last_error": last_error,
        "usage": state.usage.totals(),
    });
    (StatusCode::OK, Json(body))
}

async fn list_models(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ApiError> {
    require_auth(&state, &headers)?;
    let default_model = state.config.read().cursor.default_model.clone();
    let created = Utc::now().timestamp();
    let mut ids = vec![
        default_model.clone(),
        "auto".into(),
        "composer-2.5".into(),
        "composer-2".into(),
        "gpt-5".into(),
        "claude-4.5-sonnet".into(),
        "claude-4-sonnet".into(),
    ];
    ids.sort();
    ids.dedup();
    let data = ids
        .into_iter()
        .map(|id| ModelCard {
            id,
            object: "model",
            created,
            owned_by: "cursor",
        })
        .collect();
    Ok(Json(ModelsResponse {
        object: "list",
        data,
    }))
}

async fn chat_completions(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<ChatCompletionRequest>,
) -> Result<Response, ApiError> {
    require_auth(&state, &headers)?;

    if req.messages.is_empty() {
        return Err(ApiError::bad_request("messages must not be empty"));
    }

    let request_id = request_id_from_headers(&headers);
    let cfg = state.config.read().clone();
    let model = req
        .model
        .clone()
        .unwrap_or_else(|| cfg.cursor.default_model.clone());

    let json_mode = cfg.cursor.json_mode
        || req
            .response_format
            .as_ref()
            .is_some_and(|rf| rf.wants_json());

    let flatten = FlattenMode::parse(&cfg.cursor.message_flatten_mode);
    let mut prompt = build_prompt(
        &req.messages,
        flatten,
        &cfg.cursor.prompt_prefix,
        &cfg.cursor.prompt_suffix,
        json_mode,
    );
    let mut prompt_tokens = estimate_tokens(&prompt);
    let max_ctx = cfg.cursor.max_context_tokens.max(1);
    if prompt_tokens > max_ctx {
        state.logs.warn(format!(
            "prompt tokens≈{prompt_tokens} exceed max_context_tokens={max_ctx} request_id={request_id} truncate={}",
            cfg.cursor.truncate_over_context
        ));
        tracing::warn!(
            target: "cursor_api",
            prompt_tokens,
            max_context_tokens = max_ctx,
            truncate = cfg.cursor.truncate_over_context,
            %request_id,
            "prompt exceeds max_context_tokens"
        );
        if cfg.cursor.truncate_over_context {
            // estimate_tokens ≈ chars/4; keep a small safety margin under the budget.
            let max_chars = (max_ctx as usize).saturating_mul(4).saturating_sub(16);
            let truncated: String = prompt.chars().take(max_chars).collect();
            prompt = truncated;
            prompt_tokens = estimate_tokens(&prompt);
            state.logs.warn(format!(
                "truncated prompt to ≈{prompt_tokens} tokens (max_context_tokens={max_ctx}) request_id={request_id}"
            ));
        }
    }
    let id = format!("chatcmpl-{}", Uuid::new_v4());
    let started = Instant::now();
    let user = req.user.clone();

    state.usage.begin(
        id.clone(),
        request_id.clone(),
        user,
        model.clone(),
        req.stream,
        prompt_tokens,
    );
    state.logs.info(format!(
        "POST /v1/chat/completions request_id={request_id} model={model} stream={} prompt_tokens≈{prompt_tokens} json_mode={json_mode}",
        req.stream
    ));

    let chat = ChatRequest {
        model: model.clone(),
        prompt,
        stream: req.stream,
    };

    if req.stream {
        match state
            .backend
            .stream(chat, cfg.server.request_timeout_secs)
            .await
        {
            Ok(handle) => Ok(stream_response(
                state,
                handle,
                id,
                request_id,
                model,
                prompt_tokens,
                json_mode,
                started,
            )),
            Err(StreamStartError::Busy(_)) => {
                let elapsed = started.elapsed().as_millis() as u64;
                let msg = "server busy: max concurrency reached";
                state.usage.finish_err(&id, msg.into(), elapsed);
                state.set_last_error(Some(msg.into()));
                state.logs.warn(format!("429 busy request_id={request_id}"));
                Err(ApiError::too_many_requests(msg))
            }
            Err(StreamStartError::Other(err)) => {
                let elapsed = started.elapsed().as_millis() as u64;
                let err_msg = format!("{err:#}");
                state.usage.finish_err(&id, err_msg.clone(), elapsed);
                state.set_last_error(Some(err_msg.clone()));
                state.logs.error(format!("stream start failed: {err_msg}"));
                Err(ApiError::server(err_msg))
            }
        }
    } else {
        match state
            .backend
            .complete(chat, cfg.server.request_timeout_secs)
            .await
        {
            Ok(result) => {
                let text = normalize_output(&result.text, json_mode);
                let completion_tokens = estimate_tokens(&text);
                let elapsed = if result.duration_ms > 0 {
                    result.duration_ms
                } else {
                    started.elapsed().as_millis() as u64
                };
                state.usage.finish_ok(&id, completion_tokens, elapsed, &text);
                state.set_last_error(None);
                state.logs.info(format!(
                    "completion ok request_id={request_id} completion_tokens≈{completion_tokens} {elapsed}ms"
                ));
                let body = ChatCompletionResponse {
                    id,
                    object: "chat.completion",
                    created: Utc::now().timestamp(),
                    model,
                    choices: vec![Choice {
                        index: 0,
                        message: ResponseMessage {
                            role: "assistant",
                            content: text,
                        },
                        finish_reason: "stop",
                    }],
                    usage: Usage {
                        prompt_tokens,
                        completion_tokens,
                        total_tokens: prompt_tokens + completion_tokens,
                    },
                };
                Ok(with_request_id(Json(body).into_response(), &request_id))
            }
            Err(CompleteError::Busy(_)) => {
                let elapsed = started.elapsed().as_millis() as u64;
                let msg = "server busy: max concurrency reached";
                state.usage.finish_err(&id, msg.into(), elapsed);
                state.set_last_error(Some(msg.into()));
                state.logs.warn(format!("429 busy request_id={request_id}"));
                Err(ApiError::too_many_requests(msg))
            }
            Err(CompleteError::Other(err)) => {
                let elapsed = started.elapsed().as_millis() as u64;
                let err_msg = format!("{err:#}");
                state.usage.finish_err(&id, err_msg.clone(), elapsed);
                state.set_last_error(Some(err_msg.clone()));
                state.logs.error(format!("completion failed: {err_msg}"));
                Err(ApiError::server(err_msg))
            }
        }
    }
}

fn stream_response(
    state: AppState,
    handle: StreamHandle,
    id: String,
    request_id: String,
    model: String,
    prompt_tokens: u32,
    json_mode: bool,
    started: Instant,
) -> Response {
    let cancel = handle.cancel_sender();
    let state2 = state.clone();
    let id2 = id.clone();
    let request_id2 = request_id.clone();
    let model2 = model.clone();
    let created = Utc::now().timestamp();

    let stream = ReceiverStream::new(handle.rx).map(move |event| {
        let payload = match event {
            StreamEvent::Delta(text) => {
                let chunk = ChatCompletionChunk {
                    id: id2.clone(),
                    object: "chat.completion.chunk",
                    created,
                    model: model2.clone(),
                    choices: vec![ChunkChoice {
                        index: 0,
                        delta: ChunkDelta {
                            role: None,
                            content: Some(text),
                        },
                        finish_reason: None,
                    }],
                    usage: None,
                };
                format!("data: {}\n\n", serde_json::to_string(&chunk).unwrap())
            }
            StreamEvent::Done {
                text,
                duration_ms,
                ..
            } => {
                let normalized = normalize_output(&text, json_mode);
                let completion_tokens = estimate_tokens(&normalized);
                let elapsed = if duration_ms > 0 {
                    duration_ms
                } else {
                    started.elapsed().as_millis() as u64
                };
                state2.usage.finish_ok(&id2, completion_tokens, elapsed, &normalized);
                state2.set_last_error(None);
                state2.logs.info(format!(
                    "stream completed request_id={request_id2} completion_tokens≈{completion_tokens} {elapsed}ms"
                ));
                let finish = ChatCompletionChunk {
                    id: id2.clone(),
                    object: "chat.completion.chunk",
                    created,
                    model: model2.clone(),
                    choices: vec![ChunkChoice {
                        index: 0,
                        delta: ChunkDelta {
                            role: None,
                            content: None,
                        },
                        finish_reason: Some("stop"),
                    }],
                    usage: None,
                };
                let usage_chunk = ChatCompletionChunk {
                    id: id2.clone(),
                    object: "chat.completion.chunk",
                    created,
                    model: model2.clone(),
                    choices: vec![],
                    usage: Some(Usage {
                        prompt_tokens,
                        completion_tokens,
                        total_tokens: prompt_tokens + completion_tokens,
                    }),
                };
                format!(
                    "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
                    serde_json::to_string(&finish).unwrap(),
                    serde_json::to_string(&usage_chunk).unwrap()
                )
            }
            StreamEvent::Error(err) => {
                let elapsed = started.elapsed().as_millis() as u64;
                state2.usage.finish_err(&id2, err.clone(), elapsed);
                state2.set_last_error(Some(err.clone()));
                state2.logs.error(format!("stream error request_id={request_id2}: {err}"));
                let body = error_json(err, "server_error");
                format!(
                    "data: {}\n\ndata: [DONE]\n\n",
                    serde_json::to_string(&body).unwrap()
                )
            }
        };
        Ok::<_, Infallible>(payload)
    });

    let first = ChatCompletionChunk {
        id: id.clone(),
        object: "chat.completion.chunk",
        created: Utc::now().timestamp(),
        model: model.clone(),
        choices: vec![ChunkChoice {
            index: 0,
            delta: ChunkDelta {
                role: Some("assistant"),
                content: Some(String::new()),
            },
            finish_reason: None,
        }],
        usage: None,
    };
    let first_line = format!("data: {}\n\n", serde_json::to_string(&first).unwrap());
    let inner = futures_util::stream::once(async move { Ok::<_, Infallible>(first_line) })
        .chain(stream)
        .boxed();
    let body_stream = CancelOnDropStream { inner, cancel };

    let mut response = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header(header::CONNECTION, "keep-alive")
        .body(Body::from_stream(body_stream))
        .unwrap();
    if let Ok(val) = HeaderValue::from_str(&request_id) {
        response.headers_mut().insert("x-request-id", val);
    }
    response
}

fn request_id_from_headers(headers: &HeaderMap) -> String {
    headers
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("req-{}", Uuid::new_v4()))
}

fn with_request_id(mut response: Response, request_id: &str) -> Response {
    if let Ok(val) = HeaderValue::from_str(request_id) {
        response.headers_mut().insert("x-request-id", val);
    }
    response
}

fn require_auth(state: &AppState, headers: &HeaderMap) -> Result<(), ApiError> {
    let cfg = state.config.read().clone();
    if !cfg.auth.require_auth && cfg.auth.api_key.is_empty() {
        return Ok(());
    }
    let expected = cfg.auth.api_key;
    if expected.is_empty() {
        return Ok(());
    }
    let auth = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let token = auth
        .strip_prefix("Bearer ")
        .or_else(|| auth.strip_prefix("bearer "))
        .unwrap_or(auth);
    if token == expected {
        Ok(())
    } else {
        Err(ApiError {
            status: StatusCode::UNAUTHORIZED,
            body: error_json("invalid api key", "invalid_request_error"),
        })
    }
}

pub struct ApiError {
    status: StatusCode,
    body: crate::openai::ErrorBody,
}

impl ApiError {
    fn bad_request(msg: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            body: error_json(msg, "invalid_request_error"),
        }
    }

    fn server(msg: impl Into<String>) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            body: error_json(msg, "server_error"),
        }
    }

    fn too_many_requests(msg: impl Into<String>) -> Self {
        Self {
            status: StatusCode::TOO_MANY_REQUESTS,
            body: error_json_code(msg, "rate_limit_error", "server_busy"),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(self.body)).into_response()
    }
}

struct CancelOnDropStream {
    inner: std::pin::Pin<
        Box<dyn Stream<Item = Result<String, Infallible>> + Send>,
    >,
    cancel: tokio::sync::watch::Sender<bool>,
}

impl Stream for CancelOnDropStream {
    type Item = Result<String, Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.inner.as_mut().poll_next(cx)
    }
}

impl Drop for CancelOnDropStream {
    fn drop(&mut self) {
        let _ = self.cancel.send(true);
    }
}
