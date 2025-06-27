# Installation FFmpeg Easy Server sur Ubuntu

Ce guide vous explique comment installer et configurer FFmpeg Easy Server sur Ubuntu avec le script d'installation automatisé.

## Prérequis

- **Système** : Ubuntu 20.04, 22.04, ou 24.04 LTS
- **Accès** : Privilèges sudo/root
- **Réseau** : Connexion Internet pour télécharger les dépendances
- **Port** : Port 8765 disponible (pour le serveur)

## Installation rapide

### 1. Télécharger les fichiers du serveur

```bash
# Cloner le projet ou télécharger les fichiers
git clone <votre-repo>
cd ffmpeg-easy-distributed/ffmpeg-server

# Ou télécharger directement le dossier ffmpeg-server
```

### 2. Exécuter le script d'installation

```bash
# Rendre le script exécutable
chmod +x install_ubuntu.sh

# Lancer l'installation
sudo ./install_ubuntu.sh
```

### 3. Vérifier l'installation

```bash
# Vérifier le statut du service
ffmpeg-server-status

# Ou avec systemctl
systemctl status ffmpeg-easy-server
```

## Ce que fait le script d'installation

### ✅ Installation des dépendances
- Python 3.12 (depuis le PPA deadsnakes)
- FFmpeg avec tous les codecs
- Outils de compilation et dépendances système

### ✅ Configuration du serveur
- Création d'un utilisateur système `ffmpeg-server`
- Installation dans `/opt/ffmpeg-easy-server`
- Environnement virtuel Python isolé
- Installation des dépendances Python (`websockets`, `psutil`)

### ✅ Service systemd
- Service automatique au démarrage
- Redémarrage automatique en cas d'erreur
- Logs centralisés via journald

### ✅ Pare-feu et sécurité
- Configuration UFW pour le port 8765
- Utilisateur système dédié (non-login)
- Permissions restrictives sur les fichiers

### ✅ Scripts de gestion
- `ffmpeg-server-start` : Démarrer le serveur
- `ffmpeg-server-stop` : Arrêter le serveur
- `ffmpeg-server-status` : Voir le statut et logs récents
- `ffmpeg-server-logs` : Suivre les logs en temps réel

## Utilisation après installation

### Commandes principales

```bash
# Démarrer le serveur
ffmpeg-server-start

# Arrêter le serveur
ffmpeg-server-stop

# Voir le statut
ffmpeg-server-status

# Suivre les logs
ffmpeg-server-logs
```

### Commandes systemctl alternatives

```bash
# Contrôle du service
sudo systemctl start ffmpeg-easy-server
sudo systemctl stop ffmpeg-easy-server
sudo systemctl restart ffmpeg-easy-server
sudo systemctl status ffmpeg-easy-server

# Logs
sudo journalctl -u ffmpeg-easy-server -f
sudo journalctl -u ffmpeg-easy-server -n 50
```

### Configuration

Le serveur utilise les fichiers de configuration suivants :

- **Configuration** : `/opt/ffmpeg-easy-server/config.json`
- **Logs** : `/var/log/ffmpeg-easy-server.log`
- **Service** : `/etc/systemd/system/ffmpeg-easy-server.service`

#### Modifier la configuration

```bash
# Éditer la configuration
sudo nano /opt/ffmpeg-easy-server/config.json

# Redémarrer après modification
sudo systemctl restart ffmpeg-easy-server
```

Exemple de configuration :
```json
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
```

## Connexion depuis le client

Une fois installé, le serveur sera accessible à l'adresse :
```
IP_DU_SERVEUR:8765
```

Dans l'interface graphique FFmpeg Easy :
1. Aller dans le menu **Serveurs**
2. Cliquer sur **Gestion des Serveurs**
3. Ajouter l'IP du serveur Ubuntu et le port 8765
4. Cliquer sur **Ajouter**

## Surveillance et maintenance

### Vérifier les performances

```bash
# Voir l'utilisation CPU/RAM
htop

# Voir les processus FFmpeg
ps aux | grep ffmpeg

# Voir l'espace disque
df -h
```

### Logs importants

```bash
# Logs du serveur FFmpeg Easy
sudo tail -f /var/log/ffmpeg-easy-server.log

# Logs système du service
sudo journalctl -u ffmpeg-easy-server -f

# Logs système généraux
sudo tail -f /var/log/syslog
```

### Mise à jour

Pour mettre à jour le serveur :

```bash
# Arrêter le service
sudo systemctl stop ffmpeg-easy-server

# Sauvegarder la configuration
sudo cp /opt/ffmpeg-easy-server/config.json /tmp/

# Remplacer les fichiers avec la nouvelle version
sudo cp -r nouvels-fichiers/* /opt/ffmpeg-easy-server/
sudo chown -R ffmpeg-server:ffmpeg-server /opt/ffmpeg-easy-server

# Restaurer la configuration
sudo cp /tmp/config.json /opt/ffmpeg-easy-server/

# Redémarrer
sudo systemctl start ffmpeg-easy-server
```

## Désinstallation

Pour désinstaller complètement le serveur :

```bash
# Exécuter le script de désinstallation
sudo ./uninstall_ubuntu.sh
```

Le script supprimera :
- ✅ Service systemd
- ✅ Répertoire d'installation
- ✅ Utilisateur système
- ✅ Scripts de gestion
- ✅ Fichiers de logs

## Résolution de problèmes

### Le serveur ne démarre pas

```bash
# Vérifier les logs d'erreur
sudo journalctl -u ffmpeg-easy-server -n 50

# Vérifier que le port n'est pas utilisé
sudo netstat -tlnp | grep 8765

# Tester manuellement
sudo -u ffmpeg-server /opt/ffmpeg-easy-server/venv/bin/python /opt/ffmpeg-easy-server/main.py
```

### Problème de permissions

```bash
# Rétablir les permissions
sudo chown -R ffmpeg-server:ffmpeg-server /opt/ffmpeg-easy-server
sudo chmod +x /opt/ffmpeg-easy-server/main.py
```

### Le serveur n'est pas accessible

```bash
# Vérifier le pare-feu
sudo ufw status
sudo ufw allow 8765/tcp

# Vérifier que le serveur écoute
sudo netstat -tlnp | grep 8765
```

### Performances lentes

```bash
# Augmenter le nombre de jobs max dans la config
sudo nano /opt/ffmpeg-easy-server/config.json
# Modifier "max_jobs": 4

# Redémarrer
sudo systemctl restart ffmpeg-easy-server
```

## Support

Pour toute question ou problème :
1. Vérifiez les logs : `ffmpeg-server-logs`
2. Consultez la documentation du projet
3. Ouvrez une issue sur GitHub

---

**Note** : Ce serveur est conçu pour fonctionner en réseau local ou avec un accès sécurisé. Ne l'exposez pas directement sur Internet sans mesures de sécurité appropriées. 