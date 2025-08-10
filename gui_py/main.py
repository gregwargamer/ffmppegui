import sys
import os
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging
from logging.handlers import RotatingFileHandler
import time
import uuid
import secrets
import mimetypes
import subprocess

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

#this part do that
#configuration de base
PORT = int(os.environ.get("GUI_PORT", "4010"))
HOST = os.environ.get("GUI_HOST", "0.0.0.0")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{PORT}")
AGENT_TOKEN_FALLBACK = os.environ.get("AGENT_SHARED_TOKEN", "dev-token")
HEADLESS = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")

#this other part do that
#logger avec rotation
logs_dir = Path(__file__).parent / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
log_path = logs_dir / "python-gui.log"
logger = logging.getLogger("gui_py")
logger.setLevel(logging.DEBUG)
handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.debug("Python GUI starting")

#this part do that
#application FastAPI pour agents et conversions rapides
app = FastAPI()

# mémoire minimale (pairing codes + agents list)
ALLOWED_TOKENS = set([AGENT_TOKEN_FALLBACK])
AGENTS: Dict[str, Dict[str, Any]] = {}
JOBS: Dict[str, Dict[str, Any]] = {}
PENDING_JOBS: List[str] = []

#this part do that
#utilitaires communs

def now_ms() -> int:
    return int(time.time() * 1000)


def get_public_base_url() -> str:
    return PUBLIC_BASE_URL.rstrip('/')


def compute_output_ext(media_type: str, codec: str) -> str:
    if media_type == "audio":
        m = {"flac": ".flac", "alac": ".m4a", "aac": ".m4a", "mp3": ".mp3", "opus": ".opus", "ogg": ".ogg", "vorbis": ".ogg"}
        return m.get(codec, ".m4a")
    if media_type == "video":
        m = {"h264": ".mp4", "h265": ".mp4", "hevc": ".mp4", "av1": ".mkv", "vp9": ".webm"}
        return m.get(codec, ".mp4")
    m = {"avif": ".avif", "heic": ".heic", "heif": ".heif", "webp": ".webp", "png": ".png", "jpeg": ".jpg", "jpg": ".jpg"}
    return m.get(codec, ".png")


def build_ffmpeg_args(job: Dict[str, Any]) -> List[str]:
    media_type = job.get("mediaType")
    codec = job.get("codec")
    options = job.get("options") or {}
    args: List[str] = []
    args += ["-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-loglevel", "error"]
    if media_type == "audio":
        args.append("-vn")
        if codec == "flac": args += ["-c:a", "flac"]
        elif codec == "alac": args += ["-c:a", "alac"]
        elif codec == "aac": args += ["-c:a", "aac", "-b:a", str(options.get("bitrate", "192k"))]
        elif codec == "mp3": args += ["-c:a", "libmp3lame", "-b:a", str(options.get("bitrate", "192k"))]
        elif codec == "opus": args += ["-c:a", "libopus", "-b:a", str(options.get("bitrate", "160k"))]
        elif codec in ("ogg", "vorbis"): args += ["-c:a", "libvorbis", "-q:a", str(options.get("q", 5))]
        else: args += ["-c:a", "aac", "-b:a", str(options.get("bitrate", "192k"))]
    elif media_type == "video":
        crf = options.get("crf", 23 if codec == "h264" else 28 if codec in ("h265", "hevc") else 32 if codec == "av1" else 23)
        preset = str(options.get("preset", "medium"))
        args += ["-pix_fmt", "yuv420p"]
        if codec == "h264": args += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
        elif codec in ("h265", "hevc"): args += ["-c:v", "libx265", "-preset", preset, "-crf", str(crf)]
        elif codec == "av1": args += ["-c:v", "libsvtav1", "-preset", str(options.get("svtPreset", 6)), "-crf", str(crf)]
        elif codec == "vp9": args += ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf), "-row-mt", "1"]
        else: args += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
        if options.get("audioCopy", True) is False:
            args += ["-c:a", "aac", "-b:a", str(options.get("audioBitrate", "160k"))]
        else:
            args += ["-c:a", "copy"]
    else:
        if codec == "avif": args += ["-c:v", "libaom-av1", "-still-picture", "1", "-b:v", "0", "-crf", str(options.get("crf", 28))]
        elif codec in ("heic", "heif"): args += ["-c:v", "libx265"]
        elif codec == "webp":
            if options.get("lossless"): args += ["-c:v", "libwebp", "-lossless", "1"]
            else: args += ["-c:v", "libwebp", "-q:v", str(options.get("quality", 80))]
        elif codec == "png": args += ["-c:v", "png"]
        elif codec in ("jpeg", "jpg"): args += ["-c:v", "mjpeg", "-q:v", str(options.get("quality", 2))]
        else: args += ["-c:v", "png"]
        args += ["-frames:v", "1"]
    return args


