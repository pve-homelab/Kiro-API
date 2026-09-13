use crate::config::CursorConfig;
use anyhow::{anyhow, bail, Context, Result};
use serde::Deserialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{mpsc, watch, OwnedSemaphorePermit, Semaphore};
use tokio::time::{timeout, Duration};

#[derive(Debug, Clone)]
pub struct ChatRequest {
    pub model: String,
    pub prompt: String,
    #[allow(dead_code)]
    pub stream: bool,
}

#[derive(Debug, Clone)]
pub enum StreamEvent {
    Delta(String),
    Done {
        text: String,
        #[allow(dead_code)]
        session_id: Option<String>,
        duration_ms: u64,
    },
    Error(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConcurrencyPolicy {
    pub max_concurrency: usize,
    pub reject_when_busy: bool,
    pub queue_wait_secs: u64,
}

#[derive(Debug)]
pub struct StreamHandle {
    pub rx: mpsc::Receiver<StreamEvent>,
    cancel: watch::Sender<bool>,
}

impl StreamHandle {
    pub fn cancel(&self) {
        let _ = self.cancel.send(true);
    }

    pub fn cancel_sender(&self) -> watch::Sender<bool> {
        self.cancel.clone()
    }
}

#[derive(Debug, Clone, thiserror::Error)]
#[error("server busy: all concurrency slots in use")]
pub struct BusyError;

#[derive(Debug, thiserror::Error)]
pub enum StreamStartError {
    #[error("server busy: all concurrency slots in use")]
    Busy(#[from] BusyError),
    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

#[derive(Debug, thiserror::Error)]
pub enum CompleteError {
    #[error("server busy: all concurrency slots in use")]
    Busy(#[from] BusyError),
    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

#[derive(Clone)]
pub struct CursorBackend {
    config: Arc<parking_lot::RwLock<CursorConfig>>,
    policy: Arc<parking_lot::RwLock<ConcurrencyPolicy>>,
    semaphore: Arc<Semaphore>,
}

impl CursorBackend {
    pub fn new(
        config: CursorConfig,
        max_concurrency: usize,
        reject_when_busy: bool,
        queue_wait_secs: u64,
    ) -> Self {
        let policy = ConcurrencyPolicy {
            max_concurrency: max_concurrency.max(1),
            reject_when_busy,
            queue_wait_secs,
        };
        Self {
            config: Arc::new(parking_lot::RwLock::new(config)),
            policy: Arc::new(parking_lot::RwLock::new(policy)),
            semaphore: Arc::new(Semaphore::new(policy.max_concurrency)),
        }
    }

    pub fn update_config(
        &self,
        config: CursorConfig,
        max_concurrency: usize,
        reject_when_busy: bool,
        queue_wait_secs: u64,
    ) {
        *self.config.write() = config;
        let new_policy = ConcurrencyPolicy {
            max_concurrency: max_concurrency.max(1),
            reject_when_busy,
            queue_wait_secs,
        };
        let old_max = self.policy.read().max_concurrency;
        *self.policy.write() = new_policy;
        if new_policy.max_concurrency != old_max {
            // Semaphore capacity is fixed at creation; log if changed at runtime.
            tracing::warn!(
                "max_concurrency changed from {old_max} to {} — restart server to apply",
                new_policy.max_concurrency
            );
        }
    }

    pub fn resolve_binary(&self) -> Result<PathBuf> {
        Ok(self.resolve_launch()?.program.clone())
    }

    pub fn resolve_launch(&self) -> Result<AgentLaunch> {
        let cfg = self.config.read().clone();
        resolve_agent_launch(&cfg.binary)
    }

    /// Remaining semaphore permits (slots free for new /v1 jobs).
    pub fn available_permits(&self) -> usize {
        self.semaphore.available_permits()
    }

    pub fn max_concurrency(&self) -> usize {
        self.policy.read().max_concurrency
    }

    async fn acquire_permit(&self) -> Result<OwnedSemaphorePermit, BusyError> {
        let policy = *self.policy.read();
        if policy.reject_when_busy {
            if policy.queue_wait_secs == 0 {
                return self
                    .semaphore
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| BusyError);
            }
            match timeout(
                Duration::from_secs(policy.queue_wait_secs),
                self.semaphore.clone().acquire_owned(),
            )
            .await
            {
                Ok(Ok(permit)) => Ok(permit),
                Ok(Err(_)) => Err(BusyError),
                Err(_) => Err(BusyError),
            }
        } else {
            self.semaphore
                .clone()
                .acquire_owned()
                .await
                .map_err(|_| BusyError)
        }
    }

    pub async fn complete(&self, req: ChatRequest, timeout_secs: u64) -> Result<CompleteResult, CompleteError> {
        let _permit = self.acquire_permit().await?;

        let cfg = self.config.read().clone();
        let launch = resolve_agent_launch(&cfg.binary)?;
        // Prefer stdin for the full prompt (Kiro supports it; avoids Windows argv limits).
        let mut cmd = build_command(&launch, &cfg, &req.model, None, false)?;
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = cmd.spawn().with_context(|| {
            format!("failed to spawn kiro-cli ({})", launch.display)
        }).map_err(CompleteError::Other)?;

        if let Some(mut stdin) = child.stdin.take() {
            use tokio::io::AsyncWriteExt;
            stdin
                .write_all(req.prompt.as_bytes())
                .await
                .context("write prompt to kiro stdin")
                .map_err(CompleteError::Other)?;
            stdin
                .shutdown()
                .await
                .context("close kiro stdin")
                .map_err(CompleteError::Other)?;
        }

        // Capture pid before wait_with_output moves the Child — needed to kill the
        // Windows process tree if the timeout cancels the wait future.
        let pid = child.id();
        let output = match timeout(Duration::from_secs(timeout_secs), child.wait_with_output()).await
        {
            Ok(Ok(output)) => output,
            Ok(Err(err)) => {
                return Err(CompleteError::Other(anyhow!("wait for kiro-cli: {err}")));
            }
            Err(_) => {
                kill_process_tree(pid).await;
                return Err(CompleteError::Other(anyhow!("kiro-cli timed out")));
            }
        };

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let stdout = String::from_utf8_lossy(&output.stdout);
            return Err(CompleteError::Other(anyhow!(
                "kiro-cli failed ({}): {} {}",
                output.status,
                stderr.trim(),
                stdout.trim()
            )));
        }

        let stdout = String::from_utf8_lossy(&output.stdout);
        parse_kiro_result(&stdout).map_err(CompleteError::Other)
    }

    pub async fn stream(
        &self,
        req: ChatRequest,
        timeout_secs: u64,
    ) -> Result<StreamHandle, StreamStartError> {
        let permit = self.acquire_permit().await?;

        let cfg = self.config.read().clone();
        let launch = resolve_agent_launch(&cfg.binary)?;
        // Streaming: pass a short path-pointer prompt if huge; else inline.
        let staged = stage_prompt(&req.prompt, &cfg.workspace)?;
        let mut cmd = build_command(&launch, &cfg, &req.model, Some(&staged.cli_prompt), true)?;
        let mut child = cmd
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .with_context(|| {
                format!(
                    "failed to spawn kiro-cli for streaming ({})",
                    launch.display
                )
            })?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| anyhow!("missing kiro stdout"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| anyhow!("missing kiro stderr"))?;

        let (tx, rx) = mpsc::channel(64);
        let (cancel_tx, cancel_rx) = watch::channel(false);

        tokio::spawn(async move {
            let _permit = permit;
            let _staged = staged;
            let result =
                stream_child(child, stdout, stderr, timeout_secs, cancel_rx, tx.clone()).await;
            if let Err(err) = result {
                let _ = tx.send(StreamEvent::Error(err.to_string())).await;
            }
        });

        Ok(StreamHandle { rx, cancel: cancel_tx })
    }
}

#[derive(Debug, Clone)]
pub struct CompleteResult {
    pub text: String,
    #[allow(dead_code)]
    pub session_id: Option<String>,
    pub duration_ms: u64,
}

/// How to invoke the Cursor agent CLI (wrapper script or node + index.js).
#[derive(Debug, Clone)]
pub struct AgentLaunch {
    pub program: PathBuf,
    pub prefix_args: Vec<String>,
    pub display: String,
}

/// Windows CreateProcess command-line limit is ~32 767 chars and surfaces as
/// `os error 206` ("filename or extension is too long") when a large prompt
/// is passed as a single argv. Keep a conservative headroom for flags/paths.
#[cfg(windows)]
const INLINE_PROMPT_MAX_CHARS: usize = 4_000;
#[cfg(not(windows))]
const INLINE_PROMPT_MAX_CHARS: usize = 100_000;

struct StagedPrompt {
    cli_prompt: String,
    temp_path: Option<PathBuf>,
}

impl Drop for StagedPrompt {
    fn drop(&mut self) {
        if let Some(path) = self.temp_path.take() {
            let _ = std::fs::remove_file(&path);
        }
    }
}

fn stage_prompt(prompt: &str, workspace: &str) -> Result<StagedPrompt> {
    if prompt.len() <= INLINE_PROMPT_MAX_CHARS {
        return Ok(StagedPrompt {
            cli_prompt: prompt.to_string(),
            temp_path: None,
        });
    }

    let dir = if !workspace.is_empty() {
        PathBuf::from(workspace).join(".kiro-api-prompts")
    } else {
        std::env::temp_dir().join("kiro-api-prompts")
    };
    std::fs::create_dir_all(&dir)
        .with_context(|| format!("create prompt staging dir {}", dir.display()))?;

    let path = dir.join(format!("prompt-{}.txt", uuid::Uuid::new_v4()));
    std::fs::write(&path, prompt)
        .with_context(|| format!("write staged prompt {}", path.display()))?;

    let path_display = path.display().to_string();
    tracing::info!(
        target: "cursor_cli",
        "prompt {} chars exceeds inline limit {}; staged at {}",
        prompt.len(),
        INLINE_PROMPT_MAX_CHARS,
        path_display
    );

    let cli_prompt = format!(
        "Read the UTF-8 text file at this exact path and follow its instructions completely as your sole task. \
Do not ask clarifying questions. Reply with only the final answer required by that file.\n\nPath: {path_display}"
    );

    Ok(StagedPrompt {
        cli_prompt,
        temp_path: Some(path),
    })
}

fn build_command(
    launch: &AgentLaunch,
    cfg: &CursorConfig,
    model: &str,
    cli_prompt: Option<&str>,
    stream: bool,
) -> Result<Command> {
    let mut cmd = Command::new(&launch.program);
    for arg in &launch.prefix_args {
        cmd.arg(arg);
    }
    cmd.arg("chat");
    cmd.arg("--no-interactive");

    if cfg.force {
        cmd.arg("--trust-all-tools");
    } else {
        // No tools — Q&A style (closest to Cursor ask mode).
        cmd.arg("--trust-tools=");
    }

    let model = if model.is_empty() || model == "default" || model == "auto" {
        cfg.default_model.clone()
    } else {
        model.to_string()
    };
    if !model.is_empty() && model != "auto" {
        cmd.arg("--model").arg(&model);
    }

    if stream {
        cmd.arg("--v3");
        cmd.arg("--output-format").arg("stream-json");
    }

    if !cfg.workspace.is_empty() {
        cmd.current_dir(&cfg.workspace);
    }

    for arg in &cfg.extra_args {
        cmd.arg(arg);
    }

    if !cfg.cursor_api_key.is_empty() {
        cmd.env("KIRO_API_KEY", &cfg.cursor_api_key);
    }

    // When Some, pass as positional prompt; when None, caller pipes stdin.
    if let Some(prompt) = cli_prompt {
        cmd.arg(prompt);
    }

    cmd.kill_on_drop(true);
    // On Windows, put the CLI in a new process group so we can kill the whole tree
    // (kiro-cli often spawns node / helper children that would otherwise leak permits).
    #[cfg(windows)]
    {
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        cmd.creation_flags(CREATE_NEW_PROCESS_GROUP);
    }
    Ok(cmd)
}

/// Kill a child and any descendants. Critical on Windows where `Child::kill` only
/// signals the root process and orphans can hold work (and semaphore permits) forever.
async fn kill_child_tree(child: &mut Child) {
    let pid = child.id();
    kill_process_tree(pid).await;
    let _ = child.kill().await;
    let _ = child.wait().await;
}

async fn kill_process_tree(pid: Option<u32>) {
    let Some(pid) = pid else {
        return;
    };
    #[cfg(windows)]
    {
        let mut kill = Command::new("taskkill");
        kill.args(["/PID", &pid.to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let _ = kill.status().await;
    }
    #[cfg(unix)]
    {
        // Best-effort: signal the process group leader if the CLI forked helpers.
        let mut kill = Command::new("kill");
        kill.args(["-KILL", &format!("-{pid}")])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if kill.status().await.is_err() {
            let mut kill = Command::new("kill");
            kill.args(["-KILL", &pid.to_string()])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            let _ = kill.status().await;
        }
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = pid;
    }
}

async fn stream_child(
    mut child: Child,
    stdout: impl tokio::io::AsyncRead + Unpin,
    stderr: impl tokio::io::AsyncRead + Unpin,
    timeout_secs: u64,
    mut cancel_rx: watch::Receiver<bool>,
    tx: mpsc::Sender<StreamEvent>,
) -> Result<()> {
    let mut stdout_lines = BufReader::new(stdout).lines();
    let mut stderr_lines = BufReader::new(stderr).lines();
    let mut assembled = String::new();
    let mut session_id = None;
    let mut duration_ms = 0u64;
    let mut saw_result = false;

    let work = async {
        let mut stderr_done = false;
        loop {
            if *cancel_rx.borrow() {
                kill_child_tree(&mut child).await;
                bail!("client disconnected");
            }
            tokio::select! {
                changed = cancel_rx.changed() => {
                    if changed.is_ok() && *cancel_rx.borrow() {
                        kill_child_tree(&mut child).await;
                        bail!("client disconnected");
                    }
                }
                line = stdout_lines.next_line() => {
                    match line? {
                        Some(line) => {
                            if line.trim().is_empty() {
                                continue;
                            }
                            match handle_stream_line(&line, &mut assembled, &mut session_id, &mut duration_ms, &mut saw_result) {
                                Ok(Some(delta)) => {
                                    if tx.send(StreamEvent::Delta(delta)).await.is_err() {
                                        kill_child_tree(&mut child).await;
                                        bail!("client disconnected");
                                    }
                                }
                                Ok(None) => {}
                                Err(err) => {
                                    let _ = tx.send(StreamEvent::Error(err.to_string())).await;
                                    break;
                                }
                            }
                        }
                        None => break,
                    }
                }
                line = stderr_lines.next_line(), if !stderr_done => {
                    match line {
                        Ok(Some(line)) => {
                            tracing::debug!(target: "cursor_cli", "stderr: {line}");
                        }
                        _ => {
                            stderr_done = true;
                        }
                    }
                }
            }
        }
        Ok::<(), anyhow::Error>(())
    };

    match timeout(Duration::from_secs(timeout_secs), work).await {
        Ok(Ok(())) => {}
        Ok(Err(err)) => return Err(err),
        Err(_) => {
            kill_child_tree(&mut child).await;
            bail!("cursor agent stream timed out");
        }
    }

    let status = child.wait().await.context("wait for agent")?;
    if !status.success() && !saw_result {
        bail!("cursor agent exited with {status}");
    }

    let _ = tx
        .send(StreamEvent::Done {
            text: assembled,
            session_id,
            duration_ms,
        })
        .await;
    Ok(())
}

fn handle_stream_line(
    line: &str,
    assembled: &mut String,
    session_id: &mut Option<String>,
    duration_ms: &mut u64,
    saw_result: &mut bool,
) -> Result<Option<String>> {
    let value: Value = serde_json::from_str(line).context("invalid stream-json line")?;
    let ty = value.get("type").and_then(|v| v.as_str()).unwrap_or("");

    if let Some(sid) = value.get("session_id").and_then(|v| v.as_str()) {
        *session_id = Some(sid.to_string());
    }

    match ty {
        "assistant" => {
            let has_ts = value.get("timestamp_ms").is_some();
            let has_model_call = value.get("model_call_id").is_some();
            if has_ts && !has_model_call {
                if let Some(text) = extract_assistant_text(&value) {
                    assembled.push_str(&text);
                    return Ok(Some(text));
                }
            }
            Ok(None)
        }
        "result" => {
            *saw_result = true;
            if let Some(ms) = value.get("duration_ms").and_then(|v| v.as_u64()) {
                *duration_ms = ms;
            }
            if assembled.is_empty() {
                if let Some(text) = value.get("result").and_then(|v| v.as_str()) {
                    assembled.push_str(text);
                }
            }
            Ok(None)
        }
        _ => Ok(None),
    }
}

fn extract_assistant_text(value: &Value) -> Option<String> {
    let content = value
        .pointer("/message/content")
        .and_then(|v| v.as_array())?;
    let mut out = String::new();
    for part in content {
        if part.get("type").and_then(|v| v.as_str()) == Some("text") {
            if let Some(t) = part.get("text").and_then(|v| v.as_str()) {
                out.push_str(t);
            }
        }
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

#[derive(Debug, Deserialize)]
struct JsonResult {
    result: Option<String>,
    session_id: Option<String>,
    duration_ms: Option<u64>,
    is_error: Option<bool>,
    #[serde(default)]
    #[allow(dead_code)]
    subtype: Option<String>,
}

fn parse_json_result(stdout: &str) -> Result<CompleteResult> {
    let mut last_obj = None;
    for line in stdout.lines().rev() {
        let trimmed = line.trim();
        if trimmed.starts_with('{') {
            last_obj = Some(trimmed);
            break;
        }
    }
    let raw = last_obj.unwrap_or(stdout.trim());
    let parsed: JsonResult = serde_json::from_str(raw).with_context(|| {
        format!(
            "failed to parse agent JSON output: {}",
            raw.chars().take(200).collect::<String>()
        )
    })?;
    if parsed.is_error.unwrap_or(false) {
        bail!("agent returned is_error=true");
    }
    let text = parsed
        .result
        .filter(|s| !s.is_empty())
        .ok_or_else(|| anyhow!("agent JSON missing result text"))?;
    Ok(CompleteResult {
        text,
        session_id: parsed.session_id,
        duration_ms: parsed.duration_ms.unwrap_or(0),
    })
}

fn parse_kiro_result(stdout: &str) -> Result<CompleteResult> {
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        bail!("kiro-cli returned empty stdout");
    }
    // Prefer Cursor-shaped JSON if present; otherwise treat as plain text.
    if trimmed.contains('{') {
        if let Ok(parsed) = parse_json_result(stdout) {
            return Ok(parsed);
        }
        // Try last JSON line for stream-json style payloads with a "result" or "text" field.
        for line in trimmed.lines().rev() {
            let line = line.trim();
            if !line.starts_with('{') {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                if let Some(text) = v
                    .get("result")
                    .and_then(|x| x.as_str())
                    .or_else(|| v.pointer("/message/content").and_then(|x| x.as_str()))
                    .or_else(|| v.get("text").and_then(|x| x.as_str()))
                {
                    if !text.is_empty() {
                        return Ok(CompleteResult {
                            text: text.to_string(),
                            session_id: v
                                .get("session_id")
                                .and_then(|x| x.as_str())
                                .map(|s| s.to_string()),
                            duration_ms: v.get("duration_ms").and_then(|x| x.as_u64()).unwrap_or(0),
                        });
                    }
                }
            }
        }
    }
    Ok(CompleteResult {
        text: trimmed.to_string(),
        session_id: None,
        duration_ms: 0,
    })
}

pub fn resolve_agent_binary(configured: &str) -> Result<PathBuf> {
    Ok(resolve_agent_launch(configured)?.program)
}

pub fn resolve_agent_launch(configured: &str) -> Result<AgentLaunch> {
    if !configured.is_empty() {
        let path = PathBuf::from(configured);
        if path.is_file() {
            return Ok(AgentLaunch {
                display: path.display().to_string(),
                program: path,
                prefix_args: Vec::new(),
            });
        }
        bail!("kiro binary not found: {configured}");
    }

    for name in ["kiro-cli", "kiro"] {
        if let Some(path) = which(name) {
            return Ok(AgentLaunch {
                display: path.display().to_string(),
                program: path,
                prefix_args: Vec::new(),
            });
        }
    }

    for candidate in common_kiro_candidates() {
        if candidate.is_file() {
            return Ok(AgentLaunch {
                display: candidate.display().to_string(),
                program: candidate,
                prefix_args: Vec::new(),
            });
        }
    }

    bail!(
        "could not find `kiro-cli` on PATH. Install with: irm https://cli.kiro.dev/install.ps1 | iex  then run `kiro-cli login`"
    )
}

fn common_kiro_candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        out.push(PathBuf::from(local).join("Kiro-Cli").join("kiro-cli.exe"));
    }
    out.push(PathBuf::from(r"C:\Program Files\Kiro-Cli\kiro-cli.exe"));
    out
}

fn launch_from_path(path: PathBuf) -> Result<AgentLaunch> {
    if !path.exists() && which(path.to_string_lossy().as_ref()).is_none() {
        bail!("cursor binary not found: {}", path.display());
    }

    // If user pointed at a wrapper, still prefer sibling/share bundled node.
    if let Some(launch) = find_bundled_node_near(&path) {
        return Ok(launch);
    }

    #[cfg(windows)]
    {
        let ext = path
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext == "cmd" || ext == "bat" {
            return Ok(AgentLaunch {
                display: path.display().to_string(),
                program: PathBuf::from("cmd.exe"),
                prefix_args: vec![
                    "/D".into(),
                    "/C".into(),
                    format!("\"{}\"", path.display()),
                ],
            });
        }
        if ext == "ps1" {
            return Ok(AgentLaunch {
                display: path.display().to_string(),
                program: PathBuf::from(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
                prefix_args: vec![
                    "-NoProfile".into(),
                    "-ExecutionPolicy".into(),
                    "Bypass".into(),
                    "-File".into(),
                    path.display().to_string(),
                ],
            });
        }
    }

    Ok(AgentLaunch {
        display: path.display().to_string(),
        program: path,
        prefix_args: Vec::new(),
    })
}

fn cursor_agent_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();

    #[cfg(windows)]
    {
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            roots.push(PathBuf::from(local).join("cursor-agent"));
        }
    }

    #[cfg(not(windows))]
    {
        if let Some(home) = dirs::home_dir() {
            roots.push(home.join(".local").join("share").join("cursor-agent"));
            roots.push(home.join(".cursor-agent"));
            roots.push(home.join(".config").join("cursor-agent"));
        }
        if let Some(xdg) = std::env::var_os("XDG_DATA_HOME") {
            roots.push(PathBuf::from(xdg).join("cursor-agent"));
        }
    }

    roots
}

fn find_bundled_node_launch() -> Option<AgentLaunch> {
    for root in cursor_agent_roots() {
        if let Some(launch) = find_latest_node_under(&root) {
            return Some(launch);
        }
    }
    None
}

fn find_bundled_node_near(wrapper: &Path) -> Option<AgentLaunch> {
    let resolved = wrapper.canonicalize().unwrap_or_else(|_| wrapper.to_path_buf());
    let parent = resolved.parent()?;

    if let Some(launch) = find_latest_node_under(parent) {
        return Some(launch);
    }

    // .../cursor-agent/agent.cmd or .../versions/<ver>/node
    if parent.file_name()?.to_string_lossy() == "cursor-agent" {
        if let Some(launch) = find_latest_node_under(parent) {
            return Some(launch);
        }
    }
    if parent.file_name()?.to_string_lossy() == "versions" {
        if let Some(grand) = parent.parent() {
            if let Some(launch) = find_latest_node_under(grand) {
                return Some(launch);
            }
        }
    }

    // ~/.local/bin/agent → ~/.local/share/cursor-agent
    if parent.ends_with(Path::new(".local").join("bin")) {
        if let Some(home) = dirs::home_dir() {
            if let Some(launch) =
                find_latest_node_under(&home.join(".local").join("share").join("cursor-agent"))
            {
                return Some(launch);
            }
        }
    }

    find_bundled_node_launch()
}

fn node_binary_name() -> &'static str {
    if cfg!(windows) {
        "node.exe"
    } else {
        "node"
    }
}

fn find_latest_node_under(root: &Path) -> Option<AgentLaunch> {
    let versions = root.join("versions");
    if !versions.is_dir() {
        return None;
    }
    let node_name = node_binary_name();
    let mut best: Option<(i64, PathBuf)> = None;
    let entries = std::fs::read_dir(&versions).ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !entry.path().is_dir() {
            continue;
        }
        let Some(key) = parse_agent_version_key(&name) else {
            continue;
        };
        let node = entry.path().join(node_name);
        let index = entry.path().join("index.js");
        if node.is_file() && index.is_file() {
            let replace = match &best {
                None => true,
                Some((prev, _)) => key >= *prev,
            };
            if replace {
                best = Some((key, entry.path()));
            }
        }
    }
    let (_, ver_dir) = best?;
    let node = ver_dir.join(node_name);
    let index = ver_dir.join("index.js");
    Some(AgentLaunch {
        display: format!("{} + {}", node.display(), index.display()),
        program: node,
        prefix_args: vec![index.display().to_string()],
    })
}

fn parse_agent_version_key(name: &str) -> Option<i64> {
    // YYYY.MM.DD-commit or YYYY.MM.DD-HH-MM-SS-commit
    let date_part = name.split('-').next()?;
    let parts: Vec<&str> = date_part.split('.').collect();
    if parts.len() != 3 {
        return None;
    }
    let year: i64 = parts[0].parse().ok()?;
    let month: i64 = parts[1].parse().ok()?;
    let day: i64 = parts[2].parse().ok()?;
    Some(year * 10_000 + month * 100 + day)
}

fn common_agent_candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    let home = dirs::home_dir();

    #[cfg(windows)]
    {
        if let Some(home) = &home {
            out.push(home.join(".local").join("bin").join("agent.exe"));
            out.push(home.join(".local").join("bin").join("agent.cmd"));
            out.push(home.join(".local").join("bin").join("agent"));
            out.push(home.join(".local").join("bin").join("cursor-agent.exe"));
            out.push(home.join(".local").join("bin").join("cursor-agent.cmd"));
            out.push(home.join(".local").join("bin").join("cursor-agent"));
        }
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            let local = PathBuf::from(local);
            out.push(local.join("cursor-agent").join("agent.exe"));
            out.push(local.join("cursor-agent").join("agent.cmd"));
            out.push(local.join("cursor-agent").join("cursor-agent.exe"));
            out.push(local.join("cursor-agent").join("cursor-agent.cmd"));
        }
    }

