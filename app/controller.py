"""Business logic: Wake-on-LAN, SSH to the host PC, Minecraft server
queries, state machine, and inactivity monitoring.

The entire ``ServerController`` class is thread-safe: actions (start/stop)
run in background threads so an HTTP request is never blocked, while a
dedicated monitoring thread handles automatic shutdown after inactivity.
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
    """States displayed in the web interface."""

    PC_OFF = "pc_off"
    SERVER_OFF = "server_off"        # PC is on, Minecraft server not started
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
        self._last_message = "Waiting."
        self._last_error: Optional[str] = None
        self._last_seen_with_players_at: Optional[datetime] = None
        self._status_cache = None
        self._status_cache_at = 0.0
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()

    # ------------------------------------------------------------------
    # Low-level primitives
    # ------------------------------------------------------------------

    def send_wol(self) -> None:
        mac = self.cfg.HOST_MAC.replace(":", "").replace("-", "")
        if len(mac) != 12:
            raise ValueError(f"Invalid MAC address: {self.cfg.HOST_MAC!r}")
        mac_bytes = bytes.fromhex(mac)
        packet = b"\xff" * 6 + mac_bytes * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (self.cfg.BROADCAST_IP, 9))
        logger.info("WOL magic packet sent to %s via %s", self.cfg.HOST_MAC, self.cfg.BROADCAST_IP)

    def is_pc_reachable(self, timeout: float = 2.0) -> bool:
        """The PC is considered on if its SSH port responds."""
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
        """Runs a command on the host PC. Returns (code, stdout, stderr).

        May raise an exception (connection refused, timeout...): it is up to
        the caller to catch it if failure is an expected case (for example a
        shutdown command that cuts the connection mid-way).
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
            return f"(unable to retrieve tmux output: {exc})"

    # ------------------------------------------------------------------
    # State exposed to the web interface
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

            # Outside an in-progress start/stop action, update the inactivity
            # tracking here as well (not only in the monitoring loop, which
            # runs only every MONITOR_INTERVAL_SECONDS) so the displayed
            # countdown is accurate to the second as soon as nobody remains.
            auto_shutdown_seconds = None
            if action_state == ActionState.IDLE:
                self._update_idle_tracking(mc)
                if state == ServerState.AVAILABLE and mc is not None and mc.online == 0:
                    idle_seconds = self._idle_seconds_locked()
                    if idle_seconds is not None:
                        remaining = self.cfg.AUTO_SHUTDOWN_MINUTES * 60 - idle_seconds
                        auto_shutdown_seconds = max(0, round(remaining))

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
                "auto_shutdown_seconds": auto_shutdown_seconds,
            }
            self._status_cache = result
            self._status_cache_at = now
            return result

    def _set_progress(self, message: str, error: Optional[str] = None) -> None:
        with self._lock:
            self._last_message = message
            self._last_error = error
            self._status_cache = None  # forces the next call to recalculate
        logger.info("[controller] %s%s", message, f" (error: {error})" if error else "")

    def action_state(self) -> str:
        with self._lock:
            return self._action_state

    def is_busy(self) -> bool:
        return self.action_state() != ActionState.IDLE

    # ------------------------------------------------------------------
    # Startup sequence
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Triggers the startup sequence in the background.

        Returns False if an action is already in progress.
        """
        with self._lock:
            if self._action_state != ActionState.IDLE:
                return False
            self._action_state = ActionState.STARTING
        self._set_progress("Startup requested...")
        thread = threading.Thread(target=self._start_sequence, daemon=True)
        thread.start()
        return True

    def _start_sequence(self) -> None:
        try:
            if not self.is_pc_reachable():
                self._set_progress("Waking the PC (Wake-on-LAN)...")
                try:
                    self.send_wol()
                except Exception as exc:  # noqa: BLE001
                    self._finish_start(error=f"Failed to send the WOL packet: {exc}")
                    return

                deadline = time.monotonic() + self.cfg.PC_BOOT_TIMEOUT
                while time.monotonic() < deadline:
                    if self.is_pc_reachable():
                        break
                    time.sleep(self.cfg.POLL_INTERVAL)
                else:
                    self._finish_start(
                        error=(
                            "The PC does not respond after the WOL wake-up. "
                            "Make sure Wake-on-LAN is enabled and remains active "
                            "after shutdown (BIOS + network configuration)."
                        )
                    )
                    return

            self._set_progress("PC started, launching the Minecraft server...")

            # The PC may have just booted: the SSH service can take a few more
            # seconds to become ready than the TCP port alone suggests.
            ssh_deadline = time.monotonic() + 60
            last_exc: Optional[Exception] = None
            while time.monotonic() < ssh_deadline:
                try:
                    if self._tmux_session_exists():
                        self._set_progress("The server appears to be running already, continuing monitoring...")
                    else:
                        self.ssh_run(
                            f"tmux new-session -d -s {self.cfg.TMUX_SESSION_NAME} "
                            f"\"sudo -n bash {self.cfg.START_SCRIPT_PATH}\""
                        )
                        self._set_progress("Start script executed, waiting for the Minecraft server...")
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    time.sleep(self.cfg.POLL_INTERVAL)

            if last_exc is not None:
                self._finish_start(error=f"SSH connection failed while trying to launch the script: {last_exc}")
                return

            deadline = time.monotonic() + self.cfg.SERVER_START_TIMEOUT
            while time.monotonic() < deadline:
                if self.query_minecraft() is not None:
                    self._finish_start(message="Minecraft server is available!")
                    return
                time.sleep(self.cfg.POLL_INTERVAL)

            debug_output = self._capture_tmux_output()
            self._finish_start(
                error=(
                    "The Minecraft server did not respond within the expected time. "
                    "Check the start script. Latest lines from the tmux session:\n"
                    f"{debug_output}"
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error during the startup sequence")
            self._finish_start(error=f"Unexpected error: {exc}")

    def _finish_start(self, message: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._lock:
            self._action_state = ActionState.IDLE
        self._set_progress(message or "Startup complete.", error=error)

    # ------------------------------------------------------------------
    # Shutdown sequence
    # ------------------------------------------------------------------

    def stop(self, force: bool = False) -> dict:
        """Triggers the shutdown sequence in the background.

        If players are connected and ``force`` is False, does nothing and
        returns {"confirm_required": True, "online": n}.
        """
        with self._lock:
            if self._action_state != ActionState.IDLE:
                return {"started": False, "reason": "busy"}

            mc = self.query_minecraft()
            if mc is not None and mc.online > 0 and not force:
                return {"confirm_required": True, "online": mc.online, "players": mc.players}

            self._action_state = ActionState.STOPPING

        self._set_progress("Shutdown requested...")
        thread = threading.Thread(target=self._stop_sequence, daemon=True)
        thread.start()
        return {"started": True}

    def _stop_sequence(self) -> None:
        try:
            if not self.is_pc_reachable():
                self._finish_stop(message="The PC is already off.")
                return

            if self._tmux_session_exists():
                self._set_progress("Sending the stop command to the Minecraft server...")
                try:
                    self.ssh_run(
                        f"tmux send-keys -t {self.cfg.TMUX_SESSION_NAME} 'stop' Enter"
                    )
                except Exception as exc:  # noqa: BLE001
                    self._finish_stop(error=f"Unable to send the stop command: {exc}")
                    return

                deadline = time.monotonic() + self.cfg.SERVER_STOP_TIMEOUT
                while time.monotonic() < deadline:
                    if self.query_minecraft() is None:
                        break
                    time.sleep(self.cfg.POLL_INTERVAL)
                else:
                    self._finish_stop(
                        error=(
                            "The Minecraft server did not stop within the expected time. "
                            "The PC was NOT shut down as a safety measure (to avoid losing "
                            "the world save). Try again or check manually."
                        )
                    )
                    return
            else:
                self._set_progress("No active game session, shutting down the PC directly...")

            self._set_progress("Shutting down the PC...")
            try:
                self.ssh_run("sudo -n shutdown -h now", timeout=10)
            except Exception:
                # The connection is usually cut abruptly by the shutdown: normal.
                pass

            deadline = time.monotonic() + self.cfg.PC_SHUTDOWN_TIMEOUT
            while time.monotonic() < deadline:
                if not self.is_pc_reachable():
                    self._finish_stop(message="PC off.")
                    return
                time.sleep(self.cfg.POLL_INTERVAL)

            self._finish_stop(
                error="The PC does not appear to have shut down within the expected time. Check manually."
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error during the shutdown sequence")
            self._finish_stop(error=f"Unexpected error: {exc}")

    def _finish_stop(self, message: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._lock:
            self._action_state = ActionState.IDLE
            self._last_seen_with_players_at = None
        self._set_progress(message or "Shutdown complete.", error=error)

    # ------------------------------------------------------------------
    # Auto-shutdown monitor (inactivity)
    # ------------------------------------------------------------------

    def start_monitor(self) -> None:
        if self._monitor_thread is not None:
            return
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("Auto-shutdown monitor started (%s min of inactivity).", self.cfg.AUTO_SHUTDOWN_MINUTES)

    def stop_monitor(self) -> None:
        self._stop_monitor.set()

    def _monitor_loop(self) -> None:
        while not self._stop_monitor.is_set():
            try:
                self._monitor_tick()
            except Exception:  # noqa: BLE001
                logger.exception("Error in the auto-shutdown monitor loop")
            self._stop_monitor.wait(self.cfg.MONITOR_INTERVAL_SECONDS)

    def _monitor_tick(self) -> None:
        if self.is_busy():
            return
        if not self.is_pc_reachable():
            with self._lock:
                self._last_seen_with_players_at = None
            return

        mc = self.query_minecraft()
        with self._lock:
            self._update_idle_tracking(mc)
            idle_seconds = self._idle_seconds_locked()

        if mc is None or idle_seconds is None:
            return

        idle_minutes = idle_seconds / 60.0
        if idle_minutes >= self.cfg.AUTO_SHUTDOWN_MINUTES:
            logger.info(
                "No players for %.1f minutes (threshold: %s min): automatic shutdown.",
                idle_minutes,
                self.cfg.AUTO_SHUTDOWN_MINUTES,
            )
            self.stop(force=True)

    def _update_idle_tracking(self, mc: Optional[MCStatus]) -> None:
        """Updates the timestamp for "last time players were present".

        Must be called with ``self._lock`` already held. Never triggers the
        shutdown itself (reserved for ``_monitor_tick``): it is used only to
        feed the web-side displayed countdown and inactivity tracking.
        """
        now = datetime.now(timezone.utc)
        if mc is None:
            self._last_seen_with_players_at = None
        elif mc.online > 0:
            self._last_seen_with_players_at = now
        elif self._last_seen_with_players_at is None:
            self._last_seen_with_players_at = now

    def _idle_seconds_locked(self) -> Optional[float]:
        """Number of seconds elapsed since the last known player.

        Must be called with ``self._lock`` already held. Returns None if no
        tracking is active (server not running, or players currently
        connected).
        """
        if self._last_seen_with_players_at is None:
            return None
        now = datetime.now(timezone.utc)
        return (now - self._last_seen_with_players_at).total_seconds()
