#!/usr/bin/env python3
"""
FFmpeg Easy - Serveur d'Encodage Distribué
Point d'entrée principal du serveur
"""

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

# Add the shared directory to the path
sys.path.append(str(Path(__file__).resolve().parents[1] / 'ffmpeg-gui' / 'shared'))

from server.encode_server import EncodeServer
from server.config_manager import ServerConfig
from core.hardware_detector import detect_capabilities

def setup_logging(log_level: str, log_file: str = None):
    """Configure le système de logging"""
    level = getattr(logging, log_level.upper())
    
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

def signal_handler(server: EncodeServer):
    """Gestionnaire de signaux pour arrêt propre"""
    def handler(signum, frame):
        logging.info(f"🛑 Signal {signum} reçu, arrêt du serveur...")
        asyncio.create_task(server.stop())
    return handler

async def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(description="Serveur d'encodage FFmpeg Easy")
    
    parser.add_argument("--host", default="0.0.0.0", help="Adresse d'écoute")
    parser.add_argument("--port", type=int, default=8765, help="Port d'écoute")
    parser.add_argument("--max-jobs", type=int, default=2, help="Jobs simultanés maximum")
    parser.add_argument("--max-file-size", default="10GB", help="Taille fichier max")
    parser.add_argument("--name", help="Nom du serveur")
    parser.add_argument("--temp-dir", help="Répertoire temporaire")
    parser.add_argument("--config", help="Fichier de configuration")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", help="Fichier de log")
    parser.add_argument("--test-capabilities", action="store_true", help="Teste et affiche les capacités puis quitte")
    parser.add_argument("--validate-config", action="store_true", help="Valide la configuration puis quitte")
    
    args = parser.parse_args()
    
    setup_logging(args.log_level, args.log_file)
    
    config = ServerConfig.from_args(args)
    
    if args.validate_config:
        logging.info("✅ Configuration valide")
        return 0
    
    if args.test_capabilities:
        logging.info("🔍 Test des capacités du serveur...")
        capabilities = detect_capabilities()
        
        print("\n" + "="*60)
        print("CAPACITÉS DU SERVEUR")
        print("="*60)
        print(f"Hostname: {capabilities.hostname}")
        print(f"OS: {capabilities.os}")
        print(f"CPU: {capabilities.cpu_cores} cœurs")
        print(f"RAM: {capabilities.memory_gb} GB")
        print(f"Disque: {capabilities.disk_space_gb} GB libre")
        print(f"Performance: {capabilities.estimated_performance:.1f}")
        print(f"\nEncodeurs logiciels ({len(capabilities.software_encoders)}):")
        for encoder in capabilities.software_encoders:
            print(f"  ✓ {encoder}")
        print(f"\nEncodeurs matériels:")
        for vendor, encoders in capabilities.hardware_encoders.items():
            if encoders:
                print(f"  {vendor.upper()}: {', '.join(encoders)}")
        print(f"\nRésolution max: {capabilities.max_resolution}")
        print(f"Formats supportés: {len(capabilities.supported_formats)}")
        print("="*60)
        
        return 0
    
    try:
        server = EncodeServer(config)
        
        for sig in [signal.SIGINT, signal.SIGTERM]:
            signal.signal(sig, signal_handler(server))
        
        logging.info("🚀 Démarrage du serveur FFmpeg Easy...")
        logging.info(f"📍 Écoute sur {args.host}:{args.port}")
        logging.info(f"⚙️  Jobs max: {args.max_jobs}")
        
        await server.start()
        
    except KeyboardInterrupt:
        logging.info("🛑 Arrêt demandé par l'utilisateur")
        return 0
    except Exception as e:
        logging.error(f"❌ Erreur fatale: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
