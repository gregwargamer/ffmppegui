//gestion des routes HTTP simples
use axum::{extract::{Path, Query, State}, http::StatusCode, response::IntoResponse, Json};
use axum::body::Body;
use axum::http::header::{CONTENT_LENGTH, CONTENT_TYPE, ACCEPT_RANGES, CONTENT_RANGE};
use tokio::fs as tokio_fs;
use tokio::io::{AsyncSeekExt, AsyncWriteExt, AsyncReadExt};
use tokio::fs::File;
use std::path::Path as FsPath;
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::{agents::AgentInfo, state::AppState};
use walkdir::WalkDir;

//page d'accueil
pub async fn index() -> impl IntoResponse {
    "FFmpeg Easy Controller (Rust)"
}

//probe de santé
pub async fn health() -> impl IntoResponse {
    "ok"
}

//liste des nœuds (agents)
pub async fn nodes(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let list: Vec<AgentInfo> = state.agents.read().await.values().cloned().collect();
    Json(serde_json::json!({
        "agents": list
    }))
}

//lecture des paramètres
pub async fn settings_get(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let public_base_url = state.public_base_url.read().await.clone();
    Json(serde_json::json!({ "publicBaseUrl": public_base_url }))
}

#[derive(Deserialize)]
pub struct SettingsPayload { pub publicBaseUrl: Option<String> }

//mise à jour des paramètres
pub async fn settings_post(State(state): State<Arc<AppState>>, Json(body): Json<SettingsPayload>) -> impl IntoResponse {
    if let Some(url) = body.publicBaseUrl {
        if !(url.starts_with("http://") || url.starts_with("https://")) {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"invalid URL"}))).into_response();
        }
        *state.public_base_url.write().await = url.trim_end_matches('/').to_string();
    }
    StatusCode::OK.into_response()
}

#[derive(Deserialize)]
pub struct PairPayload { pub token: Option<String> }

//ajout d'un token d'appairage
pub async fn pair_post(State(state): State<Arc<AppState>>, Json(body): Json<PairPayload>) -> impl IntoResponse {
    let token = body.token.unwrap_or_default();
    if token.len() != 25 { return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"invalid token"}))).into_response(); }
    state.allowed_tokens.write().await.insert(token);
    StatusCode::OK.into_response()
}

//structures de scan et planification
#[derive(Deserialize)]
pub struct ScanRequest {
    pub inputRoot: String,
    pub outputRoot: String,
    pub recursive: Option<bool>,
    pub mirrorStructure: Option<bool>,
    pub mediaType: String,
    pub codec: String,
}

#[derive(Serialize)]
pub struct ScanResponse { pub count: usize, pub totalBytes: u64, pub jobs: Vec<crate::jobs::PlanJob> }

//scan de fichiers (simplifié, sans lecture réelle)
pub async fn scan(State(_state): State<Arc<AppState>>, Json(body): Json<ScanRequest>) -> impl IntoResponse {
    if body.inputRoot.is_empty() || body.outputRoot.is_empty() || body.mediaType.is_empty() || body.codec.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"invalid request"}))).into_response();
    }
    let recursive = body.recursive.unwrap_or(true);
    let mirror = body.mirrorStructure.unwrap_or(true);
    let media = body.mediaType.to_lowercase();
    let src_root = std::path::Path::new(&body.inputRoot);
    let out_root = std::path::Path::new(&body.outputRoot);

    //extensions autorisées par type
    let exts_audio = [".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma", ".aiff", ".alac"]; 
    let exts_video = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"]; 
    let exts_image = [".jpg", ".jpeg", ".png", ".webp", ".tiff", ".bmp", ".heic", ".heif", ".avif"]; 

    let mut results: Vec<crate::jobs::PlanJob> = Vec::new();
    let mut total: u64 = 0;

    for entry in WalkDir::new(src_root).into_iter().filter_map(|e| e.ok()) {
        if entry.file_type().is_dir() { if !recursive && entry.depth() > 1 { break; } continue; }
        let path = entry.path();
        if !path.is_file() { continue; }
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
        let dot_ext = format!(".{}", ext);
        let keep = match media.as_str() {
            "audio" => exts_audio.contains(&dot_ext.as_str()),
            "video" => exts_video.contains(&dot_ext.as_str()),
            _ => exts_image.contains(&dot_ext.as_str()),
        };
        if !keep { continue; }
        let meta = match tokio_fs::metadata(path).await { Ok(m) => m, Err(_) => continue };
        let rel = pathdiff::diff_paths(path, src_root).unwrap_or_else(|| std::path::PathBuf::from(entry.file_name()));
        let base = if mirror { out_root.join(&rel) } else { out_root.join(path.file_name().unwrap_or_default()) };
        let out = base.with_extension("");
        //extension de sortie déduite au moment du dispatch
        results.push(crate::jobs::PlanJob {
            source_path: path.to_string_lossy().to_string(),
            relative_path: rel.to_string_lossy().to_string(),
            media_type: match media.as_str() { "audio" => crate::jobs::MediaType::Audio, "video" => crate::jobs::MediaType::Video, _ => crate::jobs::MediaType::Image },
            size_bytes: meta.len(),
            output_path: out.to_string_lossy().to_string(),
            codec: body.codec.clone(),
        });
        total += meta.len();
    }
    Json(ScanResponse { count: results.len(), totalBytes: total, jobs: results })
}

#[derive(Deserialize)]
pub struct StartPayload { pub jobs: Vec<crate::jobs::PlanJob> }

