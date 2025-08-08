//construction du routeur HTTP principal
use axum::{routing::get, Router};

mod routes;
mod websocket;

//exposition du routeur
pub fn build_router() -> Router {
    //enregistrement des routes
    Router::new()
        .route("/", get(routes::index))
        .route("/api/health", get(routes::health))
        .route("/agent", get(websocket::agent_ws_upgrade))
}
