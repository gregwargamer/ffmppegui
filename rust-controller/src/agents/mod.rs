//informations sur un agent connecté
use serde::{Deserialize, Serialize};

//structure d'état d'un agent
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AgentInfo {
    pub id: String,
    pub name: String,
    pub concurrency: u32,
    pub encoders: Vec<String>,
    pub active_jobs: u32,
    pub last_heartbeat: i64,
}
