#!/bin/bash

# Script de désinstallation FFmpeg Easy Server pour Ubuntu
# Copyright (c) 2025 Greg Oire - MIT License

set -e

# Couleurs pour les messages
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Variables de configuration
INSTALL_DIR="/opt/ffmpeg-easy-server"
SERVICE_NAME="ffmpeg-easy-server"
USER_NAME="ffmpeg-server"

print_header() {
    echo -e "${BLUE}===================================${NC}"
    echo -e "${BLUE}  FFmpeg Easy Server Uninstaller${NC}"
    echo -e "${BLUE}===================================${NC}"
    echo ""
}

print_step() {
    echo -e "${GREEN}[ÉTAPE]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[ATTENTION]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERREUR]${NC} $1"
    exit 1
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "Ce script doit être exécuté en tant que root (sudo)"
    fi
}

confirm_uninstall() {
    echo -e "${YELLOW}ATTENTION: Cette action va supprimer complètement FFmpeg Easy Server${NC}"
    echo "  - Service systemd"
    echo "  - Répertoire d'installation ($INSTALL_DIR)"
    echo "  - Utilisateur système ($USER_NAME)"
    echo "  - Scripts de gestion"
    echo "  - Fichiers de logs"
    echo ""
    read -p "Êtes-vous sûr de vouloir continuer ? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Désinstallation annulée."
        exit 0
    fi
}

stop_and_disable_service() {
    print_step "Arrêt et désactivation du service"
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl stop "$SERVICE_NAME"
        print_info "Service arrêté"
    fi
    
    if systemctl is-enabled --quiet "$SERVICE_NAME"; then
        systemctl disable "$SERVICE_NAME"
        print_info "Service désactivé"
    fi
}

remove_service_file() {
    print_step "Suppression du fichier de service systemd"
    
    if [[ -f "/etc/systemd/system/$SERVICE_NAME.service" ]]; then
        rm "/etc/systemd/system/$SERVICE_NAME.service"
        systemctl daemon-reload
        print_info "Fichier de service supprimé"
    fi
}

remove_management_scripts() {
    print_step "Suppression des scripts de gestion"
    
    for script in ffmpeg-server-start ffmpeg-server-stop ffmpeg-server-status ffmpeg-server-logs; do
        if [[ -f "/usr/local/bin/$script" ]]; then
            rm "/usr/local/bin/$script"
            print_info "Script $script supprimé"
        fi
    done
}

remove_install_directory() {
    print_step "Suppression du répertoire d'installation"
    
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        print_info "Répertoire $INSTALL_DIR supprimé"
    fi
}

remove_user() {
    print_step "Suppression de l'utilisateur système"
    
    if id "$USER_NAME" &>/dev/null; then
        userdel "$USER_NAME"
        print_info "Utilisateur $USER_NAME supprimé"
    fi
}

remove_logs() {
    print_step "Suppression des fichiers de logs"
    
    if [[ -f "/var/log/ffmpeg-easy-server.log" ]]; then
        rm "/var/log/ffmpeg-easy-server.log"
        print_info "Fichiers de logs supprimés"
    fi
}

cleanup_firewall() {
    print_step "Nettoyage du pare-feu"
    
    if command -v ufw &> /dev/null; then
        print_warning "Règle UFW pour le port 8765 non supprimée automatiquement"
        print_info "Exécutez manuellement: sudo ufw delete allow 8765/tcp"
    fi
}

show_completion_info() {
    echo ""
    echo -e "${GREEN}================================${NC}"
    echo -e "${GREEN}   Désinstallation terminée !${NC}"
    echo -e "${GREEN}================================${NC}"
    echo ""
    echo -e "${BLUE}Éléments supprimés:${NC}"
    echo "  ✓ Service systemd"
    echo "  ✓ Répertoire d'installation"
    echo "  ✓ Utilisateur système"
    echo "  ✓ Scripts de gestion"
    echo "  ✓ Fichiers de logs"
    echo ""
    echo -e "${YELLOW}Actions manuelles recommandées:${NC}"
    echo "  - Supprimer la règle UFW: sudo ufw delete allow 8765/tcp"
    echo "  - Nettoyer les paquets inutiles: sudo apt autoremove"
    echo ""
}

main() {
    print_header
    check_root
    confirm_uninstall
    
    stop_and_disable_service
    remove_service_file
    remove_management_scripts
    remove_install_directory
    remove_user
    remove_logs
    cleanup_firewall
    
    show_completion_info
}

# Exécution du script principal
main "$@" 