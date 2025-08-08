//configuration de l'agent
#[derive(Clone, Debug)]
pub struct AgentConfig {
    pub controller_url: String,
    pub token: String,
    pub concurrency: u32,
    pub ffmpeg_path: String,
    pub job_timeout_secs: u64,
    pub upload_max_retries: u32,
    pub request_connect_timeout_secs: u64,
    pub request_timeout_secs: u64,
}
