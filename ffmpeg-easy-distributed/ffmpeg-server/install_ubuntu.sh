#!/bin/bash

# Script d'installation FFmpeg Easy Server pour Ubuntu
# Copyright (c) 2025 Greg Oire - MIT License

set -e  # Arrêter en cas d'erreur

# Obtenir le répertoire du script pour gérer les chemins relatifs de manière robuste
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Couleurs pour les messages
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Variables de configuration
INSTALL_DIR="/opt/ffmpeg-easy-server"
SERVICE_NAME="ffmpeg-easy-server"
USER_NAME="ffmpeg-server"
PYTHON_VERSION="3.12"

print_header() {
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}  FFmpeg Easy Server Installer${NC}"
    echo -e "${BLUE}================================${NC}"
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

detect_ubuntu_version() {
    if [[ ! -f /etc/lsb-release ]]; then
        print_error "Ce script est conçu pour Ubuntu uniquement"
    fi
    
    source /etc/lsb-release
    print_info "Détection: Ubuntu $DISTRIB_RELEASE"
    
    # Vérifier si c'est une version supportée
    case $DISTRIB_RELEASE in
        "20.04"|"22.04"|"24.04")
            print_info "Version Ubuntu supportée: $DISTRIB_RELEASE"
            ;;
        *)
            print_warning "Version Ubuntu non testée: $DISTRIB_RELEASE"
            read -p "Voulez-vous continuer ? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
            ;;
    esac
}

update_system() {
    print_step "Mise à jour du système"
    apt update && apt upgrade -y
}

install_dependencies() {
    print_step "Installation des dépendances système"
    
    # Dépendances essentielles
    apt install -y \
        curl \
        wget \
        git \
        build-essential \
        software-properties-common \
        apt-transport-https \
        ca-certificates \
        gnupg \
        lsb-release \
        ufw
    
    # Installation de Python 3.12 si nécessaire
    if ! command -v python3.12 &> /dev/null; then
        print_info "Installation de Python 3.12"
        add-apt-repository ppa:deadsnakes/ppa -y
        apt update
        apt install -y python3.12 python3.12-venv python3.12-dev python3-pip
    else
        print_info "Python 3.12 déjà installé"
    fi
    
    # Installation de FFmpeg avec tous les codecs
    print_info "Installation de FFmpeg"
    apt install -y ffmpeg
    
    # Vérifier la version de FFmpeg
    ffmpeg_version=$(ffmpeg -version | head -n1)
    print_info "FFmpeg installé: $ffmpeg_version"
}

create_user() {
    print_step "Création de l'utilisateur système"
    
    if ! id "$USER_NAME" &>/dev/null; then
        useradd --system --home-dir "$INSTALL_DIR" --shell /bin/bash --create-home "$USER_NAME"
        print_info "Utilisateur '$USER_NAME' créé"
    else
        print_info "Utilisateur '$USER_NAME' existe déjà"
    fi
}

setup_directory() {
    print_step "Configuration du répertoire d'installation"
    
    mkdir -p "$INSTALL_DIR"
    chown "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
    
    # Copier les fichiers du serveur
    if [[ -f "$SCRIPT_DIR/main.py" ]]; then
        print_info "Copie des fichiers du serveur (fichiers principaux)"
        # Copier les éléments spécifiques au lieu de '.' pour éviter de copier le lien symbolique 'shared' problématique
        # Utiliser SCRIPT_DIR pour s'assurer que les sources sont correctes
        cp -r "$SCRIPT_DIR/core" "$SCRIPT_DIR/server" "$SCRIPT_DIR/main.py" "$SCRIPT_DIR/requirements.txt" \
              "$SCRIPT_DIR/Dockerfile" "$SCRIPT_DIR/docker-compose.yml" \
              "$SCRIPT_DIR/README_UBUNTU_INSTALL.md" "$SCRIPT_DIR/uninstall_ubuntu.sh" "$INSTALL_DIR/"

        print_info "Suppression de l'ancien lien/fichier 'shared' dans $INSTALL_DIR s'il existe"
        rm -rf "$INSTALL_DIR/shared"

        # Le chemin vers ffmpeg-gui/shared est relatif au SCRIPT_DIR parent
        GUI_SHARED_DIR="$SCRIPT_DIR/../ffmpeg-gui/shared"
        print_info "Copie du répertoire 'shared' depuis $GUI_SHARED_DIR"
        if [ -d "$GUI_SHARED_DIR" ]; then
            cp -r "$GUI_SHARED_DIR" "$INSTALL_DIR/shared"
            print_info "Répertoire 'shared' copié avec succès."
        else
            print_error "Répertoire source '$GUI_SHARED_DIR' non trouvé."
        fi

        chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
    else
        print_error "Fichier principal 'main.py' non trouvé dans $SCRIPT_DIR. Assurez-vous que le script est dans le bon répertoire."
    fi
}

