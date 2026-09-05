(function () {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

  const els = {
    card: document.getElementById("status-card"),
    label: document.getElementById("status-label"),
    players: document.getElementById("status-players"),
    countdown: document.getElementById("status-countdown"),
    message: document.getElementById("status-message"),
    error: document.getElementById("status-error"),
    btnStart: document.getElementById("btn-start"),
    btnStop: document.getElementById("btn-stop"),
    modal: document.getElementById("confirm-modal"),
    modalMessage: document.getElementById("confirm-message"),
    confirmOk: document.getElementById("confirm-ok"),
    confirmCancel: document.getElementById("confirm-cancel"),
  };

  const STATE_LABELS = {
    pc_off: "PC off",
    server_off: "PC on — Minecraft server off",
    starting: "Starting…",
    stopping: "Shutdown in progress…",
    available: "Server available",
    unknown: "Unknown status",
  };

  const POLL_MS = 5000;
  let polling = null;

  // --- Countdown before automatic shutdown ---
  // Resynchronized on each /api/status response (authoritative value,
  // calculated server-side), then counted down locally every second between
  // refreshes for a smooth display.
  let countdownRemaining = null;
  let countdownInterval = null;

  function formatCountdown(totalSeconds) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  function updateCountdownDisplay() {
    if (countdownRemaining === null) {
      els.countdown.classList.add("hidden");
      return;
    }
    els.countdown.textContent = `⏳ Automatic shutdown in ${formatCountdown(countdownRemaining)} with no connected players`;
    els.countdown.classList.remove("hidden");
  }

  function setCountdown(seconds) {
    if (typeof seconds !== "number") {
      countdownRemaining = null;
      if (countdownInterval) {
        clearInterval(countdownInterval);
        countdownInterval = null;
      }
      updateCountdownDisplay();
      return;
    }

    countdownRemaining = Math.max(0, Math.round(seconds));
    updateCountdownDisplay();

    if (!countdownInterval) {
      countdownInterval = setInterval(() => {
        if (countdownRemaining === null) return;
        countdownRemaining = Math.max(0, countdownRemaining - 1);
        updateCountdownDisplay();
      }, 1000);
    }
  }

  async function apiFetch(url, options = {}) {
    const opts = Object.assign({ headers: {} }, options);
    opts.headers = Object.assign(
      { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      opts.headers
    );
    const response = await fetch(url, opts);
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    return { response, data };
  }

  function renderStatus(status) {
    els.card.dataset.state = status.state;
    els.label.textContent = STATE_LABELS[status.state] || status.state;

    if (status.players && (status.players.online > 0 || status.state === "available")) {
      const names = status.players.names && status.players.names.length
        ? ` (${status.players.names.join(", ")})`
        : "";
      els.players.textContent = `Connected players: ${status.players.online}/${status.players.max}${names}`;
      els.players.classList.remove("hidden");
    } else {
      els.players.textContent = "";
      els.players.classList.add("hidden");
    }

    setCountdown(status.auto_shutdown_seconds);

    els.message.textContent = status.message || "";

    if (status.error) {
      els.error.textContent = status.error;
      els.error.classList.remove("hidden");
    } else {
      els.error.classList.add("hidden");
    }

    const busy = status.state === "starting" || status.state === "stopping";
    els.btnStart.disabled = busy || status.state === "available";
    if (els.btnStop) {
      els.btnStop.disabled = busy || status.state === "pc_off";
    }
  }

  async function refreshStatus() {
    try {
      const { data } = await apiFetch("/api/status");
      if (data) renderStatus(data);
    } catch (err) {
      els.message.textContent = "";
      els.error.textContent = "Unable to contact the Raspberry Pi web server.";
      els.error.classList.remove("hidden");
    }
  }

  function startPolling() {
    if (polling) clearInterval(polling);
    refreshStatus();
    polling = setInterval(refreshStatus, POLL_MS);
  }

  els.btnStart.addEventListener("click", async () => {
    els.btnStart.disabled = true;
    await apiFetch("/api/start", { method: "POST" });
    refreshStatus();
  });

  function openModal(online) {
    els.modalMessage.textContent =
      `${online} player(s) currently connected. Shutting down the server now will disconnect them immediately. Confirm shutdown?`;
    els.modal.classList.remove("hidden");
  }

  function closeModal() {
    els.modal.classList.add("hidden");
  }

  async function doStop(force) {
    if (!els.btnStop) return;
    els.btnStop.disabled = true;
    const { data } = await apiFetch("/api/stop", {
      method: "POST",
      body: JSON.stringify({ force }),
    });

    if (data && data.confirm_required) {
      openModal(data.online);
      els.btnStop.disabled = false;
      return;
    }

    refreshStatus();
  }

  if (els.btnStop) {
    els.btnStop.addEventListener("click", () => doStop(false));
  }
  els.confirmCancel.addEventListener("click", closeModal);
  els.confirmOk.addEventListener("click", () => {
    closeModal();
    doStop(true);
  });

  startPolling();
})();
