//configuration simple du serveur
#[derive(Clone, Debug)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
}

//valeurs par défaut
impl Default for ServerConfig {
    fn default() -> Self {
        Self { host: "0.0.0.0".to_string(), port: 4000 }
    }
}
