//connexion au contrôleur (client WebSocket minimal)
use crate::config::AgentConfig;
use futures::{SinkExt, StreamExt};
use tokio_tungstenite::tungstenite::Message;
use url::Url;
use uuid::Uuid;
use sysinfo::SystemExt;
use tokio::time::{sleep, Duration};

//boucle réseau principale
pub async fn run_agent_connection(cfg: &AgentConfig) -> anyhow::Result<()> {
    //construction de l'URL WS
    let ws_url = cfg.controller_url.replace("http://", "ws://").replace("https://", "wss://");
    let url = format!("{}/agent?token={}", ws_url.trim_end_matches('/'), urlencoding::encode(&cfg.token));
    let url: Url = Url::parse(&url)?;

    //connexion
    let (mut ws, _resp) = tokio_tungstenite::connect_async(url).await?;

    //envoi du message register
    let id = format!("{}-{}", hostname::get()?.to_string_lossy(), std::process::id());
    let encoders: Vec<String> = detect_encoders(&cfg.ffmpeg_path).await.unwrap_or_default();
    let reg = serde_json::json!({
        "type": "register",
        "payload": {
            "id": id,
            "name": hostname::get()?.to_string_lossy(),
            "concurrency": cfg.concurrency,
            "encoders": encoders,
            "token": cfg.token,
        }
    });
    ws.send(Message::Text(reg.to_string())).await?;

    //boucle heartbeats
    tokio::spawn(heartbeat_loop(cfg.clone(), ws.split().0));

    //réception
    while let Some(msg) = ws.next().await {
        match msg {
            Ok(Message::Text(text)) => {
                tracing::debug!(%text, "controller message");
            }
            Ok(Message::Ping(p)) => { let _ = ws.send(Message::Pong(p)).await; }
            Ok(Message::Close(_)) => break,
            _ => {}
        }
    }
    Ok(())
}

//boucle périodique de heartbeat
async fn heartbeat_loop(cfg: AgentConfig, mut sink: impl futures::Sink<Message, Error = tokio_tungstenite::tungstenite::Error> + Unpin + Send + 'static) {
    loop {
        let msg = serde_json::json!({
            "type": "heartbeat",
            "payload": {"id": format!("{}-{}", hostname::get().map(|h| h.to_string_lossy().to_string()).unwrap_or_default(), std::process::id())}
        });
        if sink.send(Message::Text(msg.to_string())).await.is_err() { break; }
        sleep(Duration::from_secs(10)).await;
    }
}

//détection simple des encodeurs via `ffmpeg -hide_banner -encoders`
async fn detect_encoders(ffmpeg_path: &str) -> anyhow::Result<Vec<String>> {
    use tokio::process::Command;
    let out = Command::new(ffmpeg_path).arg("-hide_banner").arg("-encoders").output().await?;
    if !out.status.success() { return Ok(vec![]); }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut list = Vec::new();
    for line in text.lines() {
        if let Some(cap) = line.split_whitespace().nth(1) {
            if cap.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
                list.push(cap.to_string());
            }
        }
    }
    Ok(list)
}