//soumission de jobs et mise en file d'attente
pub async fn start(State(state): State<Arc<AppState>>, Json(body): Json<StartPayload>) -> impl IntoResponse {
    if body.jobs.is_empty() { return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"no jobs"}))).into_response(); }
    for pj in &body.jobs {
        if pj.source_path.trim().is_empty() || pj.output_path.trim().is_empty() || pj.codec.trim().is_empty() { 
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"invalid job"}))).into_response();
        }
        if !std::path::Path::new(&pj.source_path).exists() {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error":"missing source"}))).into_response();
        }
    }
    let mut ids = Vec::new();
    let now = chrono::Utc::now().timestamp_millis();
    for pj in body.jobs {
        let id = uuid::Uuid::new_v4().to_string();
        let job = crate::jobs::Job {
            id: id.clone(),
            status: "pending".to_string(),
            node_id: None,
            input_token: uuid::Uuid::new_v4().to_string(),
            output_token: uuid::Uuid::new_v4().to_string(),
            created_at: now,
            updated_at: now,
            plan: pj,
        };
        state.jobs.write().await.insert(id.clone(), job);
        state.pending_jobs.write().await.push(id.clone());
        ids.push(id);
    }
    Json(serde_json::json!({"accepted": ids.len()}))
}

//flux d'entrée (lecture de fichier avec range)
pub async fn stream_input(State(state): State<Arc<AppState>>, Path(job_id): Path<String>, Query(q): Query<std::collections::HashMap<String, String>>, req: axum::http::Request<Body>) -> impl IntoResponse {
    let token = q.get("token").cloned().unwrap_or_default();
    let job = match state.jobs.read().await.get(&job_id) { Some(j) => j.clone(), None => return (StatusCode::NOT_FOUND, "not found").into_response() };
    if token != job.input_token { return (StatusCode::FORBIDDEN, "forbidden").into_response(); }
    let path = FsPath::new(&job.plan.source_path);
    let meta = match tokio_fs::metadata(path).await { Ok(m) => m, Err(_) => return (StatusCode::NOT_FOUND, "not found").into_response() };
    let size = meta.len();
    let range_hdr = req.headers().get("range").and_then(|v| v.to_str().ok()).unwrap_or("");
    if let Some(spec) = range_hdr.strip_prefix("bytes=") {
        let mut parts = spec.split('-');
        let start: u64 = parts.next().and_then(|s| s.parse().ok()).unwrap_or(0);
        let end: u64 = parts.next().and_then(|s| s.parse().ok()).unwrap_or(size - 1);
        let len = end.saturating_sub(start) + 1;
        let mut f = match File::open(path).await { Ok(f) => f, Err(_) => return (StatusCode::NOT_FOUND, "not found").into_response() };
        let mut buf = vec![0u8; len as usize];
        if f.seek(std::io::SeekFrom::Start(start)).await.is_err() { return (StatusCode::RANGE_NOT_SATISFIABLE, "range").into_response(); }
        if f.read_exact(&mut buf).await.is_err() { return (StatusCode::RANGE_NOT_SATISFIABLE, "range").into_response(); }
        let mut resp = axum::response::Response::builder().status(StatusCode::PARTIAL_CONTENT).body(Body::from(buf)).unwrap();
        let headers = resp.headers_mut();
        headers.insert(CONTENT_RANGE, format!("bytes {}-{}/{}", start, end, size).parse().unwrap());
        headers.insert(ACCEPT_RANGES, "bytes".parse().unwrap());
        headers.insert(CONTENT_LENGTH, len.to_string().parse().unwrap());
        headers.insert(CONTENT_TYPE, "application/octet-stream".parse().unwrap());
        return resp;
    }
    let mut f = match File::open(path).await { Ok(f) => f, Err(_) => return (StatusCode::NOT_FOUND, "not found").into_response() };
    let mut buf = Vec::with_capacity(size as usize);
    if f.read_to_end(&mut buf).await.is_err() { return (StatusCode::INTERNAL_SERVER_ERROR, "io").into_response(); }
    let mut resp = axum::response::Response::builder().status(StatusCode::OK).body(Body::from(buf)).unwrap();
    let headers = resp.headers_mut();
    headers.insert(ACCEPT_RANGES, "bytes".parse().unwrap());
    headers.insert(CONTENT_LENGTH, size.to_string().parse().unwrap());
    headers.insert(CONTENT_TYPE, "application/octet-stream".parse().unwrap());
    resp
}

//réception de sortie (upload)
pub async fn stream_output(State(state): State<Arc<AppState>>, Path(job_id): Path<String>, Query(q): Query<std::collections::HashMap<String, String>>, mut req: axum::http::Request<Body>) -> impl IntoResponse {
    let token = q.get("token").cloned().unwrap_or_default();
    let job = match state.jobs.read().await.get(&job_id) { Some(j) => j.clone(), None => return (StatusCode::NOT_FOUND, "not found").into_response() };
    if token != job.output_token { return (StatusCode::FORBIDDEN, "forbidden").into_response(); }
    let tmp_path = format!("{}.part", job.plan.output_path);
    let final_path = job.plan.output_path.clone();
    let mut file = match File::create(&tmp_path).await { Ok(f) => f, Err(_) => return (StatusCode::INTERNAL_SERVER_ERROR, "io").into_response() };
    let mut body = req.body_mut();
    use futures::StreamExt as _;
    while let Some(chunk) = body.next().await {
        match chunk {
            Ok(bytes) => { if file.write_all(&bytes).await.is_err() { return (StatusCode::INTERNAL_SERVER_ERROR, "io").into_response(); } }
            Err(_) => return (StatusCode::INTERNAL_SERVER_ERROR, "io").into_response(),
        }
    }
    if tokio_fs::rename(&tmp_path, &final_path).await.is_err() { return (StatusCode::INTERNAL_SERVER_ERROR, "io").into_response(); }
    StatusCode::OK
}
