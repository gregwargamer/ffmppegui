//exécuteur FFmpeg (implémentation réelle)
use std::path::Path;
use tokio::process::Command;
use tokio::sync::mpsc;
use tokio::time::{timeout, Duration};

pub struct FfmpegExecutor {
    pub ffmpeg_path: String,
}

impl FfmpegExecutor {
    //création d'un exécuteur
    pub fn new(ffmpeg_path: String) -> Self {
        Self { ffmpeg_path }
    }

    //exécution de ffmpeg avec suivi de progression
    pub async fn spawn_and_monitor(
        &self,
        input_url: &str,
        ffmpeg_args: &[String],
        output_path: &Path,
        job_id: &str,
        progress_tx: mpsc::UnboundedSender<String>,
        job_timeout_secs: u64,
    ) -> anyhow::Result<bool> {
        //préparation de la commande ffmpeg
        let mut cmd = Command::new(&self.ffmpeg_path);
        cmd.arg("-i").arg(input_url);
        for a in ffmpeg_args {
            cmd.arg(a);
        }
        cmd.arg(output_path);
        cmd.stdout(std::process::Stdio::piped());
        cmd.stderr(std::process::Stdio::null());

        //lancement du processus
        let mut child = cmd.spawn()?;

        //tâche d'analyse de la progression
        if let Some(stdout) = child.stdout.take() {
            //parsing des lignes 'clé=valeur' émises par -progress pipe:1
            let tx = progress_tx.clone();
            let job = job_id.to_string();
            tokio::spawn(async move {
                //lecteur tamponné
                use tokio::io::{AsyncBufReadExt, BufReader};
                let mut reader = BufReader::new(stdout);
                let mut line = String::new();
                let mut payload = serde_json::Map::new();
                loop {
                    line.clear();
                    match reader.read_line(&mut line).await {
                        Ok(0) => break,
                        Ok(_) => {
                            let trimmed = line.trim();
                            if let Some((k, v)) = trimmed.split_once('=') {
                                payload.insert(k.trim().to_string(), serde_json::Value::String(v.trim().to_string()));
                                if k.trim() == "progress" {
                                    let msg = serde_json::json!({
                                        "type": "progress",
                                        "payload": {"jobId": job, "data": payload}
                                    });
                                    let _ = tx.send(msg.to_string());
                                    payload = serde_json::Map::new();
                                }
                            }
                        }
                        Err(_) => break,
                    }
                }
            });
        }

        //attente avec timeout
        let waited = timeout(Duration::from_secs(job_timeout_secs), child.wait()).await;
        match waited {
            Ok(Ok(status)) => Ok(status.success()),
            Ok(Err(err)) => Err(err.into()),
            Err(_) => {
                let _ = child.kill().await;
                Ok(false)
            }
        }
    }
}
