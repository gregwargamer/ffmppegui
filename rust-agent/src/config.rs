//configuration de l'agent
#[derive(Clone, Debug)]
pub struct AgentConfig {
    pub controller_url: String,
    pub token: String,
    pub concurrency: u32,
    pub ffmpeg_path: String,
}