setup_python_environment() {
    print_step "Configuration de l'environnement Python"
    
    # Créer l'environnement virtuel en tant qu'utilisateur ffmpeg-server
    sudo -u "$USER_NAME" python3.12 -m venv "$INSTALL_DIR/venv"
    
    # Installer les dépendances
    print_info "Installation des dépendances Python"
    sudo -u "$USER_NAME" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    # S'assurer que requirements.txt est trouvé dans INSTALL_DIR où il a été copié
    sudo -u "$USER_NAME" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
}

create_systemd_service() {
    print_step "Création du service systemd"
    
    cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=FFmpeg Easy Server
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=1
User=$USER_NAME
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$INSTALL_DIR/venv/bin
ExecStart=$INSTALL_DIR/venv/bin/python main.py
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    print_info "Service systemd créé"
}

configure_firewall() {
    print_step "Configuration du pare-feu"
    
    if command -v ufw &> /dev/null; then
        print_info "Configuration d'UFW pour le port 8765"
        ufw allow 8765/tcp
        print_info "Port 8765 ouvert dans UFW"
    else
        print_warning "UFW non installé. Assurez-vous que le port 8765 est accessible"
    fi
}

create_config_file() {
    print_step "Création du fichier de configuration"
    
    cat > "$INSTALL_DIR/config.json" << EOF
{
    "server": {
        "host": "0.0.0.0",
        "port": 8765,
        "max_jobs": 2
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/ffmpeg-easy-server.log"
    }
}
EOF
    
    chown "$USER_NAME:$USER_NAME" "$INSTALL_DIR/config.json"
    
    # Créer le fichier de log
    touch "/var/log/ffmpeg-easy-server.log"
    chown "$USER_NAME:$USER_NAME" "/var/log/ffmpeg-easy-server.log"
}

create_management_scripts() {
    print_step "Création des scripts de gestion"
    
    # Script de démarrage
    cat > "/usr/local/bin/ffmpeg-server-start" << 'EOF'
#!/bin/bash
systemctl start ffmpeg-easy-server
systemctl status ffmpeg-easy-server
EOF
    
    # Script d'arrêt
    cat > "/usr/local/bin/ffmpeg-server-stop" << 'EOF'
#!/bin/bash
systemctl stop ffmpeg-easy-server
EOF
    
    # Script de statut
    cat > "/usr/local/bin/ffmpeg-server-status" << 'EOF'
#!/bin/bash
systemctl status ffmpeg-easy-server
echo ""
echo "Logs récents:"
journalctl -u ffmpeg-easy-server -n 20 --no-pager
EOF
    
    # Script de logs
    cat > "/usr/local/bin/ffmpeg-server-logs" << 'EOF'
#!/bin/bash
journalctl -u ffmpeg-easy-server -f
EOF
    
    chmod +x /usr/local/bin/ffmpeg-server-*
    print_info "Scripts de gestion créés (/usr/local/bin/ffmpeg-server-*)"
}

start_service() {
    print_step "Démarrage du service"
    
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"
    
    sleep 3
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_info "Service démarré avec succès"
    else
        print_error "Échec du démarrage du service"
    fi
}

show_completion_info() {
    echo ""
    echo -e "${GREEN}================================${NC}"
    echo -e "${GREEN}   Installation terminée !${NC}"
    echo -e "${GREEN}================================${NC}"
    echo ""
    echo -e "${BLUE}Informations du serveur:${NC}"
    echo "  - Répertoire: $INSTALL_DIR"
    echo "  - Utilisateur: $USER_NAME"
    echo "  - Service: $SERVICE_NAME"
    echo "  - Port: 8765"
    echo ""
    echo -e "${BLUE}Commandes utiles:${NC}"
    echo "  - Démarrer:    ffmpeg-server-start"
    echo "  - Arrêter:     ffmpeg-server-stop"
    echo "  - Statut:      ffmpeg-server-status"
    echo "  - Logs:        ffmpeg-server-logs"
    echo ""
    echo -e "${BLUE}Ou via systemctl:${NC}"
    echo "  - systemctl start $SERVICE_NAME"
    echo "  - systemctl stop $SERVICE_NAME"
    echo "  - systemctl status $SERVICE_NAME"
    echo "  - journalctl -u $SERVICE_NAME -f"
    echo ""
    
    # Afficher l'IP du serveur
    server_ip=$(hostname -I | awk '{print $1}')
    echo -e "${YELLOW}Adresse du serveur: $server_ip:8765${NC}"
    echo ""
}

main() {
    print_header
    
    check_root
    detect_ubuntu_version
    update_system
    install_dependencies
    create_user
    setup_directory
    setup_python_environment
    create_systemd_service
    configure_firewall
    create_config_file
    create_management_scripts
    start_service
    show_completion_info
}

# Exécution du script principal
main "$@" 