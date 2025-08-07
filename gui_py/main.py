import sys
import os
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from PySide6 import QtCore, QtWidgets
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QFileDialog, QSpinBox,
    QTableWidget, QTableWidgetItem, QMessageBox
)

#this part do that
#configuration de base
PORT = int(os.environ.get("GUI_PORT", "4010"))
HOST = os.environ.get("GUI_HOST", "0.0.0.0")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{PORT}")
AGENT_TOKEN_FALLBACK = os.environ.get("AGENT_SHARED_TOKEN", "dev-token")

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

@app.get("/api/settings")
def api_settings():
    logger.debug("/api/settings")
    return {"publicBaseUrl": PUBLIC_BASE_URL}

@app.post("/api/pair")
def api_pair(payload: Dict[str, Any]):
    logger.debug("/api/pair %s", payload)
    token = (payload or {}).get("token", "").strip()
    if not token or len(token) != 25:
        return JSONResponse({"error": "invalid token"}, status_code=400)
    ALLOWED_TOKENS.add(token)
    return {"ok": True}

@app.get("/api/nodes")
def api_nodes():
    logger.debug("/api/nodes")
    lst = list(AGENTS.values())
    totals = {
        "totalJobs": 0,
        "pendingJobs": 0,
        "runningJobs": sum(a.get("activeJobs", 0) for a in AGENTS.values())
    }
    return {"agents": lst, "totals": totals}

#this part do that
#serveur uvicorn dans thread
class ServerThread(QtCore.QThread):
    def run(self):
        logger.debug("uvicorn server starting")
        config = uvicorn.Config(app=app, host=HOST, port=PORT, log_level="info")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

#this other part do that
#fenêtre principale Qt native
class MainWindow(QMainWindow):
    poll_timer: QtCore.QTimer

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Distributed FFmpeg Controller (Python)")
        self.resize(1100, 800)

        #this part do that
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
        self.fill_codecs()
        self.update_fields()

        # polling nodes
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.refresh_nodes)
        self.poll_timer.start(1500)

    #this other part do that
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
            r = requests.post(f"http://localhost:{PORT}/api/pair", json={"token": token}, timeout=5)
            if r.status_code >= 300:
                QMessageBox.warning(self, "Error", r.text)
                return
            QMessageBox.information(self, "OK", "Paired")
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
            # assuming parity endpoint exists on server web controller; for desktop-only, we would implement here
            self.lbl_summary.setText("Scanning…")
            # Placeholder feedback; full scan via Python could be added next
            self.lbl_summary.setText("Scan prepared (implement server or local scan)")
            self.btn_start.setEnabled(True)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def do_start(self):
        logger.debug("do_start clicked")
        QMessageBox.information(self, "Start", "Starting jobs (implement server/local dispatch)")

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
                self.tbl_nodes.setItem(i, 3, QTableWidgetItem(""))
        except Exception as e:
            logger.debug("refresh_nodes error %s", e)

#this part do that
#bootstrap app + server
def main():
    qt = QApplication(sys.argv)
    server_thread = ServerThread(); server_thread.start()
    win = MainWindow(); win.show()
    code = qt.exec(); logger.debug("GUI exited code=%s", code)
    sys.exit(code)

if __name__ == "__main__":
    main()
