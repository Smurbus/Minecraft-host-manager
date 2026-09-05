"""Loading the application configuration.

The *confidential* values (MAC address, IP, SSH credentials, start script
path, secrets...) live in ``instance/config.py``, a file intentionally absent
from the git repository (see .gitignore). The ``instance/config.example.py``
file serves as the template to copy and adapt.
"""

from __future__ import annotations

import importlib.util
import os

# Default values, overridden by instance/config.py when present.
DEFAULTS = {
    "SSH_PORT": 22,
    "MC_PORT": 25565,
    "TMUX_SESSION_NAME": "mcserver",
    "AUTO_SHUTDOWN_MINUTES": 30,
    "SSH_TIMEOUT": 10,
    "PC_BOOT_TIMEOUT": 180,       # seconds allowed for the PC to respond after WOL
    "SERVER_START_TIMEOUT": 300,  # seconds allowed for the MC server to become available
    "SERVER_STOP_TIMEOUT": 120,   # seconds allowed for the MC server to stop cleanly
    "PC_SHUTDOWN_TIMEOUT": 120,   # seconds allowed for the PC to shut down
    "POLL_INTERVAL": 5,           # interval (s) between checks during start/stop
    "STATUS_CACHE_SECONDS": 2,    # burst protection on /api/status
    "MONITOR_INTERVAL_SECONDS": 60,  # frequency of the auto-shutdown monitor
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
    """Simple namespace containing the fully resolved configuration."""


def _instance_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "instance")


def load_config() -> Config:
    instance_path = _instance_dir()
    config_file = os.path.join(instance_path, "config.py")

    if not os.path.exists(config_file):
        raise RuntimeError(
            "File 'instance/config.py' not found.\n"
            "Copy 'instance/config.example.py' to 'instance/config.py' "
            "then fill in your values (see README.md)."
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
            "Missing or empty configuration keys in instance/config.py: "
            + ", ".join(missing)
        )

    return cfg
