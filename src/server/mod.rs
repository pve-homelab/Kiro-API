mod routes;

use crate::state::AppState;
use anyhow::{Context, Result};
use axum::Router;
use std::net::SocketAddr;
use std::sync::atomic::Ordering;
use tokio::sync::watch;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;

pub async fn run_server(state: AppState, mut stop_rx: watch::Receiver<bool>) -> Result<()> {
    let addr: SocketAddr = state
        .config
        .read()
        .listen_addr()
        .parse()
        .context("invalid listen address")?;

    let app = build_router(state.clone());

    state.logs.info(format!("binding HTTP server on {addr}"));
    let provenance = state.bind_provenance.read().clone();
    state.logs.info(format!(
        "bind address {} · host from {} · port from {}",
        addr, provenance.host_source, provenance.port_source
    ));
    println!(
        "bind {} (host={}, port={})",
        addr, provenance.host_source, provenance.port_source
    );
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .with_context(|| format!("failed to bind {addr}"))?;

    state.server_running.store(true, Ordering::SeqCst);
    state.server_healthy.store(true, Ordering::SeqCst);
    *state.started_at.write() = Some(chrono::Local::now());
    let cfg = state.config.read().clone();
    let banner = crate::config::ready_banner(&cfg, state.agent_version.read().clone());
    state.logs.info(banner.clone());
    // Also print to stdout for `serve` / headless so it is visible without the TUI.
    println!("{banner}");

    let server = axum::serve(listener, app).with_graceful_shutdown(async move {
        loop {
            if *stop_rx.borrow() {
                break;
            }
            if stop_rx.changed().await.is_err() {
                break;
            }
            if *stop_rx.borrow() {
                break;
            }
        }
    });

    let result = server.await.context("HTTP server error");
    state.server_running.store(false, Ordering::SeqCst);
    state.server_healthy.store(false, Ordering::SeqCst);
    state.logs.info("HTTP server stopped");
    result
}

pub fn build_router(state: AppState) -> Router {
    Router::new()
        .merge(routes::router())
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

pub async fn start_in_background(state: AppState) -> Result<()> {
    if state.is_running() {
        anyhow::bail!("server already running");
    }

    // Probe agent binary once at start
    match state.backend.resolve_launch() {
        Ok(launch) => match crate::cursor::probe_health_launch(&launch).await {
            Ok(ver) => {
                state.logs.info(format!("kiro-cli: {ver} ({})", launch.display));
                *state.agent_version.write() = Some(ver);
            }
            Err(err) => {
                state.logs.warn(format!("agent probe failed: {err:#}"));
                *state.agent_version.write() = None;
            }
        },
        Err(err) => {
            state.logs.warn(format!("agent binary not found: {err:#}"));
        }
    }

    let (tx, rx) = watch::channel(false);
    *state.stop_tx.write() = Some(tx);
    let state_clone = state.clone();
    let handle = tokio::spawn(async move {
        if let Err(err) = run_server(state_clone.clone(), rx).await {
            state_clone.logs.error(format!("server error: {err:#}"));
            state_clone.server_running.store(false, Ordering::SeqCst);
            state_clone.server_healthy.store(false, Ordering::SeqCst);
        }
    });
    *state.server_handle.write() = Some(handle);
    // Give bind a moment
    tokio::time::sleep(std::time::Duration::from_millis(80)).await;
    if !state.is_running() {
        anyhow::bail!("server failed to start — check Logs tab");
    }
    Ok(())
}

pub async fn stop_server(state: &AppState) -> Result<()> {
    if !state.is_running() {
        anyhow::bail!("server is not running");
    }
    if let Some(tx) = state.stop_tx.write().take() {
        let _ = tx.send(true);
    }
    if let Some(handle) = state.server_handle.write().take() {
        let _ = tokio::time::timeout(std::time::Duration::from_secs(5), handle).await;
    }
    state.server_running.store(false, Ordering::SeqCst);
    state.server_healthy.store(false, Ordering::SeqCst);
    Ok(())
}
