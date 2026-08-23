"""Logique métier : Wake-on-LAN, SSH vers le PC hôte, interrogation du
serveur Minecraft, machine à états et surveillance d'inactivité.

Toute la classe ``ServerController`` est thread-safe : les actions
(démarrage/arrêt) tournent dans des threads d'arrière-plan pour ne jamais
bloquer une requête HTTP, pendant qu'un thread de surveillance dédié gère
l'extinction automatique après inactivité.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import paramiko
from mcstatus import JavaServer

logger = logging.getLogger(__name__)


class ActionState:
    IDLE = "idle"
    STARTING = "starting"
    STOPPING = "stopping"


class ServerState:
    """États affichés côté interface web."""

    PC_OFF = "pc_off"
    SERVER_OFF = "server_off"        # PC allumé, serveur Minecraft non lancé
    STARTING = "starting"
    AVAILABLE = "available"
    STOPPING = "stopping"


@dataclass
class MCStatus:
    online: int = 0
    max: int = 0
    players: list = field(default_factory=list)


class ServerController:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._action_state = ActionState.IDLE
        self._last_message = "En attente."
        self._last_error: Optional[str] = None
        self._last_seen_with_players_at: Optional[datetime] = None
        self._status_cache = None
        self._status_cache_at = 0.0
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()

    # ------------------------------------------------------------------
    # Primitives bas niveau
    # ------------------------------------------------------------------

    def send_wol(self) -> None:
        mac = self.cfg.HOST_MAC.replace(":", "").replace("-", "")
        if len(mac) != 12:
            raise ValueError(f"Adresse MAC invalide : {self.cfg.HOST_MAC!r}")
        mac_bytes = bytes.fromhex(mac)
        packet = b"\xff" * 6 + mac_bytes * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (self.cfg.BROADCAST_IP, 9))
        logger.info("Paquet magique WOL envoyé à %s via %s", self.cfg.HOST_MAC, self.cfg.BROADCAST_IP)

    def is_pc_reachable(self, timeout: float = 2.0) -> bool:
        """Le PC est considéré comme allumé si son port SSH répond."""
        try:
            with socket.create_connection((self.cfg.HOST_IP, self.cfg.SSH_PORT), timeout=timeout):
                return True
        except OSError:
            return False

    def query_minecraft(self, timeout: float = 2.0) -> Optional[MCStatus]:
        try:
            server = JavaServer.lookup(f"{self.cfg.HOST_IP}:{self.cfg.MC_PORT}", timeout=timeout)
            status = server.status()
            names = [p.name for p in status.players.sample] if status.players.sample else []
            return MCStatus(online=status.players.online, max=status.players.max, players=names)
        except Exception:
            return None

    def _ssh_client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.cfg.HOST_IP,
            port=self.cfg.SSH_PORT,
            username=self.cfg.SSH_USER,
            key_filename=self.cfg.SSH_KEY_PATH,
            timeout=self.cfg.SSH_TIMEOUT,
        )
        return client

    def ssh_run(self, command: str, timeout: Optional[float] = None):
        """Exécute une commande sur le PC hôte. Retourne (code, stdout, stderr).

        Peut lever une exception (connexion refusée, timeout...) : à charge
        de l'appelant de l'attraper si l'échec est un cas attendu (ex :
        commande de shutdown qui coupe la connexion en cours de route).
        """
        timeout = timeout or self.cfg.SSH_TIMEOUT
        client = self._ssh_client()
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            return exit_code, out, err
        finally:
            client.close()

    def _tmux_session_exists(self) -> bool:
        try:
            code, _, _ = self.ssh_run(f"tmux has-session -t {self.cfg.TMUX_SESSION_NAME}")
            return code == 0
        except Exception:
            return False

    def _capture_tmux_output(self, lines: int = 40) -> str:
        try:
            _, out, _ = self.ssh_run(
                f"tmux capture-pane -pt {self.cfg.TMUX_SESSION_NAME} -S -{lines}"
            )
            return out.strip()
        except Exception as exc:  # noqa: BLE001
            return f"(impossible de récupérer la sortie tmux : {exc})"

    # ------------------------------------------------------------------
    # État exposé à l'interface web
    # ------------------------------------------------------------------

    def get_status(self, force: bool = False) -> dict:
        with self._lock:
            now = time.monotonic()
            if not force and self._status_cache and (now - self._status_cache_at) < self.cfg.STATUS_CACHE_SECONDS:
                return self._status_cache

            action_state = self._action_state
            message = self._last_message
            error = self._last_error

            pc_up = self.is_pc_reachable()
            mc = self.query_minecraft() if pc_up else None

            if action_state == ActionState.STARTING:
                state = ServerState.STARTING
            elif action_state == ActionState.STOPPING:
                state = ServerState.STOPPING
            elif not pc_up:
                state = ServerState.PC_OFF
            elif mc is not None:
                state = ServerState.AVAILABLE
            else:
                state = ServerState.SERVER_OFF

            result = {
                "state": state,
                "pc_reachable": pc_up,
                "message": message,
                "error": error,
                "players": {
                    "online": mc.online if mc else 0,
                    "max": mc.max if mc else 0,
                    "names": mc.players if mc else [],
                },
            }
            self._status_cache = result
            self._status_cache_at = now
            return result

    def _set_progress(self, message: str, error: Optional[str] = None) -> None:
        with self._lock:
            self._last_message = message
            self._last_error = error
            self._status_cache = None  # force le prochain appel à recalculer
        logger.info("[controller] %s%s", message, f" (erreur: {error})" if error else "")

    def action_state(self) -> str:
        with self._lock:
            return self._action_state

    def is_busy(self) -> bool:
        return self.action_state() != ActionState.IDLE

    # ------------------------------------------------------------------
    # Séquence de démarrage
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Déclenche la séquence de démarrage en tâche de fond.

        Retourne False si une action est déjà en cours.
        """
        with self._lock:
            if self._action_state != ActionState.IDLE:
                return False
            self._action_state = ActionState.STARTING
        self._set_progress("Démarrage demandé...")
        thread = threading.Thread(target=self._start_sequence, daemon=True)
        thread.start()
        return True

    def _start_sequence(self) -> None:
        try:
            if not self.is_pc_reachable():
                self._set_progress("Réveil du PC (Wake-on-LAN)...")
                try:
                    self.send_wol()
                except Exception as exc:  # noqa: BLE001
                    self._finish_start(error=f"Échec de l'envoi du paquet WOL : {exc}")
                    return

                deadline = time.monotonic() + self.cfg.PC_BOOT_TIMEOUT
                while time.monotonic() < deadline:
                    if self.is_pc_reachable():
                        break
                    time.sleep(self.cfg.POLL_INTERVAL)
                else:
                    self._finish_start(
                        error=(
                            "Le PC ne répond pas après le réveil WOL. "
                            "Vérifiez que le Wake-on-LAN est bien activé et persistant "
                            "après extinction (BIOS + configuration réseau)."
                        )
                    )
                    return

            self._set_progress("PC démarré, lancement du serveur Minecraft...")

            # Le PC vient peut-être de démarrer : le service SSH peut mettre
            # quelques secondes de plus à être prêt que le simple port TCP.
            ssh_deadline = time.monotonic() + 60
            last_exc: Optional[Exception] = None
            while time.monotonic() < ssh_deadline:
                try:
                    if self._tmux_session_exists():
                        self._set_progress("Le serveur semble déjà lancé, poursuite du suivi...")
                    else:
                        self.ssh_run(
                            f"tmux new-session -d -s {self.cfg.TMUX_SESSION_NAME} "
                            f"\"sudo -n bash {self.cfg.START_SCRIPT_PATH}\""
                        )
                        self._set_progress("Script de lancement exécuté, attente du serveur Minecraft...")
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    time.sleep(self.cfg.POLL_INTERVAL)

            if last_exc is not None:
                self._finish_start(error=f"Connexion SSH impossible pour lancer le script : {last_exc}")
                return

            deadline = time.monotonic() + self.cfg.SERVER_START_TIMEOUT
            while time.monotonic() < deadline:
                if self.query_minecraft() is not None:
                    self._finish_start(message="Serveur Minecraft disponible !")
                    return
                time.sleep(self.cfg.POLL_INTERVAL)

            debug_output = self._capture_tmux_output()
            self._finish_start(
                error=(
                    "Le serveur Minecraft ne répond pas après le délai prévu. "
                    "Vérifiez le script de lancement. Dernières lignes de la session tmux :\n"
                    f"{debug_output}"
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erreur inattendue pendant la séquence de démarrage")
            self._finish_start(error=f"Erreur inattendue : {exc}")

    def _finish_start(self, message: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._lock:
            self._action_state = ActionState.IDLE
        self._set_progress(message or "Démarrage terminé.", error=error)

    # ------------------------------------------------------------------
    # Séquence d'arrêt
    # ------------------------------------------------------------------

    def stop(self, force: bool = False) -> dict:
        """Déclenche la séquence d'arrêt en tâche de fond.

        Si des joueurs sont connectés et que ``force`` vaut False, ne fait
        rien et retourne {"confirm_required": True, "online": n}.
        """
        with self._lock:
            if self._action_state != ActionState.IDLE:
                return {"started": False, "reason": "busy"}

            mc = self.query_minecraft()
            if mc is not None and mc.online > 0 and not force:
                return {"confirm_required": True, "online": mc.online, "players": mc.players}

            self._action_state = ActionState.STOPPING

        self._set_progress("Arrêt demandé...")
        thread = threading.Thread(target=self._stop_sequence, daemon=True)
        thread.start()
        return {"started": True}

    def _stop_sequence(self) -> None:
        try:
            if not self.is_pc_reachable():
                self._finish_stop(message="Le PC est déjà éteint.")
                return

            if self._tmux_session_exists():
                self._set_progress("Envoi de la commande d'arrêt au serveur Minecraft...")
                try:
                    self.ssh_run(
                        f"tmux send-keys -t {self.cfg.TMUX_SESSION_NAME} 'stop' Enter"
                    )
                except Exception as exc:  # noqa: BLE001
                    self._finish_stop(error=f"Impossible d'envoyer la commande d'arrêt : {exc}")
                    return

                deadline = time.monotonic() + self.cfg.SERVER_STOP_TIMEOUT
                while time.monotonic() < deadline:
                    if self.query_minecraft() is None:
                        break
                    time.sleep(self.cfg.POLL_INTERVAL)
                else:
                    self._finish_stop(
                        error=(
                            "Le serveur Minecraft ne s'est pas arrêté dans le délai prévu. "
                            "Le PC n'a PAS été éteint par précaution (pour éviter de perdre "
                            "la sauvegarde du monde). Réessayez ou vérifiez manuellement."
                        )
                    )
                    return
            else:
                self._set_progress("Aucune session de jeu active, extinction directe du PC...")

            self._set_progress("Extinction du PC...")
            try:
                self.ssh_run("sudo -n shutdown -h now", timeout=10)
            except Exception:
                # La connexion est généralement coupée brutalement par l'extinction : normal.
                pass

            deadline = time.monotonic() + self.cfg.PC_SHUTDOWN_TIMEOUT
            while time.monotonic() < deadline:
                if not self.is_pc_reachable():
                    self._finish_stop(message="PC éteint.")
                    return
                time.sleep(self.cfg.POLL_INTERVAL)

            self._finish_stop(
                error="Le PC ne semble pas s'être éteint dans le délai prévu. Vérifiez manuellement."
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erreur inattendue pendant la séquence d'arrêt")
            self._finish_stop(error=f"Erreur inattendue : {exc}")

    def _finish_stop(self, message: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._lock:
            self._action_state = ActionState.IDLE
            self._last_seen_with_players_at = None
        self._set_progress(message or "Arrêt terminé.", error=error)

    # ------------------------------------------------------------------
    # Surveillance auto-extinction (inactivité)
    # ------------------------------------------------------------------

    def start_monitor(self) -> None:
        if self._monitor_thread is not None:
            return
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("Surveillance auto-extinction démarrée (%s min d'inactivité).", self.cfg.AUTO_SHUTDOWN_MINUTES)

    def stop_monitor(self) -> None:
        self._stop_monitor.set()

    def _monitor_loop(self) -> None:
        while not self._stop_monitor.is_set():
            try:
                self._monitor_tick()
            except Exception:  # noqa: BLE001
                logger.exception("Erreur dans la boucle de surveillance auto-extinction")
            self._stop_monitor.wait(self.cfg.MONITOR_INTERVAL_SECONDS)

    def _monitor_tick(self) -> None:
        if self.is_busy():
            return
        if not self.is_pc_reachable():
            with self._lock:
                self._last_seen_with_players_at = None
            return

        mc = self.query_minecraft()
        if mc is None:
            # Serveur non lancé : rien à surveiller.
            return

        now = datetime.now(timezone.utc)
        if mc.online > 0:
            with self._lock:
                self._last_seen_with_players_at = now
            return

        with self._lock:
            if self._last_seen_with_players_at is None:
                self._last_seen_with_players_at = now
                return
            idle_for = (now - self._last_seen_with_players_at).total_seconds() / 60.0

        if idle_for >= self.cfg.AUTO_SHUTDOWN_MINUTES:
            logger.info(
                "Aucun joueur depuis %.1f minutes (seuil : %s min) : extinction automatique.",
                idle_for,
                self.cfg.AUTO_SHUTDOWN_MINUTES,
            )
            self.stop(force=True)
