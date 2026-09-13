use crate::ingest::IngestRequest;
use anyhow::{bail, Result};
use axum::http::{Method, StatusCode};
use serde_json::Value;
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct RouteSpec {
    pub method: Method,
    pub path: &'static str,
}

pub trait IngestAdapter: Send + Sync {
    fn id(&self) -> &'static str;
    fn routes(&self) -> &[RouteSpec];
    /// Parse a raw HTTP body into a canonical ingest request.
    fn ingest(&self, method: &Method, path: &str, body: &[u8], headers_json: &Value) -> Result<IngestRequest>;
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct AdapterInfo {
    pub id: String,
    pub routes: Vec<String>,
    pub source: String,
}

#[derive(Clone, Default)]
pub struct AdapterRegistry {
    adapters: Vec<Arc<dyn IngestAdapter>>,
}

impl AdapterRegistry {
    pub fn new() -> Self {
        Self {
            adapters: Vec::new(),
        }
    }

    pub fn register(&mut self, adapter: Arc<dyn IngestAdapter>) {
        self.adapters.push(adapter);
    }

    pub fn list(&self) -> Vec<AdapterInfo> {
        self.adapters
            .iter()
            .map(|a| AdapterInfo {
                id: a.id().into(),
                routes: a
                    .routes()
                    .iter()
                    .map(|r| format!("{} {}", r.method, r.path))
                    .collect(),
                source: "builtin".into(),
            })
            .collect()
    }

    pub fn find(&self, method: &Method, path: &str) -> Option<Arc<dyn IngestAdapter>> {
        self.adapters.iter().find_map(|a| {
            if a.routes()
                .iter()
                .any(|r| &r.method == method && r.path == path)
            {
                Some(a.clone())
            } else {
                None
            }
        })
    }

    pub fn ingest(
        &self,
        method: &Method,
        path: &str,
        body: &[u8],
        headers_json: &Value,
    ) -> Result<(String, IngestRequest)> {
        let Some(adapter) = self.find(method, path) else {
            bail!("no adapter for {method} {path}");
        };
        let req = adapter.ingest(method, path, body, headers_json)?;
        Ok((adapter.id().into(), req))
    }
}

pub fn method_status_not_found() -> StatusCode {
    StatusCode::NOT_FOUND
}
