//gestion des routes HTTP simples
use axum::response::IntoResponse;

//page d'accueil
pub async fn index() -> impl IntoResponse {
    "FFmpeg Easy Controller (Rust)"
}

//probe de santé
pub async fn health() -> impl IntoResponse {
    "ok"
}
