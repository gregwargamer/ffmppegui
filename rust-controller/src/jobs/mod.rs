//types de médias et structures de jobs
use serde::{Deserialize, Serialize};

//type de média
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MediaType {
    Audio,
    Video,
    Image,
}

//job planifié
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlanJob {
    pub source_path: String,
    pub relative_path: String,
    pub media_type: MediaType,
    pub size_bytes: u64,
    pub output_path: String,
    pub codec: String,
}

//job complet
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Job {
    pub id: String,
    pub status: String,
    pub node_id: Option<String>,
    pub input_token: String,
    pub output_token: String,
    pub created_at: i64,
    pub updated_at: i64,
    #[serde(flatten)]
    pub plan: PlanJob,
}