    #[cfg(not(windows))]
    {
        if let Some(home) = &home {
            out.push(home.join(".local").join("bin").join("agent"));
            out.push(home.join(".local").join("bin").join("cursor-agent"));
            out.push(home.join(".cursor").join("bin").join("agent"));
            out.push(home.join("bin").join("agent"));
            // Versioned package roots (in case PATH wrapper is missing)
            out.push(
                home.join(".local")
                    .join("share")
                    .join("cursor-agent")
                    .join("agent"),
            );
        }
        out.push(PathBuf::from("/usr/local/bin/agent"));
        out.push(PathBuf::from("/usr/local/bin/cursor-agent"));
        out.push(PathBuf::from("/opt/homebrew/bin/agent"));
        out.push(PathBuf::from("/opt/homebrew/bin/cursor-agent"));
    }

    out
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(name);
        if is_executable_candidate(&candidate) {
            return Some(candidate);
        }
        #[cfg(windows)]
        {
            for ext in [".exe", ".cmd", ".bat"] {
                let with_ext = dir.join(format!("{name}{ext}"));
                if is_executable_candidate(&with_ext) {
                    return Some(with_ext);
                }
            }
        }
    }
    None
}

fn is_executable_candidate(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = path.metadata() {
            return meta.permissions().mode() & 0o111 != 0;
        }
        return false;
    }
    #[cfg(not(unix))]
    {
        true
    }
}

