//état partagé du contrôleur
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::{mpsc, RwLock};
use chrono::Utc;

use crate::{agents::AgentInfo, jobs::Job};

//registre des agents + paramètres
#[derive(Debug)]
pub struct AppState {
    pub agents: RwLock<HashMap<String, AgentInfo>>, //par id
    pub agent_channels: RwLock<HashMap<String, mpsc::UnboundedSender<String>>>, //canal d'envoi JSON
    pub allowed_tokens: RwLock<HashSet<String>>,    //tokens d'appairage autorisés
    pub public_base_url: RwLock<String>,            //URL publique
    pub jobs: RwLock<HashMap<String, Job>>,         //jobs connus
    pub pending_jobs: RwLock<VecDeque<String>>,     //file d'attente FIFO (IDs de jobs)
}

impl AppState {
    //création avec valeurs par défaut
    pub fn new() -> Arc<Self> {
        let default_token = std::env::var("AGENT_SHARED_TOKEN").unwrap_or_else(|_| "dev-token".to_string());
        Arc::new(Self {
            agents: RwLock::new(HashMap::new()),
            agent_channels: RwLock::new(HashMap::new()),
            allowed_tokens: RwLock::new(HashSet::from([default_token])),
            public_base_url: RwLock::new("http://localhost:4000".to_string()),
            jobs: RwLock::new(HashMap::new()),
            pending_jobs: RwLock::new(VecDeque::new()),
        })
    }

    //mise à jour du heartbeat
    pub async fn update_heartbeat(&self, id: &str) {
        if let Some(agent) = self.agents.write().await.get_mut(id) {
            agent.last_heartbeat = Utc::now().timestamp_millis();
        }
    }
}
