(() => {
  "use strict";

  const PAGE_SIZE_STORAGE_KEY = "jatd-remote-page-size";

  const loginScreen = document.getElementById("login-screen");
  const loginUsername = document.getElementById("login-username");
  const loginPassword = document.getElementById("login-password");
  const loginError = document.getElementById("login-error");
  const loginSubmit = document.getElementById("login-submit");
  const appRoot = document.getElementById("app-root");
  const logoutBtn = document.getElementById("logout-btn");
  const roleBadge = document.getElementById("role-badge");

  const offlineBanner = document.getElementById("offline-banner");
  const offlineMessage = document.getElementById("offline-message");
  const offlineRefreshBtn = document.getElementById("offline-refresh-btn");

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
  const pageSizeSelect = document.getElementById("page-size-select");
  const pagePrevBtn = document.getElementById("page-prev");
  const pageNextBtn = document.getElementById("page-next");
  const pageIndicator = document.getElementById("page-indicator");
  const localAudio = document.getElementById("local-audio");

  let currentRole = null;
  let currentPath = "";
  let pageOffset = 0;
  let pageTotal = 0;
  let agentConnected = false;
  let localPreviewActive = false;
  let localPreviewBuffering = false;
  let lastHostIsLive = true;
  let guestLiveIntent = false;
  let lastHostLoopMode = "off";
  let localLoopMode = "off";
  let lastHostPlayState = "stopped";
  let currentLibraryItems = [];
  let localPreviewIndex = -1;
  let statusSocket = null;

  // -------------------------------------------------------------------------
  // Session / login
  // -------------------------------------------------------------------------

  function showLoginScreen() {
    loginScreen.classList.remove("hidden");
    appRoot.classList.add("hidden");
    if (statusSocket) {
      statusSocket.onclose = null;
      statusSocket.close();
      statusSocket = null;
    }
  }

  function showAppRoot() {
    loginScreen.classList.add("hidden");
    appRoot.classList.remove("hidden");
  }

  function updateRoleBadge() {
    if (currentRole === "admin") {
      roleBadge.textContent = "Admin";
      roleBadge.classList.remove("hidden");
    } else if (currentRole === "user") {
      roleBadge.textContent = "User";
      roleBadge.classList.remove("hidden");
    } else {
      roleBadge.classList.add("hidden");
    }
  }

  async function checkSession() {
    try {
      const response = await fetch("/api/session");
      const data = await response.json();
      if (data.authenticated) {
        currentRole = data.role;
        updateRoleBadge();
        showAppRoot();
        initApp();
        return;
      }
    } catch (err) {
      // fall through to login screen
    }
    currentRole = null;
    updateRoleBadge();
    showLoginScreen();
  }

  async function submitLogin() {
    loginError.textContent = "";
    const username = loginUsername.value.trim();
    const password = loginPassword.value;
    if (!username || !password) {
      loginError.textContent = "Enter a username and password.";
      return;
    }
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        loginError.textContent = "Invalid username or password.";
        return;
      }
      currentRole = data.role;
      loginPassword.value = "";
      updateRoleBadge();
      showAppRoot();
      initApp();
    } catch (err) {
      loginError.textContent = "Could not reach the server.";
    }
  }

  async function logout() {
    try {
      await fetch("/api/logout", { method: "POST" });
    } catch (err) {
      // ignore - we're logging out client-side regardless
    }
    currentRole = null;
    updateRoleBadge();
    showLoginScreen();
  }

  loginSubmit.addEventListener("click", submitLogin);
  loginPassword.addEventListener("keydown", (event) => {
    if (event.key === "Enter") submitLogin();
  });
  logoutBtn.addEventListener("click", logout);

  // -------------------------------------------------------------------------
  // Offline / agent-connection state
  // -------------------------------------------------------------------------

  function setAgentConnected(connected, offlineText) {
    const changed = connected !== agentConnected;
    agentConnected = connected;
    if (connected) {
      offlineBanner.classList.add("hidden");
    } else {
      offlineMessage.textContent = offlineText || "No jingle machine currently connected for control.";
      offlineBanner.classList.remove("hidden");
      if (localPreviewActive) {
        stopLocalPreview();
      }
    }
    updateControlsEnabled();
    if (changed) {
      fetchLibrary();
    }
  }

  function updateControlsEnabled() {
    const enabled = agentConnected;
    btnPause.disabled = !enabled && !localPreviewActive;
    btnStop.disabled = !enabled && !localPreviewActive;
    btnLoop.disabled = !enabled;
    btnLive.disabled = !enabled;
  }

  offlineRefreshBtn.addEventListener("click", () => {
    refreshStatusOnce();
    fetchLibrary();
  });

  // -------------------------------------------------------------------------
  // Now-playing / status
  // -------------------------------------------------------------------------

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
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (response.status === 401) {
      showLoginScreen();
      return { ok: false, error: "Not signed in." };
    }
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      data = { ok: false, error: "Invalid response from server." };
    }
    if (response.status === 409) {
      showStatus("The jingle machine is not currently connected.", true);
      return data;
    }
    if (!data.ok) {
      showStatus(data.error || "Request failed.", true);
    }
    return data;
  }

  function applyStatus(status) {
    setAgentConnected(Boolean(status.agent_connected), status.message);
    if (!agentConnected) {
      return;
    }
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
    if (localPreviewBuffering) {
      btnPause.textContent = "Loading\u2026";
      return;
    }
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
    statusSocket = ws;
    ws.onmessage = (event) => {
      try {
        applyStatus(JSON.parse(event.data));
      } catch (err) {
        // ignore malformed frames
      }
    };
    ws.onclose = () => {
      if (statusSocket === ws) {
        window.setTimeout(connectWebSocket, 2000);
      }
    };
    ws.onerror = () => ws.close();
  }

  async function refreshStatusOnce() {
    try {
      const response = await fetch("/api/status");
      if (response.status === 401) {
        showLoginScreen();
        return;
      }
      applyStatus(await response.json());
    } catch (err) {
      // handled by websocket retry
    }
  }

  // -------------------------------------------------------------------------
  // Playback (live/admin control + guest local preview)
  // -------------------------------------------------------------------------

  function triggerPlay(item) {
    if (!agentConnected) {
      return;
    }
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

  function startLocalPreview(item) {
    // The <audio> element fetches directly from /api/audio so the browser can progressively
    // buffer/stream large jingles instead of waiting for a full blob download before playback.
    localPreviewBuffering = true;
    localAudio.src = `/api/audio?path=${encodeURIComponent(item.path)}`;
    localAudio.loop = localLoopMode === "loop";
    localPreviewActive = true;
    localPreviewIndex = currentLibraryItems.findIndex((entry) => entry.path === item.path);
    currentPath = item.path;
    npName.textContent = `${item.name} (buffering\u2026)`;
    npName.classList.add("buffering");
    highlightPlayingRow();
    updateControlsEnabled();
    renderPlayButton();
    localAudio.play().catch(() => {
      localPreviewBuffering = false;
      localPreviewActive = false;
      npName.classList.remove("buffering");
      updateControlsEnabled();
      renderPlayButton();
      showStatus("Could not preview jingle in browser.", true);
    });
  }

  function stopLocalPreview() {
    localAudio.pause();
    localAudio.removeAttribute("src");
    localAudio.load();
    localPreviewActive = false;
    localPreviewBuffering = false;
    localPreviewIndex = -1;
    npName.classList.remove("buffering");
    updateControlsEnabled();
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

  // Fired whenever playback stalls waiting on more chunks to arrive (initial
  // load or mid-playback rebuffer on a slow connection) - show a graceful
  // loading state instead of an error, and clear it once enough data is buffered.
  localAudio.addEventListener("waiting", () => {
    if (!localPreviewActive) return;
    localPreviewBuffering = true;
    npName.classList.add("buffering");
    renderPlayButton();
  });

  localAudio.addEventListener("playing", () => {
    if (!localPreviewActive) return;
    localPreviewBuffering = false;
    npName.classList.remove("buffering");
    const item = currentLibraryItems.find((entry) => entry.path === currentPath);
    npName.textContent = item ? `${item.name} (local preview)` : "Local preview";
    renderPlayButton();
  });

  localAudio.addEventListener("error", () => {
    if (!localPreviewActive) return;
    localPreviewBuffering = false;
    npName.classList.remove("buffering");
    localPreviewActive = false;
    updateControlsEnabled();
    renderPlayButton();
    showStatus("Preview playback failed (connection issue) - try again.", true);
  });

  // -------------------------------------------------------------------------
  // Library
  // -------------------------------------------------------------------------

  function renderLibrary(items) {
    currentLibraryItems = items;
    libraryList.innerHTML = "";
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "library-item";
      row.dataset.path = item.path || "";
      row.title = agentConnected ? "Double-click to play" : "";

      const info = document.createElement("div");
      info.className = "info";
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = item.name;
      const meta = document.createElement("div");
      meta.className = "meta";
      const folderPart = item.folder ? `${item.folder} • ` : "";
      meta.textContent = `${folderPart}${formatSeconds(item.duration_seconds)}`;
      info.appendChild(name);
      info.appendChild(meta);
      row.appendChild(info);

      if (agentConnected) {
        const playBtn = document.createElement("button");
        playBtn.textContent = "Play";
        playBtn.addEventListener("click", () => triggerPlay(item));
        row.appendChild(playBtn);

        // Desktop convenience: double-click anywhere in the row plays it (mobile keeps the explicit Play tap target).
        row.addEventListener("dblclick", (event) => {
          if (event.target.closest("button")) {
            return;
          }
          triggerPlay(item);
        });
      }

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
      if (response.status === 401) {
        showLoginScreen();
        return;
      }
      const data = await response.json();
      setAgentConnected(Boolean(data.agent_connected));
      pageTotal = data.total || (data.items || []).length;
      renderLibrary(data.items || []);
      const shown = (data.items || []).length;
      if (agentConnected) {
        librarySummary.textContent = `Showing ${shown} of ${pageTotal} jingles`;
      } else if (shown === 0) {
        librarySummary.textContent = data.message || "No jingle machine currently connected, and no cached library is available.";
      } else {
        librarySummary.textContent = `Showing ${shown} cached jingle(s) - jingle machine offline.`;
      }
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

  // -------------------------------------------------------------------------
  // Now-playing controls
  // -------------------------------------------------------------------------

  btnPause.addEventListener("click", () => {
    if (localPreviewActive) {
      if (localAudio.paused) {
        localAudio.play();
      } else {
        localAudio.pause();
      }
      return;
    }
    if (!agentConnected) return;
    callControl("/api/playback/pause");
  });
  btnStop.addEventListener("click", () => {
    if (localPreviewActive) {
      stopLocalPreview();
      return;
    }
    if (!agentConnected) return;
    callControl("/api/playback/stop");
  });

  btnLoop.addEventListener("click", () => {
    if (!agentConnected) return;
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
    if (!agentConnected) return;
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

  // -------------------------------------------------------------------------
  // App init (called once per successful login/session check)
  // -------------------------------------------------------------------------

  function initApp() {
    const storedPageSize = localStorage.getItem(PAGE_SIZE_STORAGE_KEY);
    if (storedPageSize && pageSizeSelect.querySelector(`option[value="${storedPageSize}"]`)) {
      pageSizeSelect.value = storedPageSize;
    }
    updateControlsEnabled();
    refreshStatusOnce();
    connectWebSocket();
    fetchLibrary();
    renderModeButton();
    renderLoopButton();
    renderPlayButton();
  }

  checkSession();
})();
