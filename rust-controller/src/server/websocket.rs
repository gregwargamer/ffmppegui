//websocket pour les agents
use axum::{extract::{State, ws::{Message, WebSocket, WebSocketUpgrade}}, response::Response};
use futures::{SinkExt, StreamExt};
use std::sync::Arc;
use uuid::Uuid;
use chrono::Utc;
use tokio::sync::mpsc;

use crate::{agents::AgentInfo, state::AppState};

//mise à niveau WebSocket
pub async fn agent_ws_upgrade(State(state): State<Arc<AppState>>, ws: WebSocketUpgrade) -> Response {
    //gestion de la connexion WebSocket
    ws.on_upgrade(move |socket| {
        let state = state.clone();
        async move { handle_agent_socket(state, socket).await }
    })
}

//boucle principale du socket
async fn handle_agent_socket(state: Arc<AppState>, socket: WebSocket) {
    //split du socket pour permettre l'envoi concurrent
    let (mut sink, mut stream) = socket.split();
    //canal d'envoi JSON
    let (tx, mut rx) = mpsc::unbounded_channel::<String>();
    //tâche d'envoi
    tokio::spawn(async move {
        while let Some(text) = rx.recv().await {
            let _ = sink.send(Message::Text(text)).await;
        }
    });

    //message de bienvenue
    let _ = tx.send("{\"type\":\"hello\"}".to_string());

    //id agent courant (si enregistré)
    let mut current_id: Option<String> = None;

    //écoute des messages entrants
    while let Some(Ok(msg)) = stream.next().await {
        match msg {
            Message::Text(text) => {
                tracing::debug!(%text, "agent message");
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                    match val.get("type").and_then(|v| v.as_str()) {
                        Some("register") => {
                            let token_ok = if let Some(token) = val.pointer("/payload/token").and_then(|v| v.as_str()) {
                                token.len() == 25 && state.allowed_tokens.read().await.contains(token)
                            } else { false };
                            if !token_ok { let _ = tx.send("{\"type\":\"error\",\"error\":\"unauthorized\"}".to_string()); break; }
                            let id = val.pointer("/payload/id").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| Uuid::new_v4().to_string());
                            let info = AgentInfo {
                                id: id.clone(),
                                name: val.pointer("/payload/name").and_then(|v| v.as_str()).unwrap_or("agent").to_string(),
                                concurrency: val.pointer("/payload/concurrency").and_then(|v| v.as_u64()).unwrap_or(1) as u32,
                                encoders: val.pointer("/payload/encoders").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default(),
                                active_jobs: 0,
                                last_heartbeat: Utc::now().timestamp_millis(),
                                cpu: 0.0,
                                mem_used: 0,
                                mem_total: 0,
                            };
                            state.agents.write().await.insert(id.clone(), info);
                            state.agent_channels.write().await.insert(id.clone(), tx.clone());
                            current_id = Some(id.clone());
                            let _ = tx.send(format!("{{\"type\":\"registered\",\"payload\":{{\"id\":\"{}\"}}}}", id));
                            //tentative de dispatch immédiat
                            crate::server::dispatch::try_dispatch(state.clone()).await;
                        }
                        Some("heartbeat") => {
                            if let Some(id) = val.pointer("/payload/id").and_then(|v| v.as_str()) { state.update_heartbeat(id).await; }
                            //optionnel: traiter des métriques si présentes
                            if let Some(id) = val.pointer("/payload/id").and_then(|v| v.as_str()) {
                                if let Some(mut info) = state.agents.write().await.get_mut(id) {
                                    if let Some(active) = val.pointer("/payload/activeJobs").and_then(|v| v.as_u64()) { info.active_jobs = active as u32; }
                                    if let Some(cpu) = val.pointer("/payload/cpu").and_then(|v| v.as_f64()) { info.cpu = cpu as f32; }
                                    if let Some(mu) = val.pointer("/payload/memUsed").and_then(|v| v.as_u64()) { info.mem_used = mu; }
                                    if let Some(mt) = val.pointer("/payload/memTotal").and_then(|v| v.as_u64()) { info.mem_total = mt; }
                                }
                            }
                        }
                        Some("progress") => {
                            //journaliser simplement la progression
                            if let Some(job_id) = val.pointer("/payload/jobId").and_then(|v| v.as_str()) {
                                tracing::debug!(job_id, "progress update");
                                if let Some(mut job) = state.jobs.write().await.get_mut(job_id) { job.status = "running".to_string(); job.updated_at = Utc::now().timestamp_millis(); }
                            }
                        }
                        Some("complete") => {
                            let job_id = val.pointer("/payload/jobId").and_then(|v| v.as_str()).unwrap_or("");
                            let success = val.pointer("/payload/success").and_then(|v| v.as_bool()).unwrap_or(false);
                            if let Some(agent_id) = val.pointer("/payload/agentId").and_then(|v| v.as_str()) {
                            if let Some(mut info) = state.agents.write().await.get_mut(agent_id) { if info.active_jobs > 0 { info.active_jobs -= 1; } }
                            }
                            if let Some(mut job) = state.jobs.write().await.get_mut(job_id) { job.status = if success { "uploaded" } else { "failed" }.to_string(); job.updated_at = Utc::now().timestamp_millis(); }
                            //redispatch
                            crate::server::dispatch::try_dispatch(state.clone()).await;
                        }
                        _ => {}
                    }
                }
            }
            Message::Binary(_) => {}
            Message::Ping(_) => {}
            Message::Close(_) => { break; }
            _ => {}
        }
    }

    //nettoyage à la fermeture
    if let Some(id) = current_id {
        state.agent_channels.write().await.remove(&id);
        //optionnel: retirer l'agent du registre
    }
}
