//outils liés à FFmpeg pour construire les arguments
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
//construction d'arguments FFmpeg basiques avec sélection optionnelle d'encodeur vidéo
pub fn build_ffmpeg_args(job: &Job) -> Vec<String> {
    build_ffmpeg_args_with_encoder(job, None)
}

//construction d'arguments FFmpeg avec possibilité de forcer l'encodeur vidéo
pub fn build_ffmpeg_args_with_encoder(job: &Job, selected_video_encoder: Option<&str>) -> Vec<String> {
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
            if let Some(enc) = selected_video_encoder {
                args.extend(["-c:v".into(), enc.to_string()]);
                //this part do that
                //paramètres génériques selon la famille de codec
                match job.plan.codec.as_str() {
                    "vp9" => { args.extend(["-b:v".into(), "0".into(), "-crf".into(), crf.to_string(), "-row-mt".into(), "1".into()]); }
                    "av1" => { args.extend(["-crf".into(), crf.to_string()]); }
                    _ => { args.extend(["-preset".into(), preset.into(), "-crf".into(), crf.to_string()]); }
                }
            } else {
                match job.plan.codec.as_str() {
                    "h264" => { args.extend(["-c:v".into(), "libx264".into(), "-preset".into(), preset.into(), "-crf".into(), crf.to_string()]); }
                    "h265" | "hevc" => { args.extend(["-c:v".into(), "libx265".into(), "-preset".into(), preset.into(), "-crf".into(), crf.to_string()]); }
                    "av1" => { args.extend(["-c:v".into(), "libsvtav1".into(), "-preset".into(), "6".into(), "-crf".into(), crf.to_string()]); }
                    "vp9" => { args.extend(["-c:v".into(), "libvpx-vp9".into(), "-b:v".into(), "0".into(), "-crf".into(), crf.to_string(), "-row-mt".into(), "1".into()]); }
                    _ => { args.extend(["-c:v".into(), "libx264".into(), "-preset".into(), preset.into(), "-crf".into(), crf.to_string()]); }
                }
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

//détermine les encodeurs requis (candidats) pour un job
//détermine les encodeurs requis (candidats) pour un job
pub fn required_encoders(job: &Job) -> Vec<&'static str> {
    match job.plan.media_type {
        MediaType::Audio => match job.plan.codec.as_str() {
            "flac" => vec!["flac"],
            "alac" => vec!["alac"],
            "aac" => vec!["aac"],
            "mp3" => vec!["libmp3lame", "mp3"],
            "opus" => vec!["libopus", "opus"],
            "ogg" | "vorbis" => vec!["libvorbis", "vorbis"],
            _ => vec!["aac"],
        },
        MediaType::Video => match job.plan.codec.as_str() {
            //this part do that
            //inclure les encodeurs matériels quand disponibles
            "h264" => vec!["h264_nvenc", "h264_qsv", "h264_videotoolbox", "libx264", "h264"],
            "h265" | "hevc" => vec!["hevc_nvenc", "hevc_qsv", "hevc_videotoolbox", "libx265", "hevc", "h265"],
            "av1" => vec!["av1_nvenc", "av1_qsv", "libsvtav1", "libaom-av1", "av1"],
            "vp9" => vec!["vp9_qsv", "libvpx-vp9", "vp9"],
            _ => vec!["libx264", "h264"],
        },
        MediaType::Image => match job.plan.codec.as_str() {
            "avif" => vec!["libaom-av1"],
            "heic" | "heif" => vec!["libx265"],
            "webp" => vec!["libwebp"],
            "png" => vec!["png"],
            "jpeg" | "jpg" => vec!["mjpeg"],
            _ => vec!["png"],
        },
    }
}

//sélectionne l'encodeur préféré compatible avec l'agent pour ce job
pub fn select_preferred_encoder(job: &Job, agent_encoders: &[String]) -> Option<String> {
    if !matches!(job.plan.media_type, MediaType::Video) { return None; }
    let candidates = required_encoders(job);
    for cand in candidates {
        if agent_encoders.iter().any(|e| e == cand) {
            return Some(cand.to_string());
        }
    }
    None
}
