//websocket pour les agents
use axum::{extract::ws::{Message, WebSocket, WebSocketUpgrade}, response::Response};
use futures::{SinkExt, StreamExt};

//mise à niveau WebSocket
pub async fn agent_ws_upgrade(ws: WebSocketUpgrade) -> Response {
    //gestion de la connexion WebSocket
    ws.on_upgrade(|socket| async move { handle_agent_socket(socket).await })
}

//boucle principale du socket
async fn handle_agent_socket(mut socket: WebSocket) {
    //message de bienvenue
    let _ = socket.send(Message::Text("{\"type\":\"registered\"}".to_string())).await;

    //écoute des messages entrants
    while let Some(Ok(msg)) = socket.next().await {
        match msg {
            Message::Text(text) => {
                //journalisation du message
                tracing::debug!(%text, "agent message");
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
