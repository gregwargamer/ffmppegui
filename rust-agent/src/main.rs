//agent d'exécution FFmpeg (Rust)
use clap::Parser;
use tracing_subscriber::{fmt, EnvFilter};

mod config;
mod ffmpeg;
mod controller;

//ligne de commande de l'agent
#[derive(Debug, Parser)]
#[command(name = "ffmpegeasy-agent", version, about = "FFmpeg Easy Worker Agent (Rust)")]
struct Cli {
    #[arg(long, env = "CONTROLLER_URL", default_value = "http://localhost:4000")] 
    controller_url: String,
    #[arg(long, env = "AGENT_TOKEN", default_value = "dev-token")] 
    token: String,
    #[arg(long, env = "CONCURRENCY", default_value_t = (num_cpus::get() as u32))]
    concurrency: u32,
    #[arg(long, env = "FFMPEG_PATH", default_value = "ffmpeg")]
    ffmpeg_path: String,
    #[arg(long, env = "JOB_TIMEOUT_SECS", default_value_t = 3600)]
    job_timeout_secs: u64,
    #[arg(long, env = "UPLOAD_MAX_RETRIES", default_value_t = 3)]
    upload_max_retries: u32,
    #[arg(long, env = "REQ_CONNECT_TIMEOUT_SECS", default_value_t = 10)]
    request_connect_timeout_secs: u64,
    #[arg(long, env = "REQ_TIMEOUT_SECS", default_value_t = 900)]
    request_timeout_secs: u64,
}

//point d'entrée asynchrone
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    //initialisation du logging (tracing)
    let filter_layer = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    fmt().with_env_filter(filter_layer).init();

    //analyse des arguments
    let cli = Cli::parse();

    //construction de la configuration
    let cfg = config::AgentConfig {
        controller_url: cli.controller_url,
        token: cli.token,
        concurrency: cli.concurrency,
        ffmpeg_path: cli.ffmpeg_path,
        job_timeout_secs: cli.job_timeout_secs,
        upload_max_retries: cli.upload_max_retries,
        request_connect_timeout_secs: cli.request_connect_timeout_secs,
        request_timeout_secs: cli.request_timeout_secs,
    };

    //lancement de la boucle principale (connexion contrôleur)
    controller::connection::run_agent_connection(&cfg).await?;
    Ok(())
}
