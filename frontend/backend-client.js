/* ============================================================
   POSTGRESQL BACKEND CLIENT — Classroom Digital Twin
   ------------------------------------------------------------
   This phase has NO AI / no Raspberry Pi. The manual simulation
   controls of the 3D scene are the single event producer:

     manual click  ->  POST /api/ingest/snapshot  (2 Hz heartbeat)
                   ->  FastAPI OccupancyManager (2 s / 3 s)
                   ->  PostgreSQL occupancy_records
                   ->  WebSocket /ws
                   ->  avatars + seat state + statistics

   Modes
   -----
   TEST : manual simulation controls enabled. Every click updates
          the simulated detection set; a heartbeat keeps sending it
          so the backend can apply the 2 s / 3 s hysteresis.
   LIVE : external producer mode (future YOLO). Manual controls are
          disabled and the UI only listens to the backend.

   The producer is deliberately isolated in this file. When YOLO is
   added later it can replace the TEST producer by POSTing the same
   snapshots; the database and occupancy logic do not change.

   The API origin defaults to the page origin (FastAPI serves the
   frontend). Override with ?api=http://host:port or
   localStorage.setItem('twinApiBase', '...').
   ============================================================ */
(function () {
  'use strict';

  if (window.__TWIN_DB_CLIENT__) return;
  window.__TWIN_DB_CLIENT__ = true;

  var STORAGE_API = 'twinApiBase';
  var STORAGE_MODE = 'twinBackendMode';

  /*
   * Only one browser tab may act as the TEST producer. Two tabs would each
   * POST a full snapshot every 250 ms; the backend would see the seat present
   * and absent alternately, so the 2 s / 3 s delays would never elapse and
   * the seat would stay yellow with no avatar and no database record.
   * A short lease in localStorage elects a single producer tab.
   */
  var PRODUCER_KEY = 'twinSimProducer';
  var PRODUCER_TTL_MS = 3000;
  var PRODUCER_RENEW_MS = 1000;
  var TAB_ID = 'tab-' + Math.random().toString(36).slice(2) + '-' + Date.now().toString(36);

  /* The backend needs continuous samples for the 2 s / 3 s delays. */
  var SIM_INTERVAL_MS = 250;
  /* Slightly longer than EMPTY_CONFIRM_SECONDS, used when leaving TEST. */
  var SIM_CLEANUP_MS = 4500;

  var params = new URLSearchParams(location.search);

  if (params.get('api')) localStorage.setItem(STORAGE_API, params.get('api'));

  var base = params.get('api') || localStorage.getItem(STORAGE_API) || '';
  if (!base) {
    base = (location.protocol === 'file:' || location.origin === 'null')
      ? 'http://127.0.0.1:8000'
      : location.origin;
  }
  base = base.replace(/\/+$/, '');

  var API = base + '/api';
  var WS_URL = base.replace(/^http/i, 'ws') + '/ws';

  /* All timestamps are school-local (Africa/Casablanca by default). The
     browser may run in another timezone, so never rely on the browser's
     default formatting for the session clock and history. */
  var TZ = 'Africa/Casablanca';

  var S = {
    mode: localStorage.getItem(STORAGE_MODE) === 'live' ? 'live' : 'test',
    ws: null,
    connected: false,
    timer: null,
    delay: 1000,
    session: null,
    context: null,
    stats: null,
    historyOpen: false,
    historyTimer: null,
    presentSet: new Set(),   // detected/present seats (backend manager)
    occupiedSet: new Set(),  // seats with an OPEN database record
    simSeats: new Set(),     // desired detection set of the manual simulation
    simTimer: null,
    simInFlight: false,
    simReady: false,
    simInitDone: false,
    simCleanup: false,
    simCleanupTimer: null,
    simCleanupInterval: null
  };

  function $(id) { return document.getElementById(id); }

  function setStatus(text, color) {
    var dot = $('db-dot');
    var label = $('db-status');
    if (dot) dot.style.background = color || '#e67e22';
    if (label) label.textContent = text;
  }

  /*
   * The backend always sends ISO timestamps that already carry the school
   * timezone offset (e.g. 2026-11-03T10:41:35+01:00). Read the wall-clock
   * part directly instead of converting through the browser timezone: the
   * PC may run in another timezone and some browser tzdata builds disagree
   * with Africa/Casablanca for future dates.
   */
  function fmtTime(value) {
    if (!value) return '—';
    var match = /T(\d{2}:\d{2})/.exec(String(value));
    return match ? match[1] : '—';
  }

  function fmtDate(value) {
    if (!value) return '—';
    var match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
    return match ? match[3] + '/' + match[2] : '—';
  }

  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    var total = Math.max(0, Math.round(seconds));
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    if (h > 0) return h + 'h ' + String(m).padStart(2, '0') + 'm';
    return m + 'm ' + String(s).padStart(2, '0') + 's';
  }

  async function apiGet(path) {
    var response = await fetch(API + path, { headers: { 'Accept': 'application/json' } });
    if (!response.ok) throw new Error('HTTP ' + response.status + ' on ' + path);
    return response.json();
  }

  async function apiPost(path, body) {
    var response = await fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error('HTTP ' + response.status + ' on ' + path);
    return response.json();
  }

  /* ---------------------------------------------------------- UI */
  function buildUI() {
    var css = [
      '#db-ui{position:fixed;top:16px;right:16px;z-index:21;font-family:Arial,sans-serif}',
      '#db-panel{display:none;width:330px;max-height:calc(100vh - 90px);overflow:auto;',
      'background:rgba(20,16,28,.94);border:1px solid #4a3d62;border-radius:12px;padding:14px;',
      'color:#eee;backdrop-filter:blur(8px);box-shadow:0 8px 30px rgba(0,0,0,.45)}',
      '#db-panel.open{display:block}',
      '#db-panel h3{margin:0 0 8px;font-size:14px;color:#c39bf3}',
      '#db-toggle{background:#2b2238;color:#c39bf3;border:1px solid #4a3d62;border-radius:10px;',
      'padding:8px 14px;cursor:pointer;font-size:13px;float:right}',
      '.db-status{display:flex;align-items:center;gap:8px;margin-bottom:10px;font-size:11.5px;color:#cfc6df}',
      '#db-dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#e67e22;flex:0 0 auto}',
      '.db-modes{display:flex;gap:6px;margin:0 0 10px}',
      '.db-modes button{flex:1;background:#241d33;color:#bbb;border:1px solid #443a5e;border-radius:6px;',
      'padding:7px 4px;cursor:pointer;font-size:11px;font-weight:bold}',
      '.db-modes button.sel{background:#1e4fd6;color:#fff;border-color:#6f8cff}',
      '.db-kv{font-size:11.5px;color:#cfc6df;margin-bottom:8px;line-height:1.5}',
      '.db-kv b{color:#c39bf3}',
      '.db-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin:8px 0 10px}',
      '.db-stat{background:#241d33;border:1px solid #3b3152;border-radius:6px;padding:7px 3px;text-align:center}',
      '.db-stat b{display:block;font-size:15px;color:#fff}',
      '.db-stat span{font-size:9px;color:#8f83a8;text-transform:uppercase;letter-spacing:.4px}',
      '.db-actions{display:flex;gap:6px;margin:6px 0 8px}',
      '.db-actions button{flex:1;background:#3a2f4e;color:#eee;border:1px solid #5a4a78;border-radius:6px;',
      'padding:6px 8px;cursor:pointer;font-size:11px}',
      '#db-history-wrap{display:none;margin-top:6px}',
      '#db-history-wrap.open{display:block}',
      '.db-history{max-height:190px;overflow:auto;border:1px solid #3b3152;border-radius:6px}',
      '.db-history table{width:100%;border-collapse:collapse;font-size:10px}',
      '.db-history th{position:sticky;top:0;background:#2b2238;color:#c39bf3;padding:5px 4px;text-align:left;font-weight:600}',
      '.db-history td{padding:4px;border-top:1px solid #332a47;color:#cfc6df;white-space:nowrap}',
      '.db-history .open-row td{color:#7ddc9b}',
      '.db-hint{font-size:10px;color:#8f83a8;line-height:1.5;margin-top:8px}',
      'body .twin-seat.db-occ{background:#1e4fd6;color:#fff;border-color:#6f8cff}',
      /* waiting for the backend to confirm the 2 s / 3 s delay */
      'body .twin-seat.sim-pending{background:#8a6d1f;color:#fff;border-color:#f1c40f}',
      'body .twin-seat.sim-pending:disabled{opacity:1}'
    ].join('');

    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    var ui = document.createElement('div');
    ui.id = 'db-ui';
    ui.innerHTML = [
      '<div id="db-panel" class="open">',
      '  <h3>🗄️ PostgreSQL Backend</h3>',
      '  <button id="db-toggle" title="Afficher / masquer">—</button>',
      '  <div class="db-status"><span id="db-dot"></span><span id="db-status">Initialisation…</span></div>',
      '  <div class="db-modes">',
      '    <button class="db-mode" data-mode="live">LIVE</button>',
      '    <button class="db-mode" data-mode="test">TEST</button>',
      '  </div>',
      '  <div class="db-kv" id="db-session">Session : —</div>',
      '  <div class="db-stats">',
      '    <div class="db-stat"><b id="db-total">—</b><span>Total</span></div>',
      '    <div class="db-stat"><b id="db-occupied">—</b><span>Occupés</span></div>',
      '    <div class="db-stat"><b id="db-empty">—</b><span>Libres</span></div>',
      '    <div class="db-stat"><b id="db-pct">—</b><span>%</span></div>',
      '  </div>',
      '  <div class="db-actions">',
      '    <button id="db-history-btn">Historique</button>',
      '    <button id="db-refresh">Rafraîchir</button>',
      '  </div>',
      '  <div id="db-history-wrap"><div class="db-history" id="db-history"></div></div>',
      '  <div class="db-hint" id="db-hint"></div>',
      '</div>'
    ].join('');
    document.body.appendChild(ui);

    document.querySelectorAll('.db-mode').forEach(function (button) {
      button.onclick = function () { setMode(button.getAttribute('data-mode')); };
    });

    $('db-toggle').onclick = function () {
      var panel = $('db-panel');
      panel.classList.toggle('open');
      $('db-toggle').textContent = panel.classList.contains('open') ? '—' : '🗄️';
    };

    $('db-refresh').onclick = function () {
      refresh();
      if (S.historyOpen) loadHistory();
    };

    $('db-history-btn').onclick = function () {
      S.historyOpen = !S.historyOpen;
      $('db-history-wrap').classList.toggle('open', S.historyOpen);
      if (S.historyOpen) loadHistory();
    };
  }

  function updateModeButtons() {
    document.querySelectorAll('.db-mode').forEach(function (button) {
      button.classList.toggle('sel', button.getAttribute('data-mode') === S.mode);
    });
    var hint = $('db-hint');
    if (hint) {
      hint.innerHTML = S.mode === 'live'
        ? 'LIVE : producteur externe (YOLO à venir). Commandes manuelles désactivées ; les événements viennent du backend.'
        : 'TEST : la simulation manuelle envoie les événements au backend. <b>2 s</b> de présence → enregistrement PostgreSQL, <b>3 s</b> d\'absence → clôture. Jaune = en attente de confirmation.';
    }
  }

  /* ------------------------------------------------------ rendering */
  function paintSeatButtons(seatCodes) {
    var set = new Set(seatCodes || []);
    document.querySelectorAll('.twin-seat').forEach(function (button) {
      var id = (button.textContent || '').trim();
      button.classList.toggle('db-occ', set.has(id));
    });
  }

  /* Yellow while the desired simulation state and the backend presence
     disagree (i.e. during the 2 s / 3 s confirmation windows). */
  function refreshPendingClasses() {
    document.querySelectorAll('.twin-seat').forEach(function (button) {
      var id = (button.textContent || '').trim();
      var waiting = S.mode === 'test' && (S.simSeats.has(id) !== S.presentSet.has(id));
      button.classList.toggle('sim-pending', waiting);
    });
  }

  function renderSession(session, context) {
    var target = $('db-session');
    if (!target) return;
    var text;
    if (session) {
      text = 'Session <b>' + session.session_number + '</b> · ' +
        session.start_time + '–' + session.end_time;
    } else if (context && context.is_break) {
      text = '<b>Récréation</b> · 13:00–13:30';
    } else if (context && context.before_hours) {
      text = '<b>Hors session</b> · avant 08:00';
    } else if (context && context.after_hours) {
      text = '<b>Hors session</b> · après 18:30';
    } else {
      text = '<b>Hors session</b>';
    }
    if (context && context.local_time) {
      text += ' <span style="color:#8f83a8">(' + fmtTime(context.local_time) +
        ' · ' + TZ + ')</span>';
    }
    target.innerHTML = text;
  }

  function renderStats(stats) {
    if (!stats) return;
    if ($('db-total')) $('db-total').textContent = stats.total_seats;
    if ($('db-occupied')) $('db-occupied').textContent = stats.occupied_seats;
    if ($('db-empty')) $('db-empty').textContent = stats.empty_seats;
    if ($('db-pct')) $('db-pct').textContent = Math.round(stats.occupancy_percentage) + '%';
  }

  /*
   * Single entry point for pushing a confirmed presence set into the 3D
   * scene. Both the WebSocket snapshot and the REST refresh use it, so an
   * avatar is reconstructed from the backend even if a broadcast was missed
   * (page load, WebSocket reconnect, tab suspended, ...).
   */
  function syncScene(present) {
    var twin = window.ClassroomTwin;
    if (twin && typeof twin.applyState === 'function') {
      twin.applyState(present || [], null, { immediate: true });
    }
  }

  function applySnapshot(msg) {
    if (msg.timezone) TZ = msg.timezone;
    S.stats = msg.statistics || S.stats;
    S.session = msg.session || null;
    S.context = {
      session: msg.session || null,
      local_time: msg.local_time,
      is_break: msg.is_break
    };

    var present = msg.seats || [];
    var occupied = msg.occupied_seats || present;
    S.presentSet = new Set(present);
    S.occupiedSet = new Set(occupied);

    syncScene(present);

    paintSeatButtons(Array.from(S.occupiedSet));
    refreshPendingClasses();
    renderSession(S.session, S.context);
    renderStats(S.stats);

    setStatus(
      S.mode === 'test' ? 'TEST — simulation → PostgreSQL' : 'LIVE — backend connecté',
      '#2ecc71'
    );

    if (S.historyOpen) scheduleHistoryRefresh();
  }

  /* -------------------------------------------------------- history */
  async function loadHistory() {
    var container = $('db-history');
    if (!container) return;
    container.innerHTML = '<div style="padding:8px;font-size:10px;color:#8f83a8">Chargement…</div>';
    try {
      var rows = await apiGet('/occupancy/history?limit=50');
      if (!rows.length) {
        container.innerHTML = '<div style="padding:8px;font-size:10px;color:#8f83a8">Aucun enregistrement.</div>';
        return;
      }
      var html = '<table><thead><tr>' +
        '<th>Siège</th><th>Sess.</th><th>Date</th><th>De</th><th>À</th><th>Durée</th>' +
        '</tr></thead><tbody>';
      rows.forEach(function (row) {
        html += '<tr class="' + (row.is_open ? 'open-row' : '') + '">' +
          '<td>' + row.seat_code + '</td>' +
          '<td>S' + row.session_number + '</td>' +
          '<td>' + fmtDate(row.occupied_from) + '</td>' +
          '<td>' + fmtTime(row.occupied_from) + '</td>' +
          '<td>' + (row.is_open ? 'ouvert' : fmtTime(row.occupied_until)) + '</td>' +
          '<td>' + fmtDuration(row.duration_seconds) + '</td>' +
          '</tr>';
      });
      html += '</tbody></table>';
      container.innerHTML = html;
    } catch (error) {
      container.innerHTML = '<div style="padding:8px;font-size:10px;color:#e74c3c">' +
        'Historique indisponible (backend arrêté ?)</div>';
    }
  }

  function scheduleHistoryRefresh() {
    if (!S.historyOpen) return;
    if (S.historyTimer) clearTimeout(S.historyTimer);
    S.historyTimer = setTimeout(function () {
      S.historyTimer = null;
      loadHistory();
    }, 400);
  }

  /* ---------------------------------------------------------- fetch */
  async function refresh() {
    try {
      var results = await Promise.all([
        apiGet('/seats'),
        apiGet('/sessions/current'),
        apiGet('/statistics')
      ]);
      var seats = results[0];
      var context = results[1];
      var stats = results[2];
      if (context && context.timezone) TZ = context.timezone;

      var present = seats.filter(function (seat) { return seat.present; })
        .map(function (seat) { return seat.seat_code; });
      var occupied = seats.filter(function (seat) { return seat.occupied; })
        .map(function (seat) { return seat.seat_code; });

      S.presentSet = new Set(present);
      S.occupiedSet = new Set(occupied);
      syncScene(present);

      /*
       * First sync after a page load: adopt the backend presence so an open
       * record recovered on restart is not immediately treated as absent.
       * Once the user has touched a simulation control, the user is the
       * source of truth and this initialisation is skipped.
       */
      if (S.mode === 'test' && !S.simInitDone) {
        S.simSeats = new Set(present);
        S.simInitDone = true;
      }

      paintSeatButtons(Array.from(S.occupiedSet));
      refreshPendingClasses();
      S.context = context;
      S.session = context.session;
      S.stats = stats;
      renderSession(context.session, context);
      renderStats(stats);

      if (S.mode === 'test') {
        S.simReady = true;
        if (ensureProducer()) startSimHeartbeat();
        setStatus(
          S.connected ? 'TEST — simulation → PostgreSQL' : 'TEST — backend OK, WebSocket en attente',
          S.connected ? '#2ecc71' : '#f1c40f'
        );
      } else {
        setStatus(
          S.connected ? 'LIVE — backend connecté' : 'LIVE — backend OK, WebSocket en attente',
          S.connected ? '#2ecc71' : '#f1c40f'
        );
      }
    } catch (error) {
      setStatus('Backend injoignable — lance uvicorn', '#e74c3c');
    }
  }

  /* ------------------------------------------------------ websocket */
  function connect() {
    if (S.ws && (S.ws.readyState === WebSocket.OPEN || S.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    setStatus('Connexion au backend…', '#f1c40f');
    var socket;
    try {
      socket = new WebSocket(WS_URL);
    } catch (error) {
      scheduleReconnect();
      return;
    }
    S.ws = socket;

    socket.onopen = function () {
      S.connected = true;
      S.delay = 1000;
      refresh();
      if (S.historyOpen) loadHistory();
    };

    socket.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (error) { return; }
      if (msg && msg.type === 'occupancy') applySnapshot(msg);
    };

    socket.onclose = function () {
      S.connected = false;
      S.ws = null;
      setStatus(
        S.mode === 'test' ? 'TEST — WebSocket perdu, reconnexion…' : 'LIVE — reconnexion…',
        '#e74c3c'
      );
      scheduleReconnect();
    };

    socket.onerror = function () {
      try { socket.close(); } catch (error) { /* ignore */ }
    };
  }

  function scheduleReconnect() {
    if (S.timer) return;
    S.timer = setTimeout(function () {
      S.timer = null;
      connect();
    }, S.delay);
    S.delay = Math.min(S.delay * 1.7, 15000);
  }

  /* --------------------------------------------- producer election */
  function readProducerLease() {
    try {
      var raw = localStorage.getItem(PRODUCER_KEY);
      if (!raw) return null;
      var lease = JSON.parse(raw);
      if (!lease || !lease.id) return null;
      if (Date.now() - Number(lease.ts || 0) > PRODUCER_TTL_MS) return null;
      return lease;
    } catch (error) {
      return null;
    }
  }

  function writeProducerLease() {
    try {
      localStorage.setItem(PRODUCER_KEY, JSON.stringify({ id: TAB_ID, ts: Date.now() }));
    } catch (error) { /* localStorage unavailable: this tab is the producer */ }
  }

  function releaseProducerLease() {
    try {
      var lease = readProducerLease();
      if (!lease || lease.id === TAB_ID) localStorage.removeItem(PRODUCER_KEY);
    } catch (error) { /* ignore */ }
  }

  function isProducer() {
    var lease = readProducerLease();
    // No lease means localStorage is unavailable; behave as a lone producer.
    return !lease || lease.id === TAB_ID;
  }

  /*
   * Become the producer. When taking over from another tab, adopt the last
   * confirmed presence first so we never send an empty snapshot that would
   * close the other tab's records.
   */
  function claimProducer() {
    var lease = readProducerLease();
    if (lease && lease.id !== TAB_ID) {
      S.simSeats = new Set(S.presentSet);
      S.simInitDone = true;
      refreshPendingClasses();
    }
    writeProducerLease();
    return true;
  }

  /* Called on boot: join as producer only if no other tab owns the lease. */
  function ensureProducer() {
    return isProducer() ? claimProducer() : false;
  }

  function releaseProducerIfOwner() {
    if (isProducer()) releaseProducerLease();
  }

  window.addEventListener('pagehide', releaseProducerIfOwner);

  /* ============================================================
     SIMULATION PRODUCER (this phase only)
     ------------------------------------------------------------
     The desired state of the manual controls is kept in
     S.simSeats. A 2 Hz heartbeat sends it as a full snapshot so
     the backend can confirm presence (2 s) or absence (3 s).
     The UI is NOT updated from these POST responses: the single
     rendering path is the WebSocket broadcast.

     Replacing this block with an external producer (YOLO/Pi) only
     requires POSTing the same payload to /api/ingest/snapshot.
     ============================================================ */
  function sendSimSnapshot() {
    if (S.simInFlight) return;
    if (S.mode !== 'test' && !S.simCleanup) return;
    // Even the LIVE cleanup may only be sent by the producer tab; a second
    // tab switching mode must not overwrite the producer's current snapshot.
    if (!isProducer()) return;

    var seats = S.simCleanup ? [] : Array.from(S.simSeats);
    S.simInFlight = true;
    apiPost('/ingest/snapshot', { seats: seats })
      .catch(function () {
        setStatus('Backend injoignable — simulation suspendue', '#e74c3c');
      })
      .then(function () {
        S.simInFlight = false;
      });
  }

  function startSimHeartbeat() {
    if (S.simTimer) return;
    S.simTimer = setInterval(sendSimSnapshot, SIM_INTERVAL_MS);
    sendSimSnapshot();
  }

  /*
   * Keep exactly one producer tab alive. If the lease expires (the producer
   * tab was closed or suspended) this tab takes over; if another tab claims
   * it, this one stops POSTing but keeps rendering the WebSocket state.
   */
  function producerWatchdog() {
    if (S.mode !== 'test') return;
    if (isProducer()) {
      writeProducerLease();
      if (!S.simTimer && S.simReady) {
        /*
         * Takeover after the previous producer closed: this tab may have
         * cleared its local desired set when it lost the lease. Adopt the
         * last confirmed presence so taking over never sends an empty
         * snapshot and closes records that are still physically occupied.
         */
        if (!S.simSeats.size && S.presentSet.size) {
          S.simSeats = new Set(S.presentSet);
          refreshPendingClasses();
        }
        startSimHeartbeat();
      }
    } else if (S.simTimer) {
      stopSimHeartbeat(false);
      S.simSeats.clear();
      refreshPendingClasses();
    }
  }

  function cancelSimCleanup() {
    if (S.simCleanupTimer) clearTimeout(S.simCleanupTimer);
    if (S.simCleanupInterval) clearInterval(S.simCleanupInterval);
    S.simCleanupTimer = null;
    S.simCleanupInterval = null;
    S.simCleanup = false;
  }

  /*
   * Leaving TEST stops the simulation. Empty snapshots are sent for a few
   * seconds so the backend closes the open records with its normal 3 s
   * absence rule (we never write occupied_until from the browser).
   */
  function stopSimHeartbeat(cleanup) {
    if (S.simTimer) {
      clearInterval(S.simTimer);
      S.simTimer = null;
    }
    cancelSimCleanup();
    if (!cleanup) return;

    S.simCleanup = true;
    S.simCleanupInterval = setInterval(sendSimSnapshot, SIM_INTERVAL_MS);
    sendSimSnapshot();
    S.simCleanupTimer = setTimeout(function () {
      if (S.simCleanupInterval) clearInterval(S.simCleanupInterval);
      S.simCleanupInterval = null;
      S.simCleanup = false;
      S.simCleanupTimer = null;
      if (S.mode !== 'test') releaseProducerIfOwner();
    }, SIM_CLEANUP_MS);
  }

  function simToggleSeat(id) {
    if (S.mode !== 'test') return;
    claimProducer();
    S.simInitDone = true;
    if (S.simSeats.has(id)) {
      S.simSeats.delete(id);
    } else {
      S.simSeats.add(id);
    }
    refreshPendingClasses();
    startSimHeartbeat();
    sendSimSnapshot();
  }

  function simFill() {
    if (S.mode !== 'test' || !window.ClassroomTwin) return;
    claimProducer();
    S.simInitDone = true;
    window.ClassroomTwin.seats.forEach(function (id) { S.simSeats.add(id); });
    refreshPendingClasses();
    startSimHeartbeat();
    sendSimSnapshot();
  }

  function simClear() {
    if (S.mode !== 'test') return;
    claimProducer();
    S.simInitDone = true;
    S.simSeats.clear();
    refreshPendingClasses();
    startSimHeartbeat();
    sendSimSnapshot();
  }

  function simRandom() {
    if (S.mode !== 'test' || !window.ClassroomTwin) return;
    claimProducer();
    S.simInitDone = true;
    S.simSeats.clear();
    window.ClassroomTwin.seats.forEach(function (id) {
      if (Math.random() < 0.4) S.simSeats.add(id);
    });
    refreshPendingClasses();
    startSimHeartbeat();
    sendSimSnapshot();
  }

  window.TwinSimulation = {
    isActive: function () { return S.mode === 'test'; },
    toggleSeat: simToggleSeat,
    fill: simFill,
    clear: simClear,
    randomize: simRandom
  };

  /* ----------------------------------------------------------- mode */
  function syncTwinPanel() {
    var twinStatus = $('twin-status');
    if (twinStatus) {
      twinStatus.textContent = S.mode === 'live'
        ? 'LIVE — backend'
        : 'TEST — simulation → backend';
    }
    var note = $('twin-live-note');
    if (note) {
      note.textContent = '⚠️ Mode LIVE : producteur externe (YOLO à venir).';
      note.style.display = S.mode === 'live' ? 'inline' : 'none';
    }
  }

  function hideLegacyPiControls() {
    /* The Pi no longer talks to the browser directly: it will POST to the
       backend in a later phase. Hide the old browser -> Pi WebSocket row. */
    var input = $('twin-ws-url');
    if (input && input.parentElement) input.parentElement.style.display = 'none';
  }

  function setMode(mode) {
    var next = mode === 'live' ? 'live' : 'test';
    var changed = next !== S.mode;
    S.mode = next;
    localStorage.setItem(STORAGE_MODE, S.mode);
    updateModeButtons();

    var twin = window.ClassroomTwin;
    if (twin && typeof twin.setMode === 'function') twin.setMode(S.mode);
    syncTwinPanel();

    if (S.mode === 'test') {
      cancelSimCleanup();
      S.simReady = false;
      if (changed) S.simInitDone = false;
      connect();
      refresh();
    } else {
      S.simSeats.clear();
      refreshPendingClasses();
      stopSimHeartbeat(true);
      connect();
      refresh();
    }
  }

  function whenTwinReady(callback) {
    if (window.ClassroomTwin) { callback(); return; }
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      if (window.ClassroomTwin || tries > 50) {
        clearInterval(timer);
        if (window.ClassroomTwin) callback();
      }
    }, 200);
  }

  /* ----------------------------------------------------------- boot */
  buildUI();
  updateModeButtons();
  setStatus('Initialisation…', '#f1c40f');
  setInterval(producerWatchdog, PRODUCER_RENEW_MS);

  whenTwinReady(function () {
    hideLegacyPiControls();
    setMode(S.mode);
    if (S.historyOpen) loadHistory();
  });

  console.log('[Backend] client ready — API=' + API + ' WS=' + WS_URL);
})();
