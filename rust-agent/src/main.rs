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
    };

    //lancement de la boucle principale (connexion contrôleur)
    controller::connection::run_agent_connection(&cfg).await?;
    Ok(())
}
