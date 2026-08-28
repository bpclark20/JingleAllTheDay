(() => {
  "use strict";

  const PIN_STORAGE_KEY = "jatd-remote-pin";
  const npName = document.getElementById("np-name");
  const npFill = document.getElementById("np-fill");
  const npPosition = document.getElementById("np-position");
  const npDuration = document.getElementById("np-duration");
  const btnPause = document.getElementById("btn-pause");
  const btnStop = document.getElementById("btn-stop");
  const btnLoop = document.getElementById("btn-loop");
  const btnLive = document.getElementById("btn-live");
  const statusLine = document.getElementById("status-line");
  const searchInput = document.getElementById("search-input");
  const libraryList = document.getElementById("library-list");
  const librarySummary = document.getElementById("library-summary");
  const pinBtn = document.getElementById("pin-btn");
  const pinModal = document.getElementById("pin-modal");
  const pinInput = document.getElementById("pin-input");
  const pinSave = document.getElementById("pin-save");
  const pinCancel = document.getElementById("pin-cancel");
  const pageSizeSelect = document.getElementById("page-size-select");
  const pagePrevBtn = document.getElementById("page-prev");
  const pageNextBtn = document.getElementById("page-next");
  const pageIndicator = document.getElementById("page-indicator");
  const roleBadge = document.getElementById("role-badge");
  const localAudio = document.getElementById("local-audio");

  const PAGE_SIZE_STORAGE_KEY = "jatd-remote-page-size";
  let currentPath = "";
  let pageOffset = 0;
  let pageTotal = 0;
  let currentRole = null;
  let localPreviewActive = false;
  let lastHostIsLive = true;
  let guestLiveIntent = false;
  let lastHostLoopMode = "off";
  let localLoopMode = "off";
  let lastHostPlayState = "stopped";
  let currentLibraryItems = [];
  let localPreviewIndex = -1;

  function getPin() {
    return localStorage.getItem(PIN_STORAGE_KEY) || "";
  }

  function setPin(value) {
    localStorage.setItem(PIN_STORAGE_KEY, value);
  }

  function updateRoleBadge() {
    if (currentRole === "admin") {
      roleBadge.textContent = "Admin";
      roleBadge.classList.remove("hidden");
    } else if (currentRole === "user") {
      roleBadge.textContent = "Guest";
      roleBadge.classList.remove("hidden");
    } else {
      roleBadge.classList.add("hidden");
    }
  }

  async function resolveRole() {
    const pin = getPin();
    if (!pin) {
      currentRole = null;
      updateRoleBadge();
      renderModeButton();
      renderLoopButton();
      renderPlayButton();
      return;
    }
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      const data = await response.json();
      currentRole = data.ok ? data.role : null;
    } catch (err) {
      currentRole = null;
    }
    updateRoleBadge();
    renderModeButton();
    renderLoopButton();
    renderPlayButton();
  }

  function formatSeconds(seconds) {
    const total = Math.max(0, Math.floor(seconds || 0));
    const minutes = Math.floor(total / 60);
    const secs = total % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  }

  function showStatus(message, isError) {
    statusLine.textContent = message;
    statusLine.style.color = isError ? "#e57373" : "";
    if (message) {
      window.clearTimeout(showStatus._t);
      showStatus._t = window.setTimeout(() => {
        statusLine.textContent = "";
      }, 4000);
    }
  }

  async function callControl(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Remote-Pin": getPin(),
      },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = { ok: false, error: "Invalid response from server." };
    }
    if (response.status === 403) {
      showStatus(data.error || "PIN required or incorrect.", true);
      openPinModal();
      return data;
    }
    if (!data.ok) {
      showStatus(data.error || "Request failed.", true);
    }
    return data;
  }

  function applyStatus(status) {
    const isLive = status.is_live_mode !== false;
    if (currentRole !== "admin" && guestLiveIntent && !isLive) {
      // Host dropped out of Live while this guest was mirroring it - fall back to local preview
      // so further Plays go to the browser instead of the host's (now Preview) output device.
      guestLiveIntent = false;
      showStatus("Host switched to Preview - your session is back to local preview mode.", true);
    }
    lastHostIsLive = isLive;

    if (localPreviewActive) {
      // Local preview owns the Now Playing UI - the host's real status is ignored until it ends/stops.
      renderModeButton();
      renderLoopButton();
      renderPlayButton();
      return;
    }
    currentPath = status.current_path || "";
    npName.textContent = status.current_name || "Nothing playing";
    const duration = status.duration_seconds || 0;
    const position = status.position_seconds || 0;
    const pct = duration > 0 ? Math.min(100, (position / duration) * 100) : 0;
    npFill.style.width = `${pct}%`;
    npPosition.textContent = formatSeconds(position);
    npDuration.textContent = formatSeconds(duration);

    lastHostLoopMode = status.loop_mode || "off";
    renderLoopButton();
    renderModeButton();
    lastHostPlayState = status.state || "stopped";
    renderPlayButton();

    highlightPlayingRow();
  }

  function isMirroringHost() {
    return currentRole === "admin" || guestLiveIntent;
  }

  function renderLoopButton() {
    const mode = isMirroringHost() ? lastHostLoopMode : localLoopMode;
    btnLoop.dataset.mode = mode;
    btnLoop.textContent =
      mode === "loop" ? "Loop: On" : mode === "continuous" ? "Loop: Continuous" : "Loop: Off";
  }

  function renderPlayButton() {
    let state;
    if (isMirroringHost()) {
      state = lastHostPlayState;
    } else if (localPreviewActive) {
      state = localAudio.paused ? "paused" : "playing";
    } else {
      state = "stopped";
    }
    btnPause.textContent = state === "playing" ? "Pause" : state === "paused" ? "Resume" : "Play Selected";
  }

  function renderModeButton() {
    const isLive = currentRole === "admin" ? lastHostIsLive : guestLiveIntent;
    btnLive.dataset.live = String(isLive);
    btnLive.textContent = isLive ? "Mode: Live" : "Mode: Preview";
  }

  function highlightPlayingRow() {
    document.querySelectorAll(".library-item").forEach((el) => {
      el.classList.toggle("playing", el.dataset.path === currentPath);
    });
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/status`);
    ws.onmessage = (event) => {
      try {
        applyStatus(JSON.parse(event.data));
      } catch (err) {
        // ignore malformed frames
      }
    };
    ws.onclose = () => {
      window.setTimeout(connectWebSocket, 2000);
    };
    ws.onerror = () => ws.close();
  }

  async function refreshStatusOnce() {
    try {
      const response = await fetch("/api/status");
      applyStatus(await response.json());
    } catch (err) {
      // handled by websocket retry
    }
  }

  function triggerPlay(item) {
    if (currentRole !== "admin" && !guestLiveIntent) {
      startLocalPreview(item);
      return;
    }
    callControl("/api/playback/play", {
      path: item.path,
      loop_mode: btnLoop.dataset.mode || "off",
      live: btnLive.dataset.live !== "false",
    });
  }

  async function startLocalPreview(item) {
    try {
      const response = await fetch(`/api/audio?path=${encodeURIComponent(item.path)}`, {
        headers: { "X-Remote-Pin": getPin() },
      });
      if (response.status === 403) {
        showStatus("PIN required or incorrect.", true);
        openPinModal();
        return;
      }
      if (!response.ok) {
        showStatus("Could not load jingle for preview.", true);
        return;
      }
      const blob = await response.blob();
      if (localAudio.src) {
        URL.revokeObjectURL(localAudio.src);
      }
      localAudio.src = URL.createObjectURL(blob);
      localAudio.loop = localLoopMode === "loop";
      localPreviewActive = true;
      localPreviewIndex = currentLibraryItems.findIndex((entry) => entry.path === item.path);
      currentPath = item.path;
      npName.textContent = `${item.name} (local preview)`;
      highlightPlayingRow();
      await localAudio.play();
    } catch (err) {
      showStatus("Could not preview jingle in browser.", true);
    }
  }

  function stopLocalPreview() {
    localAudio.pause();
    localAudio.currentTime = 0;
    localPreviewActive = false;
    localPreviewIndex = -1;
    refreshStatusOnce();
  }

  localAudio.addEventListener("timeupdate", () => {
    if (!localPreviewActive) return;
    const duration = localAudio.duration || 0;
    const position = localAudio.currentTime || 0;
    const pct = duration > 0 ? Math.min(100, (position / duration) * 100) : 0;
    npFill.style.width = `${pct}%`;
    npPosition.textContent = formatSeconds(position);
    npDuration.textContent = formatSeconds(duration);
  });

  localAudio.addEventListener("ended", () => {
    if (!localPreviewActive) return;
    if (localLoopMode === "continuous" && localPreviewIndex >= 0 && localPreviewIndex + 1 < currentLibraryItems.length) {
      startLocalPreview(currentLibraryItems[localPreviewIndex + 1]);
      return;
    }
    stopLocalPreview();
  });

  localAudio.addEventListener("play", renderPlayButton);
  localAudio.addEventListener("pause", renderPlayButton);

  function renderLibrary(items) {
    currentLibraryItems = items;
    libraryList.innerHTML = "";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "library-item";
      row.dataset.path = item.path;
      row.title = "Double-click to play";

      const info = document.createElement("div");
      info.className = "info";
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = item.name;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `${item.folder} • ${formatSeconds(item.duration_seconds)}`;
      info.appendChild(name);
      info.appendChild(meta);

      const playBtn = document.createElement("button");
      playBtn.textContent = "Play";
      playBtn.addEventListener("click", () => triggerPlay(item));

      // Desktop convenience: double-click anywhere in the row plays it (mobile keeps the explicit Play tap target).
      row.addEventListener("dblclick", (event) => {
        if (event.target.closest("button")) {
          return;
        }
        triggerPlay(item);
      });

      row.appendChild(info);
      row.appendChild(playBtn);
      libraryList.appendChild(row);
    }
    highlightPlayingRow();
  }

  function currentPageSize() {
    return parseInt(pageSizeSelect.value, 10) || 0;
  }

  function updatePagerControls() {
    const limit = currentPageSize();
    const totalPages = limit > 0 ? Math.max(1, Math.ceil(pageTotal / limit)) : 1;
    const currentPage = limit > 0 ? Math.floor(pageOffset / limit) + 1 : 1;
    pageIndicator.textContent = `Page ${currentPage} of ${totalPages}`;
    pagePrevBtn.disabled = pageOffset <= 0;
    pageNextBtn.disabled = limit <= 0 || pageOffset + limit >= pageTotal;
  }

  let searchDebounce = null;
  async function fetchLibrary() {
    const search = encodeURIComponent(searchInput.value.trim());
    const limit = currentPageSize();
    try {
      const response = await fetch(`/api/library?search=${search}&limit=${limit}&offset=${pageOffset}`);
      const data = await response.json();
      pageTotal = data.total || 0;
      renderLibrary(data.items || []);
      const shown = (data.items || []).length;
      librarySummary.textContent = `Showing ${shown} of ${pageTotal} jingles`;
      updatePagerControls();
    } catch (err) {
      librarySummary.textContent = "Could not load library.";
    }
  }

  searchInput.addEventListener("input", () => {
    window.clearTimeout(searchDebounce);
    searchDebounce = window.setTimeout(() => {
      pageOffset = 0;
      fetchLibrary();
    }, 250);
  });

  pageSizeSelect.addEventListener("change", () => {
    localStorage.setItem(PAGE_SIZE_STORAGE_KEY, pageSizeSelect.value);
    pageOffset = 0;
    fetchLibrary();
  });

  pagePrevBtn.addEventListener("click", () => {
    const limit = currentPageSize();
    if (limit <= 0) return;
    pageOffset = Math.max(0, pageOffset - limit);
    fetchLibrary();
  });

  pageNextBtn.addEventListener("click", () => {
    const limit = currentPageSize();
    if (limit <= 0) return;
    if (pageOffset + limit >= pageTotal) return;
    pageOffset += limit;
    fetchLibrary();
  });

  btnPause.addEventListener("click", () => {
    if (localPreviewActive) {
      if (localAudio.paused) {
        localAudio.play();
      } else {
        localAudio.pause();
      }
      return;
    }
    callControl("/api/playback/pause");
  });
  btnStop.addEventListener("click", () => {
    if (localPreviewActive) {
      stopLocalPreview();
      return;
    }
    callControl("/api/playback/stop");
  });

  btnLoop.addEventListener("click", () => {
    const order = ["off", "loop", "continuous"];
    const next = order[(order.indexOf(btnLoop.dataset.mode || "off") + 1) % order.length];
    if (!isMirroringHost()) {
      localLoopMode = next;
      renderLoopButton();
      if (localPreviewActive) {
        localAudio.loop = next === "loop";
      }
      return;
    }
    callControl("/api/playback/mode", { loop_mode: next });
  });

  btnLive.addEventListener("click", async () => {
    const nextLive = btnLive.dataset.live === "false";
    const data = await callControl("/api/playback/output", { live: nextLive });
    if (!data.ok) {
      return;
    }
    if (currentRole === "admin") {
      lastHostIsLive = nextLive;
    } else {
      guestLiveIntent = nextLive;
      if (guestLiveIntent && localPreviewActive) {
        // Switched to mirroring the host - the local browser-only preview must stop.
        stopLocalPreview();
      }
    }
    renderModeButton();
    renderLoopButton();
    renderPlayButton();
  });

  function openPinModal() {
    pinInput.value = getPin();
    pinModal.classList.remove("hidden");
  }

  function closePinModal() {
    pinModal.classList.add("hidden");
  }

  pinBtn.addEventListener("click", openPinModal);
  pinCancel.addEventListener("click", closePinModal);
  pinSave.addEventListener("click", () => {
    setPin(pinInput.value.trim());
    closePinModal();
    showStatus("PIN saved.");
    resolveRole();
  });

  const storedPageSize = localStorage.getItem(PAGE_SIZE_STORAGE_KEY);
  if (storedPageSize && pageSizeSelect.querySelector(`option[value="${storedPageSize}"]`)) {
    pageSizeSelect.value = storedPageSize;
  }

  refreshStatusOnce();
  connectWebSocket();
  fetchLibrary();
  resolveRole();
  renderModeButton();
  renderLoopButton();
  renderPlayButton();
  if (!getPin()) {
    openPinModal();
  }
})();
