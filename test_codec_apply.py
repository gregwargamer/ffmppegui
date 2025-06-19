#!/usr/bin/env python3
"""
Script de test pour vérifier le fonctionnement du bouton Apply Codec
"""

import time
import subprocess
import sys

def test_codec_functionality():
    """Test pour vérifier que les boutons de codec fonctionnent"""
    print("🧪 Test du bouton Apply Codec")
    print("=" * 40)
    
    print("\n📋 Instructions de test:")
    print("1. L'application devrait être ouverte")
    print("2. Ajoutez un fichier de test (ex: test_media/sample_video.mp4)")
    print("3. Sélectionnez un codec (ex: H.265/HEVC)")
    print("4. Sélectionnez un encodeur (ex: libx265)")
    print("5. Cliquez sur '🔄 Apply Codec'")
    print("6. Vérifiez que la colonne 'Encoder' se met à jour dans la liste")
    
    print("\n🔍 Points à vérifier:")
    print("✓ Le bouton affiche un message de confirmation")
    print("✓ La colonne 'Encoder' montre le nouvel encodeur")
    print("✓ Si un job est sélectionné, seul ce job est modifié")
    print("✓ Si aucun job n'est sélectionné, tous les jobs du même type sont modifiés")
    
    print("\n🐛 Debugging:")
    print("- Surveillez la console pour les messages 'DEBUG:'")
    print("- Ces messages montrent comment l'encodeur est extrait du nom d'affichage")
    
    print("\n🚨 Problèmes potentiels:")
    print("- Si le bouton ne fait rien, vérifiez qu'un encodeur est sélectionné")
    print("- Si l'encodeur n'apparaît pas, vérifiez les messages de debug")
    print("- Si le message d'erreur apparaît, l'encodeur n'est pas correctement mappé")

if __name__ == "__main__":
    test_codec_functionality()
    
    print(f"\n⏰ Attendez quelques secondes que l'application se charge...")
    print("🖱️  Testez maintenant le bouton '🔄 Apply Codec' dans l'application !") 