pub async fn probe_health(binary: &Path) -> Result<String> {
    let launch = launch_from_path(binary.to_path_buf())?;
    probe_health_launch(&launch).await
}

pub async fn probe_health_launch(launch: &AgentLaunch) -> Result<String> {
    let mut cmd = Command::new(&launch.program);
    for arg in &launch.prefix_args {
        cmd.arg(arg);
    }
    cmd.arg("--version");
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    let output = timeout(Duration::from_secs(10), cmd.output())
        .await
        .context("agent --version timed out")?
        .with_context(|| format!("spawn agent --version ({})", launch.display))?;
    let mut text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() {
        text = String::from_utf8_lossy(&output.stderr).trim().to_string();
    }
    if !output.status.success() && text.is_empty() {
        bail!("agent --version failed with {}", output.status);
    }
    Ok(text)
}

pub async fn smoke_test(
    backend: &CursorBackend,
    timeout_secs: u64,
) -> Result<String> {
    let req = ChatRequest {
        model: String::new(),
        prompt: "Reply with exactly: bridge-ok".into(),
        stream: false,
    };
    let result = backend
        .complete(req, timeout_secs.min(120))
        .await
        .map_err(|e| match e {
            CompleteError::Busy(_) => anyhow!("server busy"),
            CompleteError::Other(err) => err,
        })?;
    Ok(result.text.trim().to_string())
}
