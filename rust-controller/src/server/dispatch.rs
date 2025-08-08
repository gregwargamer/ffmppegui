//logique de dispatch des jobs vers les agents
use std::sync::Arc;
use crate::state::AppState;
use crate::ffmpeg::{build_ffmpeg_args, compute_output_ext};

//tentative de distribution (bouchon)
pub async fn try_dispatch(state: Arc<AppState>) {
    let mut made_progress = true;
    while made_progress {
        made_progress = false;
        //tri simple: LIFO pour l'instant (peut évoluer en tri par taille)
        let next_job_id_opt = { state.pending_jobs.write().await.pop() };
        let Some(job_id) = next_job_id_opt else { break };

        let (agent_id, input_url, output_url, args, output_ext) = {
            let jobs = state.jobs.read().await;
            let job = match jobs.get(&job_id) { Some(j) => j.clone(), None => continue };
            //choix naïf: premier agent avec capacité disponible
            let mut chosen: Option<String> = None;
            {
                let agents = state.agents.read().await;
                for (id, info) in agents.iter() {
                    if info.concurrency > info.active_jobs { chosen = Some(id.clone()); break; }
                }
            }
            let Some(agent_id) = chosen else { state.pending_jobs.write().await.push(job_id.clone()); break };
            let base = state.public_base_url.read().await.clone();
            let input_url = format!("{}/stream/input/{}?token={}", base.trim_end_matches('/'), urlencoding::encode(&job.id), job.input_token);
            let output_url = format!("{}/stream/output/{}?token={}", base.trim_end_matches('/'), urlencoding::encode(&job.id), job.output_token);
            let args = build_ffmpeg_args(&job);
            let output_ext = compute_output_ext(&job.plan.media_type, &job.plan.codec);
            (agent_id, input_url, output_url, args, output_ext)
        };

        //envoi au canal de l'agent
        if let Some(tx) = state.agent_channels.read().await.get(&agent_id) {
            let payload = serde_json::json!({
                "type": "lease",
                "payload": {
                    "jobId": job_id,
                    "inputUrl": input_url,
                    "outputUrl": output_url,
                    "ffmpegArgs": args,
                    "outputExt": output_ext,
                    "threads": 0
                }
            }).to_string();
            let _ = tx.send(payload);
            //incrémenter activeJobs
            if let Some(info) = state.agents.write().await.get_mut(&agent_id) { info.active_jobs += 1; }
            made_progress = true;
        } else {
            //pas de canal: remettre en file
            state.pending_jobs.write().await.push(job_id);
        }
    }
}
