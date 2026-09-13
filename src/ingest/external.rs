//! External adapter loader: `%APPDATA%/Kiro-API/adapters/<id>/manifest.toml`

use crate::ingest::adapter::{AdapterInfo, AdapterRegistry, IngestAdapter, RouteSpec};
use crate::ingest::IngestRequest;
use anyhow::{bail, Context, Result};
use axum::http::Method;
use serde::Deserialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;

#[derive(Debug, Deserialize)]
struct Manifest {
    id: String,
    #[serde(default)]
    method: String,
    path: String,
    command: String,
    #[serde(default)]
    args: Vec<String>,
    #[serde(default = "default_timeout")]
    timeout_secs: u64,
}

fn default_timeout() -> u64 {
    30
}

pub fn load_external_adapters(registry: &mut AdapterRegistry) -> Vec<AdapterInfo> {
    let mut loaded = Vec::new();
    let Ok(dir) = adapters_dir() else {
        return loaded;
    };
    if !dir.is_dir() {
        return loaded;
    }
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return loaded;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let manifest_path = path.join("manifest.toml");
        if !manifest_path.is_file() {
            continue;
        }
        match load_one(&manifest_path) {
            Ok((info, adapter)) => {
                tracing::info!(
                    target: "ingest",
                    "loaded external adapter {} → {} {}",
                    info.id,
                    info.routes.first().cloned().unwrap_or_default(),
                    path.display()
                );
                loaded.push(info);
                registry.register(adapter);
            }
            Err(err) => {
                tracing::warn!(
                    target: "ingest",
                    "skip adapter {}: {err:#}",
                    path.display()
                );
            }
        }
    }
    loaded
}

fn adapters_dir() -> Result<PathBuf> {
    let base = dirs::config_dir().context("config dir")?;
    Ok(base.join("Kiro-API").join("adapters"))
}

fn load_one(manifest_path: &Path) -> Result<(AdapterInfo, Arc<dyn IngestAdapter>)> {
    let raw = std::fs::read_to_string(manifest_path)?;
    let man: Manifest = toml::from_str(&raw)?;
    let method = match man.method.to_uppercase().as_str() {
        "" | "POST" => Method::POST,
        "GET" => Method::GET,
        "PUT" => Method::PUT,
        other => bail!("unsupported method {other}"),
    };
    let path = man.path.clone();
    let path_static = Box::leak(path.into_boxed_str());
    let route = RouteSpec {
        method: method.clone(),
        path: path_static,
    };
    let info = AdapterInfo {
        id: man.id.clone(),
        routes: vec![format!("{} {}", method, path_static)],
        source: "external".into(),
    };
    let adapter = ExternalAdapter {
        id_owned: man.id,
        routes: vec![route],
        command: man.command,
        args: man.args,
        timeout_secs: man.timeout_secs,
        workdir: manifest_path
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from(".")),
    };
    Ok((info, Arc::new(adapter)))
}

struct ExternalAdapter {
    id_owned: String,
    routes: Vec<RouteSpec>,
    command: String,
    args: Vec<String>,
    timeout_secs: u64,
    workdir: PathBuf,
}

// SAFETY: routes path is leaked &'static str from load_one.
unsafe impl Send for ExternalAdapter {}
unsafe impl Sync for ExternalAdapter {}

impl IngestAdapter for ExternalAdapter {
    fn id(&self) -> &'static str {
        // External ids are dynamic; expose via meta. Trait wants &'static — use a fixed label.
        "external"
    }

    fn routes(&self) -> &[RouteSpec] {
        &self.routes
    }

    fn ingest(&self, method: &Method, path: &str, body: &[u8], headers_json: &Value) -> Result<IngestRequest> {
        let mut cmd = Command::new(&self.command);
        cmd.args(&self.args)
            .current_dir(&self.workdir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("CURSOR_API_ADAPTER_ID", &self.id_owned)
            .env("CURSOR_API_HTTP_METHOD", method.as_str())
            .env("CURSOR_API_HTTP_PATH", path)
            .env(
                "CURSOR_API_HEADERS_JSON",
                headers_json.to_string(),
            );

        let mut child = cmd.spawn().with_context(|| {
            format!(
                "spawn external adapter {} ({})",
                self.id_owned, self.command
            )
        })?;
        if let Some(mut stdin) = child.stdin.take() {
            use std::io::Write;
            stdin.write_all(body)?;
        }
        let output = child
            .wait_with_output()
            .context("wait external adapter")?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            bail!(
                "external adapter {} failed: {} {}",
                self.id_owned,
                output.status,
                stderr.trim()
            );
        }
        let mut req: IngestRequest =
            serde_json::from_slice(&output.stdout).context("external adapter stdout JSON")?;
        if req.meta.is_null() {
            req.meta = serde_json::json!({
                "adapter": self.id_owned,
                "source": "external",
                "timeout_secs": self.timeout_secs,
            });
        }
        Ok(req)
    }
}
