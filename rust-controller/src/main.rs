//serveur principal axum (contrôleur)
use axum::{routing::get, Router};
use std::net::SocketAddr;
use tracing_subscriber::{fmt, EnvFilter};

mod config;
mod server;
mod jobs;
mod agents;

//point d'entrée asynchrone
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    //initialisation du logging (tracing)
    let filter_layer = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    fmt().with_env_filter(filter_layer).init();

    //construction du routeur HTTP
    let app = server::build_router();

    //configuration d'écoute par défaut
    let host = std::env::var("HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let port: u16 = std::env::var("PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(4000);
    let addr: SocketAddr = format!("{}:{}", host, port).parse()?;

    //démarrage du serveur
    tracing::info!(%addr, "controller listening");
    axum::Server::bind(&addr).serve(app.into_make_service()).await?;
    Ok(())
}
