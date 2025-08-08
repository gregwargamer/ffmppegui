//connexion au contrôleur (client WebSocket minimal)
use crate::config::AgentConfig;
use futures::{SinkExt, StreamExt};
use tokio_tungstenite::tungstenite::Message;
use url::Url;
use uuid::Uuid;
use sysinfo::SystemExt;
use tokio::time::{sleep, Duration};
use tokio::process::Command;

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
    let (mut sink, mut stream) = ws.split();
    tokio::spawn(heartbeat_loop(cfg.clone(), sink.clone()));

    //réception
    while let Some(msg) = stream.next().await {
        match msg {
            Ok(Message::Text(text)) => {
                tracing::debug!(%text, "controller message");
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                    if val.get("type").and_then(|v| v.as_str()) == Some("lease") {
                        let p = val.get("payload").cloned().unwrap_or_default();
                        let job_id = p.get("jobId").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let input_url = p.get("inputUrl").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let output_url = p.get("outputUrl").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let ffmpeg_args: Vec<String> = p.get("ffmpegArgs").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
                        let output_ext = p.get("outputExt").and_then(|v| v.as_str()).unwrap_or(".out").to_string();
                        let _ = handle_lease(cfg, &mut sink, job_id, input_url, output_url, ffmpeg_args, output_ext).await;
                    }
                }
            }
            Ok(Message::Ping(p)) => { /* ping/pong géré automatiquement par le serveur */ }
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

//traitement d'un lease: exécuter ffmpeg et uploader
async fn handle_lease(cfg: &AgentConfig, sink: &mut (impl futures::Sink<Message, Error = tokio_tungstenite::tungstenite::Error> + Unpin), job_id: String, input_url: String, output_url: String, ffmpeg_args: Vec<String>, output_ext: String) -> anyhow::Result<()> {
    use tokio::fs;
    use tokio::io::AsyncReadExt;
    use tokio::io::AsyncWriteExt;
    use reqwest::Client;
    use std::path::PathBuf;

    //dossier temporaire
    let tmp_dir = std::env::temp_dir().join("ffmpegeasy");
    fs::create_dir_all(&tmp_dir).await.ok();
    let tmp_out = tmp_dir.join(format!("{}{}", job_id, output_ext));

    //lancement de ffmpeg
    let mut cmd = Command::new(&cfg.ffmpeg_path);
    cmd.arg("-i").arg(&input_url);
    for a in ffmpeg_args { cmd.arg(a); }
    cmd.arg(&tmp_out);
    let status = cmd.status().await?;
    if !status.success() {
        let msg = serde_json::json!({"type":"complete","payload":{"jobId": job_id, "agentId": format!("{}-{}", hostname::get()?.to_string_lossy(), std::process::id()), "success": false}}).to_string();
        let _ = sink.send(Message::Text(msg)).await;
        return Ok(())
    }

    //upload
    let client = Client::new();
    let file = fs::File::open(&tmp_out).await?;
    let size = file.metadata().await?.len();
    let stream = reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(file));
    let resp = client.put(&output_url).header(reqwest::header::CONTENT_LENGTH, size).body(stream).send().await?;
    if !resp.status().is_success() {
        let msg = serde_json::json!({"type":"complete","payload":{"jobId": job_id, "agentId": format!("{}-{}", hostname::get()?.to_string_lossy(), std::process::id()), "success": false}}).to_string();
        let _ = sink.send(Message::Text(msg)).await;
        return Ok(())
    }
    let msg = serde_json::json!({"type":"complete","payload":{"jobId": job_id, "agentId": format!("{}-{}", hostname::get()?.to_string_lossy(), std::process::id()), "success": true}}).to_string();
    let _ = sink.send(Message::Text(msg)).await;
    //nettoyage
    let _ = fs::remove_file(&tmp_out).await;
    Ok(())
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
