#!/bin/bash
# Debian server installer

# Ensure the script is run from the correct directory
cd "$(dirname "$0")" || exit

echo "--- Updating package list and installing dependencies ---"
sudo apt-get update
sudo apt-get install -y python3.10-venv ffmpeg

echo "--- Creating Python virtual environment ---"
python3 -m venv venv

echo "--- Installing Python packages ---"
# Activate venv, install, and then deactivate
source venv/bin/activate
pip install requests
deactivate

echo "--- Creating systemd service file ---"
# Note: $USER and $PWD will be resolved at the time the script is run
# This is crucial for the service to find the correct paths.
USER_WHO_RAN=$(logname)
WORKING_DIR=$(pwd)
EXEC_START="$WORKING_DIR/venv/bin/python $WORKING_DIR/server.py"

# Create the log file and set permissions
sudo touch /var/log/ffmpeg_server.log
sudo chown "$USER_WHO_RAN":"$USER_WHO_RAN" /var/log/ffmpeg_server.log

echo "[Unit]
Description=FFmpeg Encoding Server
After=network.target

[Service]
User=$USER_WHO_RAN
WorkingDirectory=$WORKING_DIR
ExecStart=$EXEC_START
StandardOutput=append:/var/log/ffmpeg_server.log
StandardError=append:/var/log/ffmpeg_server.log
Restart=always

[Install]
WantedBy=multi-user.target" | sudo tee /etc/systemd/system/ffmpeg-server.service

echo "--- Enabling and starting the service ---"
sudo systemctl daemon-reload
sudo systemctl enable ffmpeg-server
sudo systemctl start ffmpeg-server

echo "--- Installation Complete ---"
echo "The FFmpeg Encoding Server is now running."
echo "You can check its status with: sudo systemctl status ffmpeg-server"
echo "Logs are being written to: /var/log/ffmpeg_server.log" 