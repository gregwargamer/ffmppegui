//websocket pour les agents
use axum::{extract::{State, ws::{Message, WebSocket, WebSocketUpgrade}}, response::Response};
use futures::{SinkExt, StreamExt};
use std::sync::Arc;
use uuid::Uuid;
use chrono::Utc;

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
async fn handle_agent_socket(state: Arc<AppState>, mut socket: WebSocket) {
    //message de bienvenue
    let _ = socket.send(Message::Text("{\"type\":\"hello\"}".to_string())).await;

    //écoute des messages entrants
    while let Some(Ok(msg)) = socket.next().await {
        match msg {
            Message::Text(text) => {
                //journalisation du message
                tracing::debug!(%text, "agent message");
                //attente d'un message register JSON
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                    if val.get("type").and_then(|v| v.as_str()) == Some("register") {
                        let token_ok = if let Some(token) = val.pointer("/payload/token").and_then(|v| v.as_str()) {
                            state.allowed_tokens.read().await.contains(token)
                        } else { false };
                        if !token_ok {
                            let _ = socket.send(Message::Text("{\"type\":\"error\",\"error\":\"unauthorized\"}".to_string())).await;
                            break;
                        }
                        let id = val.pointer("/payload/id").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| Uuid::new_v4().to_string());
                        let info = AgentInfo {
                            id: id.clone(),
                            name: val.pointer("/payload/name").and_then(|v| v.as_str()).unwrap_or("agent").to_string(),
                            concurrency: val.pointer("/payload/concurrency").and_then(|v| v.as_u64()).unwrap_or(1) as u32,
                            encoders: val.pointer("/payload/encoders").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default(),
                            active_jobs: 0,
                            last_heartbeat: Utc::now().timestamp_millis(),
                        };
                        state.agents.write().await.insert(id.clone(), info);
                        let _ = socket.send(Message::Text(format!("{{\"type\":\"registered\",\"payload\":{{\"id\":\"{}\"}}}}", id))).await;
                    } else if val.get("type").and_then(|v| v.as_str()) == Some("heartbeat") {
                        if let Some(id) = val.pointer("/payload/id").and_then(|v| v.as_str()) {
                            state.update_heartbeat(id).await;
                        }
                    }
                }
            }
            Message::Binary(_) => {
                //ignoré pour l'instant
            }
            Message::Ping(p) => {
                let _ = socket.send(Message::Pong(p)).await;
            }
            Message::Close(_) => {
                break;
            }
            _ => {}
        }
    }
}
