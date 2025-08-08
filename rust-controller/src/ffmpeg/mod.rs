//outils liés à FFmpeg pour construire les arguments
use crate::jobs::{Job, MediaType};

//détermination de l'extension de sortie
pub fn compute_output_ext(media_type: &MediaType, codec: &str) -> String {
    match media_type {
        MediaType::Audio => match codec {
            "flac" => ".flac".into(),
            "alac" => ".m4a".into(),
            "aac" => ".m4a".into(),
            "mp3" => ".mp3".into(),
            "opus" => ".opus".into(),
            "ogg" | "vorbis" => ".ogg".into(),
            _ => ".m4a".into(),
        },
        MediaType::Video => match codec {
            "h264" => ".mp4".into(),
            "h265" | "hevc" => ".mp4".into(),
            "av1" => ".mkv".into(),
            "vp9" => ".webm".into(),
            _ => ".mp4".into(),
        },
        MediaType::Image => match codec {
            "avif" => ".avif".into(),
            "heic" => ".heic".into(),
            "heif" => ".heif".into(),
            "webp" => ".webp".into(),
            "png" => ".png".into(),
            "jpeg" | "jpg" => ".jpg".into(),
            _ => ".png".into(),
        },
    }
}

//construction d'arguments FFmpeg basiques
pub fn build_ffmpeg_args(job: &Job) -> Vec<String> {
    let mut args: Vec<String> = vec![
        "-hide_banner".into(),
        "-nostdin".into(),
        "-y".into(),
        "-progress".into(), "pipe:1".into(),
        "-loglevel".into(), "error".into(),
    ];
    match job.plan.media_type {
        MediaType::Audio => {
            args.push("-vn".into());
            match job.plan.codec.as_str() {
                "flac" => { args.extend(["-c:a".into(), "flac".into()]); }
                "alac" => { args.extend(["-c:a".into(), "alac".into()]); }
                "aac" => { args.extend(["-c:a".into(), "aac".into(), "-b:a".into(), "192k".into()]); }
                "mp3" => { args.extend(["-c:a".into(), "libmp3lame".into(), "-b:a".into(), "192k".into()]); }
                "opus" => { args.extend(["-c:a".into(), "libopus".into(), "-b:a".into(), "160k".into()]); }
                "ogg" | "vorbis" => { args.extend(["-c:a".into(), "libvorbis".into(), "-q:a".into(), "5".into()]); }
                _ => { args.extend(["-c:a".into(), "aac".into(), "-b:a".into(), "192k".into()]); }
            }
        }
        MediaType::Video => {
            args.extend(["-pix_fmt".into(), "yuv420p".into()]);
            let (crf, preset) = if job.plan.codec == "h265" || job.plan.codec == "hevc" { (28, "medium") } else if job.plan.codec == "av1" { (32, "6") } else { (23, "medium") };
            match job.plan.codec.as_str() {
                "h264" => { args.extend(["-c:v".into(), "libx264".into(), "-preset".into(), preset.into(), "-crf".into(), crf.to_string()]); }
                "h265" | "hevc" => { args.extend(["-c:v".into(), "libx265".into(), "-preset".into(), preset.into(), "-crf".into(), crf.to_string()]); }
                "av1" => { args.extend(["-c:v".into(), "libsvtav1".into(), "-preset".into(), "6".into(), "-crf".into(), crf.to_string()]); }
                "vp9" => { args.extend(["-c:v".into(), "libvpx-vp9".into(), "-b:v".into(), "0".into(), "-crf".into(), crf.to_string(), "-row-mt".into(), "1".into()]); }
                _ => { args.extend(["-c:v".into(), "libx264".into(), "-preset".into(), preset.into(), "-crf".into(), crf.to_string()]); }
            }
            args.extend(["-c:a".into(), "copy".into()]);
        }
        MediaType::Image => {
            match job.plan.codec.as_str() {
                "avif" => { args.extend(["-c:v".into(), "libaom-av1".into(), "-still-picture".into(), "1".into(), "-b:v".into(), "0".into(), "-crf".into(), "28".into()]); }
                "heic" | "heif" => { args.extend(["-c:v".into(), "libx265".into()]); }
                "webp" => { args.extend(["-c:v".into(), "libwebp".into(), "-q:v".into(), "80".into()]); }
                "png" => { args.extend(["-c:v".into(), "png".into()]); }
                "jpeg" | "jpg" => { args.extend(["-c:v".into(), "mjpeg".into(), "-q:v".into(), "2".into()]); }
                _ => { args.extend(["-c:v".into(), "png".into()]); }
            }
            args.extend(["-frames:v".into(), "1".into()]);
        }
    }
    args
}
