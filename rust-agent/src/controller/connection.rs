//connexion au contrôleur (client WebSocket minimal)
//connexion au contrôleur (client WebSocket minimal)
use crate::config::AgentConfig;
use futures::{SinkExt, StreamExt};
use tokio_tungstenite::tungstenite::Message;
use url::Url;
use uuid::Uuid;
use sysinfo::SystemExt;
use tokio::time::{sleep, Duration};
use tokio::process::Command;
use tokio::sync::mpsc;

//boucle réseau principale
pub async fn run_agent_connection(cfg: &AgentConfig) -> anyhow::Result<()> {
    //construction de l'URL WS
    let ws_url = cfg.controller_url.replace("http://", "ws://").replace("https://", "wss://");
    let url = format!("{}/agent?token={}", ws_url.trim_end_matches('/'), urlencoding::encode(&cfg.token));
    let url: Url = Url::parse(&url)?;

    //connexion
    let (ws, _resp) = tokio_tungstenite::connect_async(url).await?;

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
    let (mut sink, mut stream) = ws.split();

    //canal d'envoi texte
    let (tx, mut rx) = mpsc::unbounded_channel::<String>();

    //tâche d'écriture vers le socket
    tokio::spawn(async move {
        while let Some(text) = rx.recv().await {
            let _ = sink.send(Message::Text(text)).await;
        }
    });

    //envoi du register initial
    let _ = tx.send(reg.to_string());

    //boucle heartbeats
    let hb_tx = tx.clone();
    tokio::spawn(heartbeat_loop(cfg.clone(), hb_tx));

    //réception
    while let Some(msg) = stream.next().await {
        match msg {
            Ok(Message::Text(text)) => {
                //journalisation du message reçu
                tracing::debug!(%text, "controller message");
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                    if val.get("type").and_then(|v| v.as_str()) == Some("lease") {
                        let p = val.get("payload").cloned().unwrap_or_default();
                        let job_id = p.get("jobId").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let input_url = p.get("inputUrl").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let output_url = p.get("outputUrl").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let ffmpeg_args: Vec<String> = p.get("ffmpegArgs").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
                        let output_ext = p.get("outputExt").and_then(|v| v.as_str()).unwrap_or(".out").to_string();
                        //lancer le traitement du lease
                        let tx_clone = tx.clone();
                        let cfg_clone = cfg.clone();
                        tokio::spawn(async move {
                            let _ = handle_lease(&cfg_clone, tx_clone, job_id, input_url, output_url, ffmpeg_args, output_ext).await;
                        });
                    }
                }
            }
            Ok(Message::Ping(_)) => { /* ignoré, ping/pong côté serveur */ }
            Ok(Message::Close(_)) => break,
            _ => {}
        }
    }
    Ok(())
}

//boucle périodique de heartbeat
async fn heartbeat_loop(cfg: AgentConfig, tx: mpsc::UnboundedSender<String>) {
    loop {
        //construction message heartbeat
        let msg = serde_json::json!({
            "type": "heartbeat",
            "payload": {"id": format!("{}-{}", hostname::get().map(|h| h.to_string_lossy().to_string()).unwrap_or_default(), std::process::id())}
        });
        //envoi au canal socket
        if tx.send(msg.to_string()).is_err() { break; }
        sleep(Duration::from_secs(10)).await;
    }
}

//traitement d'un lease: exécuter ffmpeg et uploader
async fn handle_lease(cfg: &AgentConfig, tx: mpsc::UnboundedSender<String>, job_id: String, input_url: String, output_url: String, ffmpeg_args: Vec<String>, output_ext: String) -> anyhow::Result<()> {
    //dossier temporaire
    let tmp_dir = std::env::temp_dir().join("ffmpegeasy");
    tokio::fs::create_dir_all(&tmp_dir).await.ok();
    let tmp_out = tmp_dir.join(format!("{}{}", job_id, output_ext));

    //construction de la commande ffmpeg
    let mut cmd = Command::new(&cfg.ffmpeg_path);
    cmd.arg("-i").arg(&input_url);
    for a in &ffmpeg_args { cmd.arg(a); }
    cmd.arg(&tmp_out);
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::null());

    //lancement du processus
    let mut child = cmd.spawn()?;
    if let Some(mut stdout) = child.stdout.take() {
        //tâche de parsing de la progression
        let tx_progress = tx.clone();
        let job_id_clone = job_id.clone();
        tokio::spawn(async move {
            use tokio::io::{AsyncBufReadExt, BufReader};
            let mut reader = BufReader::new(stdout);
            let mut line = String::new();
            let mut payload = serde_json::Map::new();
            loop {
                line.clear();
                match reader.read_line(&mut line).await {
                    Ok(0) => break,
                    Ok(_) => {
                        if let Some((k, v)) = line.trim().split_once('=') {
                            payload.insert(k.trim().to_string(), serde_json::Value::String(v.trim().to_string()));
                            if k.trim() == "progress" {
                                let msg = serde_json::json!({"type":"progress","payload": {"jobId": job_id_clone, "data": payload}});
                                let _ = tx_progress.send(msg.to_string());
                                payload = serde_json::Map::new();
                            }
                        }
                    }
                    Err(_) => break,
                }
            }
        });
    }

    //attente fin de ffmpeg
    let status = child.wait().await?;
    if !status.success() {
        let msg = serde_json::json!({"type":"complete","payload":{"jobId": job_id, "agentId": format!("{}-{}", hostname::get()?.to_string_lossy(), std::process::id()), "success": false}}).to_string();
        let _ = tx.send(msg);
        return Ok(())
    }

    //upload du résultat
    let client = reqwest::Client::new();
    let file = tokio::fs::File::open(&tmp_out).await?;
    let size = file.metadata().await?.len();
    let stream = reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(file));
    let resp = client.put(&output_url).header(reqwest::header::CONTENT_LENGTH, size).body(stream).send().await?;
    if !resp.status().is_success() {
        let msg = serde_json::json!({"type":"complete","payload":{"jobId": job_id, "agentId": format!("{}-{}", hostname::get()?.to_string_lossy(), std::process::id()), "success": false}}).to_string();
        let _ = tx.send(msg);
        return Ok(())
    }
    let msg = serde_json::json!({"type":"complete","payload":{"jobId": job_id, "agentId": format!("{}-{}", hostname::get()?.to_string_lossy(), std::process::id()), "success": true}}).to_string();
    let _ = tx.send(msg);
    //nettoyage du fichier temporaire
    let _ = tokio::fs::remove_file(&tmp_out).await;
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
