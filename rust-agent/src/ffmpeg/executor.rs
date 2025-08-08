//exécuteur FFmpeg (bouchon)
pub struct FfmpegExecutor {
    pub ffmpeg_path: String,
}

impl FfmpegExecutor {
    //création d'un exécuteur
    pub fn new(ffmpeg_path: String) -> Self {
        Self { ffmpeg_path }
    }

    //lancement simplifié (bouchon)
    pub async fn spawn_and_monitor(&self, _args: &[String]) -> anyhow::Result<i32> {
        Ok(0)
    }
}
