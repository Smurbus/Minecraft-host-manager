"""Chargement de la configuration de l'application.

Les valeurs *confidentielles* (adresse MAC, IP, identifiants SSH, chemin du
script de lancement, secrets...) vivent dans ``instance/config.py``, un
fichier volontairement absent du dépôt git (voir .gitignore). Le fichier
``instance/config.example.py`` sert de modèle à copier/adapter.
"""

from __future__ import annotations

import importlib.util
import os

# Valeurs par défaut, surchargées par instance/config.py si présentes.
DEFAULTS = {
    "SSH_PORT": 22,
    "MC_PORT": 25565,
    "TMUX_SESSION_NAME": "mcserver",
    "AUTO_SHUTDOWN_MINUTES": 30,
    "SSH_TIMEOUT": 10,
    "PC_BOOT_TIMEOUT": 180,       # secondes laissées au PC pour répondre après le WOL
    "SERVER_START_TIMEOUT": 300,  # secondes laissées au serveur MC pour devenir disponible
    "SERVER_STOP_TIMEOUT": 120,   # secondes laissées au serveur MC pour s'arrêter proprement
    "PC_SHUTDOWN_TIMEOUT": 120,   # secondes laissées au PC pour s'éteindre
    "POLL_INTERVAL": 5,           # intervalle (s) entre deux vérifications pendant start/stop
    "STATUS_CACHE_SECONDS": 2,    # anti rafale sur /api/status
    "MONITOR_INTERVAL_SECONDS": 60,  # fréquence de la surveillance auto-extinction
    "SESSION_LIFETIME_HOURS": 12,
    "LOGIN_MAX_ATTEMPTS": 5,
    "LOGIN_LOCKOUT_SECONDS": 900,
    "INVITE_LIFETIME_HOURS": 48,
}

REQUIRED_KEYS = [
    "HOST_MAC",
    "HOST_IP",
    "BROADCAST_IP",
    "SSH_USER",
    "SSH_KEY_PATH",
    "START_SCRIPT_PATH",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD_HASH",
    "SECRET_KEY",
]


class Config:
    """Espace de noms simple contenant toute la configuration résolue."""


def _instance_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")


def load_config() -> Config:
    instance_path = _instance_dir()
    config_file = os.path.join(instance_path, "config.py")

    if not os.path.exists(config_file):
        raise RuntimeError(
            "Fichier 'instance/config.py' introuvable.\n"
            "Copiez 'instance/config.example.py' vers 'instance/config.py' "
            "puis renseignez vos valeurs (voir README.md)."
        )

    spec = importlib.util.spec_from_file_location("instance_config", config_file)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    cfg = Config()
    for key, value in DEFAULTS.items():
        setattr(cfg, key, value)
    for key in dir(module):
        if key.isupper():
            setattr(cfg, key, getattr(module, key))

    missing = [key for key in REQUIRED_KEYS if not getattr(cfg, key, None)]
    if missing:
        raise RuntimeError(
            "Clés de configuration manquantes ou vides dans instance/config.py : "
            + ", ".join(missing)
        )

    return cfg
