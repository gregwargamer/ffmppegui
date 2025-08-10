//logique de dispatch des jobs vers les agents
use std::sync::Arc;
use crate::state::AppState;
use crate::ffmpeg::{build_ffmpeg_args_with_encoder, compute_output_ext, required_encoders, select_preferred_encoder};

//tentative de distribution (bouchon)
pub async fn try_dispatch(state: Arc<AppState>) {
    let mut made_progress = true;
    while made_progress {
        made_progress = false;

        //FIFO: dépiler en tête, et si non dispatchable, re-empiler en queue
        let next_job_id_opt = { state.pending_jobs.write().await.pop_front() };
        let Some(job_id) = next_job_id_opt else { break };

        //lecture snapshot du job
        let job_opt = { state.jobs.read().await.get(&job_id).cloned() };
        let Some(job) = job_opt else { continue };

        //encodeurs requis pour ce job
        let needed = required_encoders(&job);

        //sélection d'agent: capacité disponible, et encoders compatibles, puis plus faible charge
        let selected_agent = {
            let agents = state.agents.read().await;
            agents
                .iter()
                .filter(|(_id, info)| info.concurrency > info.active_jobs)
                .filter(|(_id, info)| needed.iter().any(|enc| info.encoders.iter().any(|have| have == enc)))
                .min_by_key(|(_id, info)| info.active_jobs)
                .map(|(id, _)| id.clone())
        };

        let Some(agent_id) = selected_agent else {
            //pas d'agent disponible/compatible: remettre en queue
            state.pending_jobs.write().await.push_back(job_id);
            break;
        };

        //construction des URLs et arguments
        let base = state.public_base_url.read().await.clone();
        let input_url = format!(
            "{}/stream/input/{}?token={}",
            base.trim_end_matches('/'),
            urlencoding::encode(&job.id),
            job.input_token
        );
        let output_url = format!(
            "{}/stream/output/{}?token={}",
            base.trim_end_matches('/'),
            urlencoding::encode(&job.id),
            job.output_token
        );
        //choisir l'encodeur vidéo préféré pour cet agent
        let selected_encoder = if let Some(info) = state.agents.read().await.get(&agent_id) {
            select_preferred_encoder(&job, &info.encoders)
        } else { None };
        let mut args = build_ffmpeg_args_with_encoder(&job, selected_encoder.as_deref());
        //this other part do that
        //appliquer quelques options avancées si présentes dans le plan (parité TS basique)
        if let Some(opts) = job.plan.options.as_ref() {
            if let Some(audio_copy) = opts.get("audioCopy").and_then(|v| v.as_bool()) {
                if !audio_copy {
                    //remplacer -c:a copy par -c:a aac -b:a 160k
                    let mut i = 0;
                    while i + 1 < args.len() {
                        if args[i] == "-c:a" && args[i+1] == "copy" { args[i+1] = "aac".into(); }
                        i += 1;
                    }
                    args.extend(["-b:a".into(), opts.get("audioBitrate").and_then(|v| v.as_str()).unwrap_or("160k").to_string()]);
                }
            }
        }
        let output_ext = compute_output_ext(&job.plan.media_type, &job.plan.codec);

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

            //incrémenter la charge
            if let Some(info) = state.agents.write().await.get_mut(&agent_id) { info.active_jobs += 1; }

            //mettre à jour l'état du job
            if let Some(job) = state.jobs.write().await.get_mut(&job_id) {
                job.status = "assigned".to_string();
                job.node_id = Some(agent_id.clone());
                job.updated_at = chrono::Utc::now().timestamp_millis();
            }
            made_progress = true;
        } else {
            //agent non joignable: remettre en queue
            state.pending_jobs.write().await.push_back(job_id);
        }
    }
}
