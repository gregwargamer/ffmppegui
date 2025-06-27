FROM python:3.11-slim

# Installer FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copier le code
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Port d'écoute
EXPOSE 8765

# Lancer le serveur
CMD ["python", "main.py", "--port", "8765", "--max-jobs", "2"]