async def scan_files(root: str, recursive: bool, media_type: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    exts_audio = {'.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg', '.opus', '.wma', '.aiff', '.alac'}
    exts_video = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v'}
    exts_image = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.bmp', '.heic', '.heif', '.avif'}

    async def walk(dir_path: str):
        try:
            for entry in os.scandir(dir_path):
                full = entry.path
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        await walk(full)
                elif entry.is_file(follow_symlinks=False):
                    ext = os.path.splitext(entry.name)[1].lower()
                    if (media_type == 'audio' and ext in exts_audio) or (media_type == 'video' and ext in exts_video) or (media_type == 'image' and ext in exts_image):
                        try:
                            st = os.stat(full)
                            results.append({"path": full, "size": st.st_size})
                        except Exception as e:
                            logger.debug(f"stat fail {full}: {e}")
        except Exception as e:
            logger.debug(f"scan error {dir_path}: {e}")

    await walk(root)
    return results

@app.get("/api/settings")
def api_settings():
    logger.debug("/api/settings")
    return {"publicBaseUrl": get_public_base_url()}

@app.post("/api/pair")
def api_pair(payload: Dict[str, Any]):
    logger.debug("/api/pair %s", payload)
    token = (payload or {}).get("token", "").strip()
    if not token or len(token) != 25:
        return JSONResponse({"error": "invalid token"}, status_code=400)
    ALLOWED_TOKENS.add(token)
    return {"ok": True}

@app.post("/api/settings")
def api_settings_update(payload: Dict[str, Any]):
    global PUBLIC_BASE_URL
    val = (payload or {}).get("publicBaseUrl", "").strip()
    if not val.startswith("http"):
        return JSONResponse({"error": "invalid URL"}, status_code=400)
    PUBLIC_BASE_URL = val.rstrip('/')
    logger.info("publicBaseUrl updated to %s", PUBLIC_BASE_URL)
    return {"ok": True, "publicBaseUrl": PUBLIC_BASE_URL}

@app.get("/api/nodes")
def api_nodes():
    logger.debug("/api/nodes")
    lst = [a.get("info", {}) for a in AGENTS.values()]
    totals = {
        "totalJobs": len(JOBS),
        "pendingJobs": len(PENDING_JOBS),
        "runningJobs": sum(a.get("info", {}).get("activeJobs", 0) for a in AGENTS.values())
    }
    return {"agents": lst, "totals": totals}

#API scan pour préparer un plan de jobs
@app.post("/api/scan")
async def api_scan(payload: Dict[str, Any]):
    try:
        input_root = os.path.abspath((payload or {}).get("inputRoot", "").strip())
        output_root = os.path.abspath((payload or {}).get("outputRoot", "").strip())
        recursive = bool((payload or {}).get("recursive", True))
        mirror = bool((payload or {}).get("mirrorStructure", True))
        media_type = (payload or {}).get("mediaType", "")
        codec = (payload or {}).get("codec", "")
        options = (payload or {}).get("options", {}) or {}
        if not input_root or not output_root or media_type not in ("audio","video","image") or not codec:
            return JSONResponse({"error": "invalid request"}, status_code=400)
        if not os.path.isdir(input_root):
            return JSONResponse({"error": "inputRoot not found"}, status_code=400)
        os.makedirs(output_root, exist_ok=True)
        files = await scan_files(input_root, recursive, media_type)
        out_ext = compute_output_ext(media_type, codec)
        plan: List[Dict[str, Any]] = []
        total_size = 0
        for f in files:
            src = f["path"]; size = int(f["size"]); total_size += size
            rel = os.path.relpath(src, input_root)
            base = os.path.join(output_root, rel) if mirror else os.path.join(output_root, os.path.basename(rel))
            out_path = os.path.splitext(base)[0] + out_ext
            plan.append({"sourcePath": src, "relativePath": rel, "mediaType": media_type, "sizeBytes": size, "outputPath": out_path, "codec": codec, "options": options})
        logger.info("scan %s files total=%s bytes", len(plan), total_size)
        return {"count": len(plan), "totalBytes": total_size, "jobs": plan}
    except Exception as e:
        logger.exception("scan error")
        return JSONResponse({"error": str(e)}, status_code=500)

#API start pour accepter et mettre en file d'attente les jobs
@app.post("/api/start")
async def api_start(payload: Dict[str, Any]):
    jobs_in = (payload or {}).get("jobs", [])
    if not isinstance(jobs_in, list) or not jobs_in:
        return JSONResponse({"error": "no jobs"}, status_code=400)
    accepted = 0
    for pj in jobs_in:
        jid = str(uuid.uuid4())
        job = {**pj, "id": jid, "status": "pending", "inputToken": secrets.token_hex(16), "outputToken": secrets.token_hex(16), "createdAt": now_ms(), "updatedAt": now_ms(), "nodeId": None}
        JOBS[jid] = job
        PENDING_JOBS.append(jid)
        accepted += 1
    logger.info("start accepted jobs=%s", accepted)
    await try_dispatch()
    return {"accepted": accepted}

#flux de téléchargement du fichier source avec gestion du Range
@app.get("/stream/input/{job_id}")
async def stream_input(job_id: str, request: Request, token: Optional[str] = None):
    job = JOBS.get(job_id)
    if not job or token != job.get("inputToken"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    src = job.get("sourcePath")
    if not src or not os.path.isfile(src):
        return JSONResponse({"error": "not found"}, status_code=404)
    file_size = os.path.getsize(src)
    range_header = request.headers.get('range', '')
    ctype = mimetypes.guess_type(src)[0] or 'application/octet-stream'

    def file_iter(start: int, end: int, chunk_size: int = 1024 * 1024):
        with open(src, 'rb') as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    if range_header.startswith('bytes='):
        try:
            start_s, end_s = range_header.replace('bytes=', '').split('-')
            start = int(start_s or 0)
            end = int(end_s) if end_s else file_size - 1
        except Exception:
            start, end = 0, file_size - 1
        length = end - start + 1
        headers = {
            'Content-Range': f'bytes {start}-{end}/{file_size}',
            'Accept-Ranges': 'bytes',
            'Content-Length': str(length),
            'Content-Type': ctype,
        }
        return StreamingResponse(file_iter(start, end), status_code=206, headers=headers)

    headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(file_size), 'Content-Type': ctype}
    return StreamingResponse(file_iter(0, file_size - 1), status_code=200, headers=headers)

#flux de réception du fichier encodé
@app.put("/stream/output/{job_id}")
async def stream_output(job_id: str, token: Optional[str] = None, request: Request = None):
    job = JOBS.get(job_id)
    if not job or token != job.get("outputToken"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    out_path = job.get("outputPath")
    if not out_path:
        return JSONResponse({"error": "no output path"}, status_code=400)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = out_path + ".part"

    async def write_body():
        with open(tmp_path, 'wb') as w:
            async for chunk in request.stream():
                if chunk:
                    w.write(chunk)
        os.replace(tmp_path, out_path)

    await write_body()
    job["status"] = "completed"
    job["updatedAt"] = now_ms()
    logger.info("upload completed job=%s", job_id)
    return {"ok": True}

#upload de fichiers locaux vers un dossier de destination
@app.post("/api/upload")
async def api_upload(dest: str = Form(...), files: List[UploadFile] = File(...)):
    if not dest:
        return JSONResponse({"error": "missing dest"}, status_code=400)
    os.makedirs(dest, exist_ok=True)
    saved = 0
    for up in files:
        try:
            out_path = os.path.join(dest, os.path.basename(up.filename))
            with open(out_path, 'wb') as w:
                while True:
                    chunk = await up.read(1024 * 1024)
                    if not chunk:
                        break
                    w.write(chunk)
            saved += 1
        except Exception as e:
            logger.debug("upload save error %s", e)
    return {"ok": True, "count": saved}

#boîte de dialogue native côté serveur (best-effort)
@app.post("/api/pick")
async def api_pick(payload: Dict[str, Any]):
    kind = (payload or {}).get("type", "dir")
    try:
        if sys.platform == 'darwin':
            if kind == 'dir':
                script = 'choose folder with prompt "Select a folder"\nPOSIX path of result'
            else:
                script = 'choose file with prompt "Select file(s)" with multiple selections allowed\nset p to {}\nrepeat with f in result\nset end of p to POSIX path of f\nend repeat\nreturn p as string'
            out = subprocess.check_output(['osascript', '-e', script]).decode().strip()
            paths = [s.strip() for s in out.split(',') if s.strip()]
            if not paths:
                return JSONResponse({"error": "no selection"}, status_code=400)
            return {"paths": paths}
        elif sys.platform.startswith('linux'):
            args = ['--file-selection'] + (['--directory'] if kind == 'dir' else ['--multiple', '--separator=::'])
            try:
                out = subprocess.check_output(['zenity', *args]).decode()
            except Exception:
                return JSONResponse({"error": "picker unavailable"}, status_code=400)
            sep = '\n' if kind == 'dir' else '::'
            paths = [s.strip() for s in out.split(sep) if s.strip()]
            if not paths:
                return JSONResponse({"error": "no selection"}, status_code=400)
            return {"paths": paths}
    except Exception as e:
        logger.debug("pick error %s", e)
    return JSONResponse({"error": "picker unsupported"}, status_code=400)

#gestion websocket d'agent (/agent)
@app.websocket("/agent")
async def agent_socket(ws: WebSocket):
    await ws.accept()
    agent_id: Optional[str] = None
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            payload = msg.get("payload") or {}
            if mtype == "register":
                token = (payload.get("token") or "").strip()
                if not token or token not in ALLOWED_TOKENS:
                    await ws.close(code=1008)
                    return
                agent_id = payload.get("id") or str(uuid.uuid4())
                info = {
                    "id": agent_id,
                    "name": payload.get("name") or f"agent-{agent_id[:6]}",
                    "concurrency": int(max(1, int(payload.get("concurrency") or 1))),
                    "encoders": list(payload.get("encoders") or []),
                    "activeJobs": 0,
                    "lastHeartbeat": now_ms(),
                }
                AGENTS[agent_id] = {"info": info, "ws": ws}
                logger.info("agent registered id=%s", agent_id)
                await try_dispatch()
            elif mtype == "heartbeat":
                aid = payload.get("id")
                rec = AGENTS.get(aid or agent_id or "")
                if rec:
                    rec["info"]["lastHeartbeat"] = now_ms()
                    if isinstance(payload.get("activeJobs"), int):
                        rec["info"]["activeJobs"] = int(payload.get("activeJobs"))
            elif mtype == "lease-accepted":
                job_id = payload.get("jobId")
                job = JOBS.get(job_id or "")
                if job:
                    job["status"] = "running"
                    job["updatedAt"] = now_ms()
            elif mtype == "progress":
                pass
            elif mtype == "complete":
                job_id = payload.get("jobId")
                success = bool(payload.get("success"))
                aid = payload.get("agentId") or agent_id
                rec = AGENTS.get(aid or "")
                job = JOBS.get(job_id or "")
                if rec and rec.get("info", {}).get("activeJobs", 0) > 0:
                    rec["info"]["activeJobs"] -= 1
                if job:
                    job["status"] = "uploaded" if success else "failed"
                    job["updatedAt"] = now_ms()
                await try_dispatch()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("agent socket error %s", e)
    finally:
        if agent_id and agent_id in AGENTS:
            try:
                del AGENTS[agent_id]
            except Exception:
                pass
        try:
            await ws.close()
        except Exception:
            pass

#dispatch des jobs vers agents disponibles
async def try_dispatch():
    try:
        # tri: plus gros fichiers en premier
        PENDING_JOBS.sort(key=lambda jid: JOBS.get(jid, {}).get("sizeBytes", 0), reverse=True)
        made_progress = True
        while made_progress:
            made_progress = False
            # boucle sur agents pour respecter leur capacité
            for aid, rec in list(AGENTS.items()):
                info = rec.get("info", {})
                free = int(info.get("concurrency", 1)) - int(info.get("activeJobs", 0))
                if free <= 0:
                    continue
                if not PENDING_JOBS:
                    break
                jid = PENDING_JOBS.pop(0)
                job = JOBS.get(jid)
                if not job:
                    continue
                base = get_public_base_url()
                input_url = f"{base}/stream/input/{jid}?token={job['inputToken']}"
                output_url = f"{base}/stream/output/{jid}?token={job['outputToken']}"
                ff_args = build_ffmpeg_args(job)
                out_ext = os.path.splitext(job.get("outputPath") or "")[1] or compute_output_ext(job.get("mediaType"), job.get("codec"))
                job["status"] = "assigned"
                job["nodeId"] = aid
                job["updatedAt"] = now_ms()
                info["activeJobs"] = int(info.get("activeJobs", 0)) + 1
                try:
                    await rec["ws"].send_json({"type": "lease", "payload": {"jobId": jid, "inputUrl": input_url, "outputUrl": output_url, "ffmpegArgs": ff_args, "outputExt": out_ext, "threads": 0}})
                    made_progress = True
                except Exception as e:
                    logger.debug("send lease failed %s", e)
                    # échec d'envoi: remettre en file d'attente
                    info["activeJobs"] = max(0, int(info.get("activeJobs", 0)) - 1)
                    PENDING_JOBS.insert(0, jid)
    except Exception as e:
        logger.debug("dispatch error %s", e)

#fenêtre Qt et serveur intégrés (désactivés en mode headless)
if not HEADLESS:
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
        QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QFileDialog, QSpinBox,
        QTableWidget, QTableWidgetItem, QMessageBox
    )

    #serveur uvicorn dans thread dédié
    class ServerThread(QtCore.QThread):
        def run(self):
            logger.debug("uvicorn server starting")
            config = uvicorn.Config(app=app, host=HOST, port=PORT, log_level="info")
            server = uvicorn.Server(config)
            asyncio.run(server.serve())

    #fenêtre principale Qt native
    class MainWindow(QMainWindow):
        poll_timer: QtCore.QTimer

        def __init__(self):
            super().__init__()
            self.setWindowTitle("Distributed FFmpeg Controller (Python)")
            self.resize(1100, 800)

            #sections
            root = QWidget(); layout = QVBoxLayout(root)

            # Pairing section
            pair_row1 = QHBoxLayout()
            pair_row2 = QHBoxLayout()
            layout.addLayout(pair_row1)
            layout.addLayout(pair_row2)
            pair_row1.addWidget(QLabel("Controller URL:"))
            self.public_url = QLineEdit(PUBLIC_BASE_URL)
            pair_row1.addWidget(self.public_url)
            pair_row2.addWidget(QLabel("Pairing code:"))
            self.pair_code = QLineEdit()
            self.btn_pair = QPushButton("Pair")
            pair_row2.addWidget(self.pair_code)
            pair_row2.addWidget(self.btn_pair)

            # Paths section
            path_row1 = QHBoxLayout(); layout.addLayout(path_row1)
            path_row1.addWidget(QLabel("Input root:"))
            self.input_root = QLineEdit()
            self.btn_pick_in = QPushButton("Pick…")
            path_row1.addWidget(self.input_root)
            path_row1.addWidget(self.btn_pick_in)

            path_row2 = QHBoxLayout(); layout.addLayout(path_row2)
            path_row2.addWidget(QLabel("Output root:"))
            self.output_root = QLineEdit()
            self.btn_pick_out = QPushButton("Pick…")
            path_row2.addWidget(self.output_root)
            path_row2.addWidget(self.btn_pick_out)

            flags_row = QHBoxLayout(); layout.addLayout(flags_row)
            self.chk_recursive = QCheckBox("Recursive")
            self.chk_recursive.setChecked(True)
            self.chk_mirror = QCheckBox("Mirror structure")
            self.chk_mirror.setChecked(True)
            flags_row.addWidget(self.chk_recursive)
            flags_row.addWidget(self.chk_mirror)

            # Media & Codec section
            media_row = QHBoxLayout(); layout.addLayout(media_row)
            media_row.addWidget(QLabel("Media:"))
            self.cmb_media = QComboBox(); self.cmb_media.addItems(["audio", "video", "image"]) 
            media_row.addWidget(self.cmb_media)
            media_row.addWidget(QLabel("Codec:"))
            self.cmb_codec = QComboBox(); media_row.addWidget(self.cmb_codec)

            quality_row = QHBoxLayout(); layout.addLayout(quality_row)
            self.lbl_crf = QLabel("CRF/Quality:")
            self.spn_crf = QSpinBox(); self.spn_crf.setRange(0, 63); self.spn_crf.setValue(23)
            quality_row.addWidget(self.lbl_crf)
            quality_row.addWidget(self.spn_crf)

            bitrate_row = QHBoxLayout(); layout.addLayout(bitrate_row)
            self.lbl_bitrate = QLabel("Bitrate:")
            self.ed_bitrate = QLineEdit("192k")
            bitrate_row.addWidget(self.lbl_bitrate)
            bitrate_row.addWidget(self.ed_bitrate)

            preset_row = QHBoxLayout(); layout.addLayout(preset_row)
            self.lbl_preset = QLabel("Preset:")
            self.cmb_preset = QComboBox(); self.cmb_preset.addItems(["veryslow","slower","slow","medium","fast","faster","veryfast"]) 
            preset_row.addWidget(self.lbl_preset)
            preset_row.addWidget(self.cmb_preset)

            actions_row = QHBoxLayout(); layout.addLayout(actions_row)
            self.btn_scan = QPushButton("Scan")
            self.btn_start = QPushButton("Start")
            self.btn_start.setEnabled(False)
            actions_row.addWidget(self.btn_scan)
            actions_row.addWidget(self.btn_start)

            self.lbl_summary = QLabel("")
            layout.addWidget(self.lbl_summary)

            # Nodes table
            layout.addWidget(QLabel("Nodes"))
            self.tbl_nodes = QTableWidget(0, 4)
            self.tbl_nodes.setHorizontalHeaderLabels(["Name","Concurrency","Active","Last heartbeat"])
            layout.addWidget(self.tbl_nodes)

            self.setCentralWidget(root)

            # events
            self.cmb_media.currentTextChanged.connect(self.fill_codecs)
            self.cmb_codec.currentTextChanged.connect(self.update_fields)
            self.btn_pick_in.clicked.connect(self.pick_input)
            self.btn_pick_out.clicked.connect(self.pick_output)
            self.btn_pair.clicked.connect(self.do_pair)
            self.btn_scan.clicked.connect(self.do_scan)
            self.btn_start.clicked.connect(self.do_start)

            # init
            self.last_plan = None
            self.fill_codecs()
            self.update_fields()

            # polling nodes
            self.poll_timer = QtCore.QTimer(self)
            self.poll_timer.timeout.connect(self.refresh_nodes)
            self.poll_timer.start(1500)

        #helpers
        def fill_codecs(self):
            media = self.cmb_media.currentText()
            self.cmb_codec.clear()
            if media == "audio":
                self.cmb_codec.addItems(["flac","alac","aac","mp3","opus","ogg"])
            elif media == "video":
                self.cmb_codec.addItems(["h264","h265","av1","vp9"])
            else:
                self.cmb_codec.addItems(["avif","heic","webp","png","jpeg"]) 

        def update_fields(self):
            media = self.cmb_media.currentText(); codec = self.cmb_codec.currentText()
            show_preset = (media == "video")
            show_crf = (media == "video") or (media == "image" and codec in ("avif","webp"))
            show_bitrate = (media == "audio" and codec in ("aac","mp3","opus"))
            self.lbl_preset.setVisible(show_preset); self.cmb_preset.setVisible(show_preset)
            self.lbl_crf.setVisible(show_crf); self.spn_crf.setVisible(show_crf)
            self.lbl_bitrate.setVisible(show_bitrate); self.ed_bitrate.setVisible(show_bitrate)

        def pick_input(self):
            d = QFileDialog.getExistingDirectory(self, "Select input folder"); logger.debug("pick_input %s", d)
            if d: self.input_root.setText(d)

        def pick_output(self):
            d = QFileDialog.getExistingDirectory(self, "Select output folder"); logger.debug("pick_output %s", d)
            if d: self.output_root.setText(d)

        def do_pair(self):
            import requests
            url = self.public_url.text().strip(); token = self.pair_code.text().strip(); logger.debug("do_pair url=%s tokenlen=%s", url, len(token))
            if not url.startswith("http"):
                QMessageBox.warning(self, "Error", "Enter a valid controller URL")
                return
            if len(token) != 25:
                QMessageBox.warning(self, "Error", "Token must be 25 characters")
                return
            try:
                r1 = requests.post(f"http://localhost:{PORT}/api/settings", json={"publicBaseUrl": url}, timeout=5)
                if r1.status_code >= 300:
                    QMessageBox.warning(self, "Error", r1.text)
                    return
                r2 = requests.post(f"http://localhost:{PORT}/api/pair", json={"token": token}, timeout=5)
                if r2.status_code >= 300:
                    QMessageBox.warning(self, "Error", r2.text)
                    return
                QMessageBox.information(self, "OK", "Paired and URL saved")
            except Exception as e: QMessageBox.warning(self, "Error", str(e))

        def do_scan(self):
            import requests
            payload = {
                "inputRoot": self.input_root.text().strip(),
                "outputRoot": self.output_root.text().strip(),
                "recursive": self.chk_recursive.isChecked(),
                "mirrorStructure": self.chk_mirror.isChecked(),
                "mediaType": self.cmb_media.currentText(),
                "codec": self.cmb_codec.currentText(),
                "options": { "crf": self.spn_crf.value(), "preset": self.cmb_preset.currentText(), "bitrate": self.ed_bitrate.text().strip() }
            }
            logger.debug("do_scan %s", payload)
            try:
                self.lbl_summary.setText("Scanning…")
                r = requests.post(f"http://localhost:{PORT}/api/scan", json=payload, timeout=30)
                j = r.json()
                if r.status_code >= 300:
                    self.lbl_summary.setText(j.get('error') or 'Scan failed')
                    self.btn_start.setEnabled(False)
                    return
                self.last_plan = j
                mib = (j.get('totalBytes', 0) / (1024*1024))
                self.lbl_summary.setText(f"{j.get('count',0)} files, total {mib:.1f} MiB")
                self.btn_start.setEnabled(j.get('count', 0) > 0)
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

        def do_start(self):
            import requests
            try:
                if not self.last_plan or not self.last_plan.get('jobs'):
                    QMessageBox.information(self, "Start", "Scan first")
                    return
                r = requests.post(f"http://localhost:{PORT}/api/start", json={"jobs": self.last_plan['jobs']}, timeout=15)
                j = r.json()
                if r.status_code >= 300:
                    QMessageBox.warning(self, "Error", j.get('error') or 'Start failed')
                    return
                QMessageBox.information(self, "Start", f"Accepted {j.get('accepted',0)} jobs")
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

        def refresh_nodes(self):
            import requests
            try:
                r = requests.get(f"http://localhost:{PORT}/api/nodes", timeout=3)
                if r.status_code >= 300:
                    return
                data = r.json()
                agents = data.get("agents", [])
                self.tbl_nodes.setRowCount(len(agents))
                for i, a in enumerate(agents):
                    self.tbl_nodes.setItem(i, 0, QTableWidgetItem(str(a.get("name",""))))
                    self.tbl_nodes.setItem(i, 1, QTableWidgetItem(str(a.get("concurrency",0))))
                    self.tbl_nodes.setItem(i, 2, QTableWidgetItem(str(a.get("activeJobs",0))))
                    hb_ms = a.get("lastHeartbeat") or 0
                    self.tbl_nodes.setItem(i, 3, QTableWidgetItem(str(hb_ms)))
            except Exception as e:
                logger.debug("refresh_nodes error %s", e)

#bootstrap app + server

def main():
    if HEADLESS:
        logger.info("starting in HEADLESS mode on %s:%s", HOST, PORT)
        uvicorn.run(app=app, host=HOST, port=PORT, log_level="info")
        return
    from PySide6.QtWidgets import QApplication
    qt = QApplication(sys.argv)
    server_thread = ServerThread(); server_thread.start()
    win = MainWindow(); win.show()
    code = qt.exec(); logger.debug("GUI exited code=%s", code)
    sys.exit(code)

if __name__ == "__main__":
    main()
