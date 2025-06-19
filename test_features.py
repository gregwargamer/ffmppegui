#!/usr/bin/env python3
"""
Script de test pour démontrer les nouvelles fonctionnalités de FFmpeg Easy

Nouvelles fonctionnalités implémentées:
1. Génération automatique des noms de fichiers de sortie avec suffixe (ex: _x265)
2. Boutons Apply unifiés et intelligents
3. Bouton Start fonctionnel sans nécessiter de dossier de sortie
"""

import os
from pathlib import Path

def create_test_files():
    """Crée des fichiers de test pour démontrer les fonctionnalités"""
    test_dir = Path("test_media")
    test_dir.mkdir(exist_ok=True)
    
    # Créer quelques fichiers de test vides
    test_files = [
        "sample_video.mp4",
        "sample_audio.wav", 
        "sample_image.jpg"
    ]
    
    for filename in test_files:
        filepath = test_dir / filename
        if not filepath.exists():
            # Créer un fichier vide pour les tests
            filepath.touch()
            print(f"✅ Créé: {filepath}")
    
    print(f"\n📁 Dossier de test créé: {test_dir.absolute()}")
    print("\n🎯 Instructions de test:")
    print("1. Lancez l'application avec: python main.py")
    print("2. Ajoutez les fichiers du dossier 'test_media'")
    print("3. Sélectionnez un codec/encodeur (ex: H.265/HEVC)")
    print("4. NE sélectionnez PAS de dossier de sortie")
    print("5. Cliquez sur 'Apply Settings' pour appliquer les paramètres")
    print("6. Cliquez sur '🚀 Start Encoding' pour démarrer")
    print("7. Les fichiers de sortie seront créés avec des suffixes comme:")
    print("   - sample_video_x265.mp4")
    print("   - sample_audio_aac.mp4") 
    print("   - sample_image_webp.webp")

def demonstrate_naming_logic():
    """Démontre la logique de génération des noms"""
    print("\n🏷️  Logique de génération des suffixes:")
    
    naming_examples = {
        "x265/hevc": "_x265",
        "x264/h264": "_x264", 
        "av1": "_av1",
        "vp9": "_vp9",
        "nvenc": "_nvenc",
        "qsv": "_qsv",
        "amf": "_amf",
        "videotoolbox": "_vt",
        "aac": "_aac",
        "mp3": "_mp3", 
        "opus": "_opus",
        "flac": "_flac",
        "webp": "_webp",
        "avif": "_avif"
    }
    
    for encoder, suffix in naming_examples.items():
        print(f"   {encoder:15} → {suffix}")
    
    print("\n📋 Boutons Apply unifiés:")
    print("   - Si des jobs sont sélectionnés → applique uniquement aux sélectionnés")
    print("   - Si aucune sélection → applique à tous les jobs du type actuel")
    print("   - Plus besoin de choisir entre 'Apply to All' et 'Apply to Selected'")

if __name__ == "__main__":
    print("🎬 FFmpeg Easy - Test des nouvelles fonctionnalités")
    print("=" * 50)
    
    create_test_files()
    demonstrate_naming_logic()
    
    print("\n✨ Fonctionnalités implémentées:")
    print("✅ Génération automatique des noms avec suffixe encodeur")
    print("✅ Dossier de sortie optionnel (utilise le dossier source par défaut)")
    print("✅ Boutons Apply unifiés et intelligents")
    print("✅ Bouton Start fonctionnel sans validation de dossier de sortie")
    print("✅ Interface utilisateur améliorée avec conseils contextuels") 