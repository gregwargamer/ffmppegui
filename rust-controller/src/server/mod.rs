//construction du routeur HTTP principal
use axum::{routing::{get, post, put, options}, Router};
use std::sync::Arc;

use crate::state::AppState;
use tower_http::cors::CorsLayer;

mod routes;
mod websocket;
pub mod dispatch;

//exposition du routeur
pub fn build_router(state: Arc<AppState>) -> Router {
    //enregistrement des routes
    Router::new()
        .route("/", get(routes::index))
        .route("/api/health", get(routes::health))
        .route("/api/nodes", get(routes::nodes))
        .route("/api/settings", get(routes::settings_get).post(routes::settings_post))
        .route("/api/pair", post(routes::pair_post))
        .route("/api/scan", post(routes::scan))
        .route("/api/start", post(routes::start))
        .route("/api/*rest", options(routes::options_ok))
        .route("/stream/input/:jobId", get(routes::stream_input))
        .route("/stream/output/:jobId", put(routes::stream_output))
        .route("/stream/*rest", options(routes::options_ok))
        .route("/agent", get(websocket::agent_ws_upgrade))
        .route("/agent", options(routes::options_ok))
        .with_state(state)
        .layer(CorsLayer::permissive())
}
