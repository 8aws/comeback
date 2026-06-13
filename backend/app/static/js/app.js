function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─── State ───────────────────────────────────────────────────────────────────
const state = {
  tab: 'backup',
  containers: [],
  selectedContainers: new Set(),
  backups: [],
  jobs: [],
  activeJobId: null,
  cardStats: {},     // id → {cpu_pct, mem_usage, mem_pct, net_rx, net_tx} for main-page cards
  statsHistory: {},  // id → [{cpu, mem}] last samples for the card sparkline
};

// ─── Router ──────────────────────────────────────────────────────────────────
function navigate(tab) {
  state.tab = tab;
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `tab-${tab}`));
  if (tab === 'backup') { loadContainers(); loadSchedules(); }
  if (tab === 'restore') { loadBackups(); loadTestResources(); }
  if (tab === 'jobs') loadJobs();
  if (tab === 'deploy') { loadTemplates(); loadDeployEnv(); applyDeployCard('templates'); applyDeployCard('custom'); }
  if (tab === 'updates') loadUpdates();
  if (tab === 'monitor') loadMonitor(); else clearTimeout(_monitorTimer);
  if (tab !== 'backup') clearTimeout(_cardStatsTimer);
}

// ─── Utilities ───────────────────────────────────────────────────────────────
function fmt(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function icon(name) {
  const icons = {
    backup: '📦', restore: '🔄', verify: '✅', running: '▶', stopped: '⏹',
    volume: '💾', db: '🗄', network: '🌐', image: '🐳', check: '✓',
    trash: '🗑', download: '⬇', play: '▶', info: 'ℹ', warning: '⚠', success: '✅', error: '❌',
  };
  return icons[name] || '•';
}

function badge(text, cls = '') {
  return `<span class="badge ${cls}">${text}</span>`;
}

function statusDot(running) {
  return `<div class="status-dot ${running ? 'running' : 'exited'}"></div>`;
}

function jobStatusBadge(status) {
  return `<span class="job-status ${status}">${status}</span>`;
}

function showToast(msg, type = 'info') {
  if (type === 'error') console.error('[toast]', msg);
  else if (type === 'warning') console.warn('[toast]', msg);
  const el = document.createElement('div');
  el.className = `alert ${type}`;
  el.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:999;max-width:420px;animation:fadeIn 0.2s;cursor:pointer';
  el.textContent = msg;
  el.title = 'Click para cerrar';
  el.onclick = () => el.remove();
  document.body.appendChild(el);
  // errors stay until clicked; other toasts auto-dismiss after 5s
  if (type !== 'error') setTimeout(() => el.remove(), 5000);
}

// ─── Job Modal (real-time) ────────────────────────────────────────────────────
function openJobModal(jobId, title) {
  state.activeJobId = jobId;

  const modal = document.getElementById('job-modal');
  modal.querySelector('.modal-title').textContent = title;
  const logViewer = modal.querySelector('.log-viewer');
  const progressBar = modal.querySelector('.progress-bar');
  const progressPct = modal.querySelector('.progress-pct');
  const statusEl = modal.querySelector('.job-status-inline');

  const spinner = modal.querySelector('.spinner');
  const resultIcon = modal.querySelector('.job-result-icon');
  const footer = modal.querySelector('.job-modal-footer');

  logViewer.innerHTML = '';
  progressBar.style.width = '0%';
  progressPct.textContent = '0%';
  statusEl.textContent = 'running';
  statusEl.className = 'job-status-inline job-status running';
  spinner.style.display = '';
  resultIcon.style.display = 'none';
  footer.style.display = 'none';

  modal.style.display = 'flex';

  let ws = null;
  let pollInterval = null;
  let lastLogCount = 0;

  function appendLog(entry) {
    const line = document.createElement('div');
    line.className = 'log-line';
    const ts = new Date(entry.ts).toLocaleTimeString('es-ES');
    line.innerHTML = `<span class="log-ts">${ts}</span><span class="log-level ${entry.level}">[${entry.level.toUpperCase()}]</span><span class="log-msg">${escapeHtml(entry.message)}</span>`;
    logViewer.appendChild(line);
    logViewer.scrollTop = logViewer.scrollHeight;
  }

  function applyJobState(msg) {
    if (msg.type === 'state') {
      (msg.logs || []).forEach(appendLog);
      lastLogCount = (msg.logs || []).length;
      progressBar.style.width = (msg.job?.progress || 0) + '%';
      progressPct.textContent = (msg.job?.progress || 0) + '%';
      if (msg.job?.status && msg.job.status !== 'running' && msg.job.status !== 'pending') {
        onFinished(msg.job.status);
      }
    } else if (msg.type === 'log') {
      appendLog(msg.data);
    } else if (msg.type === 'progress') {
      progressBar.style.width = msg.pct + '%';
      progressPct.textContent = msg.pct + '%';
    } else if (msg.type === 'finished') {
      onFinished(msg.status);
    }
  }

  function onFinished(status) {
    if (status === 'success') {
      progressBar.style.width = '100%';
      progressPct.textContent = '100%';
    }
    statusEl.textContent = status;
    statusEl.className = `job-status-inline job-status ${status}`;
    spinner.style.display = 'none';
    resultIcon.textContent = status === 'success' ? '✅' : status === 'failed' ? '❌' : '⚠️';
    resultIcon.style.display = '';
    footer.style.display = 'flex';
    stopPolling();
    if (state.tab === 'backup' || state.tab === 'restore') loadBackups();
  }

  function stopPolling() {
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    if (ws) { try { ws.close(); } catch(_) {} ws = null; }
  }

  // Polling fallback: used if WS fails or is not supported by proxy
  function startPolling() {
    // Only warn if the job isn't already done
    API.jobs.get(jobId).then(job => {
      if (job.status === 'running' || job.status === 'pending') {
        appendLog({ ts: new Date().toISOString(), level: 'warning', message: 'WebSocket unavailable — using polling' });
      }
      // Sync current state immediately before interval fires
      const newLogs = (job.logs || []).slice(lastLogCount);
      newLogs.forEach(appendLog);
      lastLogCount = (job.logs || []).length;
      progressBar.style.width = job.progress + '%';
      progressPct.textContent = job.progress + '%';
      if (job.status !== 'running' && job.status !== 'pending') {
        onFinished(job.status);
        return;
      }
      pollInterval = setInterval(async () => {
        try {
          const j = await API.jobs.get(jobId);
          const nl = (j.logs || []).slice(lastLogCount);
          nl.forEach(appendLog);
          lastLogCount = (j.logs || []).length;
          progressBar.style.width = j.progress + '%';
          progressPct.textContent = j.progress + '%';
          if (j.status !== 'running' && j.status !== 'pending') {
            onFinished(j.status);
          }
        } catch (_) {}
      }, 1500);
    }).catch(() => {});
  }

  // Try WebSocket first; fall back to polling only if it genuinely fails
  let wsConnected = false;
  try {
    ws = API.jobs.ws(jobId);

    ws.onopen = () => { wsConnected = true; };

    ws.onmessage = (ev) => applyJobState(JSON.parse(ev.data));

    ws.onerror = () => {
      ws = null;
      if (!wsConnected) startPolling(); // only fall back if we never connected
    };

    ws.onclose = (ev) => {
      // Normal close (1000/1001) after a successful connection → already done, no polling
      if (!wsConnected) startPolling();
    };
  } catch (e) {
    startPolling();
  }

  const closeModal = () => {
    stopPolling();
    modal.style.display = 'none';
  };
  modal.querySelector('.modal-close').onclick = closeModal;
  modal.querySelector('.job-modal-ok').onclick = closeModal;
}

// ─── BACKUP TAB ───────────────────────────────────────────────────────────────
async function loadContainers() {
  const grid = document.getElementById('container-grid');
  grid.innerHTML = '<div class="text-muted text-sm">Cargando contenedores…</div>';
  try {
    // Phase 1: fast list without sizes — render immediately
    state.containers = await API.containers.list();
    renderContainers();
    // Phase 2: sizes are slow (daemon computes disk usage) — merge when ready
    API.containers.sizes().then(sizes => {
      let changed = false;
      state.containers.forEach(c => {
        const s = sizes[c.id];
        if (s) { c.size_bytes = s.size_bytes; c.size_human = s.size_human; changed = true; }
      });
      if (changed && state.tab === 'backup') renderContainers();
    }).catch(() => {});
    // Phase 3: live CPU/RAM/net metrics + sparkline history (background loop)
    pollCardStats();
  } catch (e) {
    grid.innerHTML = `<div class="alert error">Failed to load containers: ${e.message}</div>`;
  }
}

// ─── Container view prefs (persisted) ──────────────────────────────────────
const viewPrefs = {
  get filter()    { return localStorage.getItem('cb_filter') || 'all'; },
  set filter(v)   { localStorage.setItem('cb_filter', v); },
  get sort()      { return localStorage.getItem('cb_sort') || 'name'; },
  set sort(v)     { localStorage.setItem('cb_sort', v); },
  get cols()      { return parseInt(localStorage.getItem('cb_cols') || '1', 10); },
  set cols(v)     { localStorage.setItem('cb_cols', v); },
  get favorites() { try { return new Set(JSON.parse(localStorage.getItem('cb_favs') || '[]')); } catch (e) { return new Set(); } },
  set favorites(s){ localStorage.setItem('cb_favs', JSON.stringify([...s])); },
  get verified()  { try { return new Set(JSON.parse(localStorage.getItem('cb_verified') || '[]')); } catch (e) { return new Set(); } },
  set verified(s) { localStorage.setItem('cb_verified', JSON.stringify([...s])); },
  isCollapsed(g)  { return localStorage.getItem(`cb_acc_${g}`) === '1'; },
  setCollapsed(g, v) { localStorage.setItem(`cb_acc_${g}`, v ? '1' : '0'); },
};

function setContainerFilter(f) { viewPrefs.filter = f; renderContainers(); }
function setContainerSort(s)   { viewPrefs.sort = s; renderContainers(); }
function setContainerCols(n)   { viewPrefs.cols = n; renderContainers(); }
function toggleAccordion(group){ viewPrefs.setCollapsed(group, !viewPrefs.isCollapsed(group)); renderContainers(); }

function toggleFavorite(ev, name) {
  ev.stopPropagation();   // don't toggle selection
  const favs = viewPrefs.favorites;
  favs.has(name) ? favs.delete(name) : favs.add(name);
  viewPrefs.favorites = favs;
  renderContainers();
}

// ─── Card live stats polling (sparkline history) ────────────────────────────
let _cardStatsTimer = null;
const SPARK_POINTS = 24;

async function pollCardStats() {
  clearTimeout(_cardStatsTimer);
  if (!monitoringEnabled() || state.tab !== 'backup') return;
  try {
    const stats = await API.stats.list();
    state.cardStats = Object.fromEntries(stats.filter(s => !s.error).map(s => [s.id, s]));
    stats.filter(s => !s.error).forEach(s => {
      const h = state.statsHistory[s.id] = state.statsHistory[s.id] || [];
      h.push({ cpu: s.cpu_pct || 0, mem: s.mem_pct || 0 });
      if (h.length > SPARK_POINTS) h.shift();
    });
    if (state.tab === 'backup') renderContainers();
  } catch (e) { /* stats are best-effort */ }
  _cardStatsTimer = setTimeout(pollCardStats, 10000);
}

function _sparkline(id, width = 120, height = 26) {
  const h = state.statsHistory[id];
  if (!h || h.length < 2) return '';
  const pts = (key, max) => h.map((p, i) =>
    `${(i / (SPARK_POINTS - 1) * width).toFixed(1)},${(height - Math.min(p[key], max) / max * height).toFixed(1)}`
  ).join(' ');
  return `
    <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"
         style="display:block;background:var(--surface);border-radius:4px">
      <polyline points="${pts('mem', 100)}" fill="none" stroke="var(--blue)" stroke-width="1.5"/>
      <polyline points="${pts('cpu', 100)}" fill="none" stroke="var(--purple)" stroke-width="1.5"/>
    </svg>`;
}

// ─── Container status & actions (Cosmos-style) ──────────────────────────────
const STATUS_STYLES = {
  running:    ['running',    'var(--success)'],
  restarting: ['restarting', 'var(--warning)'],
  paused:     ['paused',     'var(--yellow)'],
  exited:     ['stopped',    'var(--error)'],
  dead:       ['dead',       'var(--error)'],
  created:    ['created',    'var(--text-muted)'],
};

function _statusBadge(c) {
  const [label, color] = STATUS_STYLES[c.status] || [c.status, 'var(--text-muted)'];
  return `<span style="background:${color};color:#fff;font-size:10px;font-weight:700;
    padding:2px 8px;border-radius:10px;text-transform:uppercase;flex-shrink:0">${label}</span>`;
}

const CONTAINER_ACTIONS = {
  pull:     { icon: '⬇',  title: 'Forzar pull de la imagen', confirm: false, job: true },
  pause:    { icon: '⏸',  title: 'Pausar',                   confirm: false },
  unpause:  { icon: '⏯',  title: 'Reanudar',                 confirm: false },
  start:    { icon: '▶',  title: 'Arrancar',                 confirm: false },
  stop:     { icon: '⏹',  title: 'Parar',                    confirm: true },
  restart:  { icon: '🔄', title: 'Reiniciar',                confirm: true },
  recreate: { icon: '♻️', title: 'Recrear (misma config)',   confirm: true, job: true },
  kill:     { icon: '☠',  title: 'Kill (SIGKILL)',           confirm: true, danger: true },
};

function _actionsFor(c) {
  if (c.status === 'running') return ['pull', 'pause', 'stop', 'restart', 'recreate', 'kill'];
  if (c.status === 'paused') return ['unpause', 'stop', 'kill'];
  if (c.status === 'restarting') return ['stop', 'recreate', 'kill'];
  return ['start', 'pull', 'recreate'];   // exited / created / dead
}

function _actionBar(c) {
  return `<div style="display:flex;gap:4px;flex-wrap:wrap" onclick="event.stopPropagation()">
    ${_actionsFor(c).map(a => {
      const def = CONTAINER_ACTIONS[a];
      return `<button class="btn btn-outline btn-sm" style="padding:2px 7px;font-size:12px;${def.danger ? 'color:var(--error);border-color:var(--error)' : ''}"
        title="${t(def.title)}" onclick="containerAction('${c.id}', '${a}', '${escapeHtml(c.name)}')">${def.icon}</button>`;
    }).join('')}
  </div>`;
}

async function containerAction(id, action, name) {
  const def = CONTAINER_ACTIONS[action];
  if (def.confirm && !confirm(`${t(def.title)} — ${name}?`)) return;
  try {
    const r = await API.containers.action(id, action);
    if (r.job_id) {
      openJobModal(r.job_id, `${t(def.title)} — ${name}`);
      window._scheduleJobPoll?.();
    } else {
      showToast(`${name}: ${action} OK`, 'success');
      setTimeout(loadContainers, 800);   // give docker a moment, then refresh
    }
  } catch (e) {
    showToast(`${name}: ${e.message}`, 'error');
  }
}

function _portBadges(c) {
  const out = [];
  for (const [cport, bindings] of Object.entries(c.ports || {})) {
    for (const b of bindings || []) {
      if (b.HostPort) out.push(`${b.HostPort}:${cport.replace('/tcp', '')}`);
    }
  }
  return [...new Set(out)].slice(0, 6)
    .map(p => `<span class="badge" style="background:rgba(210,153,34,0.15);color:var(--yellow)">${escapeHtml(p)}</span>`).join('');
}

function _networkBadges(c) {
  return (c.networks || []).filter(n => n !== 'bridge').slice(0, 4)
    .map(n => `<span class="badge">${escapeHtml(n)}</span>`).join('');
}

// Exit codes from a voluntary docker stop/kill — not a crash:
// 0 = clean exit, 130 = SIGINT, 137 = SIGKILL (stop timeout), 143 = SIGTERM
const VOLUNTARY_EXIT_CODES = new Set([0, 130, 137, 143]);

function _hasProblem(c) {
  // unhealthy, crashed (unexpected exit code), restarting or dead
  const verified = viewPrefs.verified;
  if (verified.has(c.name)) return false;   // user vouched for it — treat as normal
  return c.health === 'unhealthy'
    || (c.status === 'exited' && c.exit_code != null && !VOLUNTARY_EXIT_CODES.has(c.exit_code))
    || c.status === 'restarting'
    || c.status === 'dead';
}

function _isVerifiedProblem(c) {
  // would be a problem, but the user marked it verified
  return viewPrefs.verified.has(c.name);
}

function toggleVerified(ev, name) {
  ev.stopPropagation();
  const v = viewPrefs.verified;
  v.has(name) ? v.delete(name) : v.add(name);
  viewPrefs.verified = v;
  renderContainers();
}

function _sortContainers(list) {
  const sort = viewPrefs.sort;
  return [...list].sort((a, b) => {
    if (sort === 'created') return (b.created || '').localeCompare(a.created || '');
    if (sort === 'size') return (b.size_bytes || 0) - (a.size_bytes || 0);
    return a.name.localeCompare(b.name);
  });
}

function _containerItem(c) {
  const sel = state.selectedContainers.has(c.id);
  const favs = viewPrefs.favorites;
  const volCount = (c.volumes || []).length;
  const problem = _hasProblem(c);
  const verifiedProblem = _isVerifiedProblem(c);
  const cols = viewPrefs.cols;
  const liveStats = state.cardStats?.[c.id];

  const badges = [
    problem ? badge(c.health === 'unhealthy' ? 'unhealthy' : `exit ${c.exit_code ?? '?'}`, 'db') : '',
    verifiedProblem ? `<span class="badge" onclick="toggleVerified(event, '${escapeHtml(c.name)}')" title="${t('Click para quitar el verificado')}" style="cursor:pointer">✔ ${t('verificado')}</span>` : '',
    c.db_type ? badge(c.db_type, 'db') : '',
    volCount ? badge(`${volCount} vol`, 'vol') : '',
    c.size_human ? badge(c.size_human, '') : '',
  ].filter(Boolean).join('');

  const verifyBtn = problem
    ? `<span onclick="toggleVerified(event, '${escapeHtml(c.name)}')" title="${t('Marcar como verificado — se gestiona como contenedor normal')}"
        style="cursor:pointer;font-size:13px;opacity:0.6;flex-shrink:0">✔</span>`
    : '';
  const favBtn = `<span onclick="toggleFavorite(event, '${escapeHtml(c.name)}')" title="${t('Favorito')}"
    style="cursor:pointer;font-size:13px;opacity:${favs.has(c.name) ? 1 : 0.25};flex-shrink:0">⭐</span>`;

  const statsText = liveStats && c.running ? `
    <div class="text-muted" style="font-size:10.5px;display:flex;gap:10px;flex-wrap:wrap">
      <span style="color:var(--purple)">● CPU ${liveStats.cpu_pct}%</span>
      <span style="color:var(--blue)">● RAM ${_fmtBytes(liveStats.mem_usage)} (${liveStats.mem_pct}%)</span>
      <span>↓${_fmtBytes(liveStats.net_rx)} ↑${_fmtBytes(liveStats.net_tx)}</span>
    </div>` : '';
  const spark = c.running && cols <= 3 ? _sparkline(c.id, cols === 1 ? 220 : 140) : '';
  const sparkRow = spark || statsText ? `
    <div style="display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap">
      ${statsText}${spark}
    </div>` : '';

  const ports = cols <= 2 ? _portBadges(c) : '';
  const nets = cols <= 2 ? _networkBadges(c) : '';
  const portNetRow = (ports || nets) ? `
    <div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center">${ports}${nets}</div>` : '';

  // Card: header row → image → badges → actions → ports/nets → graph
  return `
  <div class="container-item ${sel ? 'selected' : ''} ${problem ? 'problem' : ''}" data-id="${c.id}" onclick="toggleContainer('${c.id}')"
       style="flex-direction:column;align-items:stretch;gap:6px">
    <div style="display:flex;align-items:center;gap:8px;min-width:0">
      <div class="container-check"></div>
      ${_statusBadge(c)}
      <div class="container-name" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${escapeHtml(c.name)}</div>
      ${verifyBtn}
      ${favBtn}
    </div>
    <div class="container-image text-muted text-sm" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(c.image)}</div>
    ${badges ? `<div style="display:flex;flex-wrap:wrap;gap:4px">${badges}</div>` : ''}
    ${cols <= 3 ? _actionBar(c) : ''}
    ${portNetRow}
    ${sparkRow}
  </div>`;
}

function _accordionGroup(key, title, items, alwaysOpen = false) {
  if (!items.length) return '';
  const collapsed = !alwaysOpen && viewPrefs.isCollapsed(key);
  const cols = viewPrefs.cols;
  const grid = collapsed ? '' : `
    <div style="display:grid;gap:10px;grid-template-columns:repeat(${cols},minmax(0,1fr));margin-top:8px">
      ${items.map(_containerItem).join('')}
    </div>`;
  const chevron = alwaysOpen ? '' : `<span style="font-size:11px">${collapsed ? '▶' : '▼'}</span>`;
  const header = alwaysOpen
    ? `<div style="display:flex;align-items:center;gap:8px;margin-top:4px"><strong style="font-size:13px">${title}</strong><span class="text-muted text-sm">(${items.length})</span></div>`
    : `<div onclick="toggleAccordion('${key}')" style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;margin-top:4px">
         ${chevron}<strong style="font-size:13px">${title}</strong><span class="text-muted text-sm">(${items.length})</span>
       </div>`;
  return header + grid;
}

function renderContainers() {
  const grid = document.getElementById('container-grid');

  // 3-4 columns need the full page width — drop the sidebar below the grid
  const layout = document.getElementById('backup-layout');
  if (layout) layout.style.gridTemplateColumns = viewPrefs.cols >= 3 ? '1fr' : '1fr 320px';

  // Toolbar state
  document.querySelectorAll('.filter-chip').forEach(b =>
    b.classList.toggle('btn-primary', b.dataset.filter === viewPrefs.filter));
  document.querySelectorAll('.col-btn').forEach(b =>
    b.classList.toggle('btn-primary', parseInt(b.dataset.cols, 10) === viewPrefs.cols));
  const sortSel = document.getElementById('container-sort');
  if (sortSel) sortSel.value = viewPrefs.sort;

  if (!state.containers.length) {
    grid.innerHTML = '<div class="empty-state"><div class="icon">🐳</div><div>No containers found</div></div>';
    return;
  }

  const favs = viewPrefs.favorites;
  const filter = viewPrefs.filter;

  // Problems are always pinned on top, regardless of the active filter
  const problems = _sortContainers(state.containers.filter(_hasProblem));
  let rest = state.containers.filter(c => !_hasProblem(c));

  if (filter === 'running') rest = rest.filter(c => c.running);
  if (filter === 'stopped') rest = rest.filter(c => !c.running);
  if (filter === 'favorites') rest = rest.filter(c => favs.has(c.name));

  const favItems = _sortContainers(rest.filter(c => favs.has(c.name)));
  const others   = _sortContainers(rest.filter(c => !favs.has(c.name)));

  let html = _accordionGroup('problems', '⚠️ Con problemas', problems, true);
  if (filter !== 'favorites' && favItems.length) {
    html += _accordionGroup('favs', '⭐ Favoritos', favItems);
    html += _accordionGroup('others', '🐳 Resto', others);
  } else {
    html += _accordionGroup('others', filter === 'favorites' ? '⭐ Favoritos' : '🐳 Contenedores', filter === 'favorites' ? favItems : others);
  }

  grid.innerHTML = html || '<div class="text-muted text-sm">Nada que mostrar con este filtro.</div>';
  updateSelectionInfo();
}

function toggleContainer(id) {
  if (state.selectedContainers.has(id)) {
    state.selectedContainers.delete(id);
  } else {
    state.selectedContainers.add(id);
  }
  renderContainers();
}

function selectAll() {
  state.containers.forEach(c => state.selectedContainers.add(c.id));
  renderContainers();
}

function selectNone() {
  state.selectedContainers.clear();
  renderContainers();
}

function updateSelectionInfo() {
  const n = state.selectedContainers.size;
  document.getElementById('selection-count').textContent = n ? `${n} selected` : '';
  document.getElementById('btn-start-backup').disabled = n === 0;
  document.getElementById('btn-schedule-backup').disabled = n === 0;
}

async function startBackup() {
  const n = state.selectedContainers.size;
  if (!n) return;

  const includeImages = document.getElementById('opt-images').checked;
  const label = document.getElementById('opt-label').value.trim() || null;

  try {
    const { job_id } = await API.backups.start({
      container_ids: [...state.selectedContainers],
      include_images: includeImages,
      label,
    });
    openJobModal(job_id, `Backup — ${n} container(s)`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Backup error: ${e.message}`, 'error');
  }
}

// ─── SCHEDULED BACKUPS ────────────────────────────────────────────────────────
function openScheduleForm() {
  if (!state.selectedContainers.size) return;
  document.getElementById('schedule-form').style.display = 'block';
  document.getElementById('sched-name').focus();
}

async function createSchedule() {
  const name = document.getElementById('sched-name').value.trim();
  if (!name) { showToast('Pon un nombre a la programación', 'warning'); return; }
  if (!state.selectedContainers.size) { showToast('Selecciona contenedores primero', 'warning'); return; }
  try {
    await API.schedules.create({
      name,
      container_ids: [...state.selectedContainers],
      frequency: document.getElementById('sched-frequency').value,
      time: document.getElementById('sched-time').value || '03:00',
      weekday: parseInt(document.getElementById('sched-weekday').value, 10),
      retention: parseInt(document.getElementById('sched-retention').value, 10) || 7,
      include_images: document.getElementById('opt-images').checked,
    });
    document.getElementById('schedule-form').style.display = 'none';
    document.getElementById('sched-name').value = '';
    showToast(`Programación "${name}" creada`, 'success');
    loadSchedules();
  } catch (e) {
    showToast(`Error creando programación: ${e.message}`, 'error');
  }
}

const WEEKDAY_NAMES = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];

async function loadSchedules() {
  const list = document.getElementById('schedules-list');
  try {
    const schedules = await API.schedules.list();
    if (!schedules.length) {
      list.innerHTML = '<div class="text-muted text-sm">Sin programaciones. Selecciona contenedores y pulsa ⏰ Programar.</div>';
      return;
    }
    list.innerHTML = schedules.map(s => {
      const freq = s.frequency === 'weekly'
        ? `semanal (${WEEKDAY_NAMES[s.weekday]} ${s.time})`
        : `diaria (${s.time})`;
      return `
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:8px;opacity:${s.enabled ? 1 : 0.5}">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <strong>⏰ ${escapeHtml(s.name)}</strong>
          <div class="flex gap-2">
            <button class="btn btn-outline btn-sm" title="Ejecutar ahora" onclick="runScheduleNow('${s.id}', '${escapeHtml(s.name)}')">▶</button>
            <button class="btn btn-outline btn-sm" title="${s.enabled ? 'Pausar' : 'Activar'}" onclick="toggleSchedule('${s.id}', ${!s.enabled})">${s.enabled ? '⏸' : '▶️'}</button>
            <button class="btn btn-outline btn-sm" title="Eliminar" style="color:var(--red,#f85149)" onclick="deleteSchedule('${s.id}', '${escapeHtml(s.name)}')">🗑</button>
          </div>
        </div>
        <div class="text-muted text-sm" style="margin-top:4px">
          ${freq} · ${s.container_ids.length} contenedor(es) · retención ${s.retention}<br>
          Próxima: ${fmt(s.next_run)}${s.last_run ? ` · Última: ${fmt(s.last_run)}` : ''}
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div class="alert error">Error cargando programaciones: ${escapeHtml(e.message)}</div>`;
  }
}

async function toggleSchedule(id, enabled) {
  try {
    await API.schedules.update(id, { enabled });
    loadSchedules();
  } catch (e) { showToast(`Error: ${e.message}`, 'error'); }
}

async function deleteSchedule(id, name) {
  if (!confirm(`¿Eliminar la programación "${name}"? Los backups ya creados no se borran.`)) return;
  try {
    await API.schedules.delete(id);
    showToast(`Programación "${name}" eliminada`, 'success');
    loadSchedules();
  } catch (e) { showToast(`Error: ${e.message}`, 'error'); }
}

async function runScheduleNow(id, name) {
  try {
    const { job_id } = await API.schedules.run(id);
    openJobModal(job_id, `⏰ ${name} (manual)`);
    window._scheduleJobPoll?.();
  } catch (e) { showToast(`Error: ${e.message}`, 'error'); }
}

// ─── RESTORE TAB ──────────────────────────────────────────────────────────────
async function loadBackups() {
  const list = document.getElementById('backup-list');
  list.innerHTML = '<div class="text-muted text-sm">Loading backups...</div>';
  try {
    state.backups = await API.backups.list();
    renderBackups();
  } catch (e) {
    list.innerHTML = `<div class="alert error">Failed to load backups: ${e.message}</div>`;
  }
}

function _backupItem(b) {
  return `
    <div class="backup-item">
      <div class="backup-item-header">
        <span style="font-size:20px">📦</span>
        <div style="flex:1;min-width:0">
          <div class="backup-name">${escapeHtml(b.label || b.name)}</div>
          <div class="text-sm text-muted">${escapeHtml(b.name)}</div>
        </div>
        <div class="backup-actions">
          <button class="btn btn-outline btn-sm" onclick="verifyBackup('${b.name}')">✅ ${t('Verify')}</button>
          <button class="btn btn-outline btn-sm" style="color:var(--yellow);border-color:var(--yellow)" onclick="openRestoreModal('${b.name}','test-')">🧪 Test</button>
          <button class="btn btn-blue btn-sm" onclick="openRestoreModal('${b.name}')">🔄 Restore</button>
          <a class="btn btn-outline btn-sm" href="/api/backups/${b.name}/download">⬇ ${t('Download')}</a>
          <button class="btn btn-danger btn-sm" onclick="deleteBackup('${b.name}')">🗑</button>
        </div>
      </div>
      <div class="backup-meta">
        <span>📅 ${fmt(b.created_at)}</span>
        <span>🖥 ${escapeHtml(b.source_hostname)}</span>
        <span>📦 ${b.size_human}</span>
        <span>🐳 ${b.container_count} ${t('contenedores')}</span>
        <span>💾 ${b.volume_count} ${t('volúmenes')}</span>
        <span>🗄 ${b.db_count} ${t('bases de datos')}</span>
      </div>
      ${b.containers?.length ? `
      <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">
        ${b.containers.map(c => `<span class="badge">${escapeHtml(c.name)}</span>`).join('')}
      </div>` : ''}
    </div>`;
}

function toggleBackupGroup(key) {
  viewPrefs.setCollapsed(`bk_${key}`, !viewPrefs.isCollapsed(`bk_${key}`));
  renderBackups();
}

function renderBackups() {
  const list = document.getElementById('backup-list');
  if (!state.backups.length) {
    list.innerHTML = `<div class="empty-state"><div class="icon">📦</div><div>${t('No hay backups')}</div><div class="text-sm text-muted mt-2">${t('Crea un backup desde la pestaña Backup')}</div></div>`;
    return;
  }

  // Scheduled backups (label starts with ⏰) go into collapsed groups per
  // schedule so periodic runs don't flood the page
  const manual = state.backups.filter(b => !(b.label || '').startsWith('⏰'));
  const scheduled = state.backups.filter(b => (b.label || '').startsWith('⏰'));

  let html = manual.map(_backupItem).join('');

  const groups = {};
  scheduled.forEach(b => { (groups[b.label] = groups[b.label] || []).push(b); });

  html += Object.entries(groups).map(([label, items]) => {
    items.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    const key = label.replace(/\W/g, '_');
    const stored = localStorage.getItem(`cb_acc_bk_${key}`);
    const collapsed = stored === null ? true : stored === '1';   // collapsed by default
    const dates = `${fmt(items[items.length - 1].created_at)} → ${fmt(items[0].created_at)}`;
    return `
    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-top:10px">
      <div onclick="toggleBackupGroup('${key}')" style="display:flex;align-items:center;gap:10px;cursor:pointer;user-select:none">
        <span style="font-size:11px">${collapsed ? '▶' : '▼'}</span>
        <strong>${escapeHtml(label)}</strong>
        <span class="text-muted text-sm">${items.length} ${t('copias')} · ${dates}</span>
      </div>
      ${collapsed ? '' : `<div style="margin-top:10px;display:grid;gap:10px">${items.map(_backupItem).join('')}</div>`}
    </div>`;
  }).join('');

  list.innerHTML = html;
}

async function verifyBackup(name) {
  try {
    const { job_id } = await API.restore.verify({ backup_id: name });
    openJobModal(job_id, `Verify — ${name}`);
  } catch (e) {
    showToast(`Verify error: ${e.message}`, 'error');
  }
}

async function deleteBackup(name) {
  if (!confirm(`Delete backup "${name}"? This cannot be undone.`)) return;
  try {
    await API.backups.delete(name);
    showToast('Backup deleted', 'success');
    loadBackups();
  } catch (e) {
    showToast(`Delete failed: ${e.message}`, 'error');
  }
}

let _restoreTarget = null;
let _restorePrefix = '';

function uploadBackup(input) {
  const file = input.files[0];
  if (!file) return;
  input.value = '';   // allow re-selecting the same file later

  const wrap = document.getElementById('upload-progress');
  const bar = document.getElementById('upload-bar');
  const status = document.getElementById('upload-status');
  wrap.style.display = 'block';
  bar.style.width = '0%';
  status.textContent = `Subiendo ${file.name}…`;

  const fd = new FormData();
  fd.append('file', file);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/backups/upload');
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      bar.style.width = `${pct}%`;
      status.textContent = `Subiendo ${file.name}… ${pct}%`;
    }
  };
  xhr.onload = () => {
    if (xhr.status === 401) { showLogin(); wrap.style.display = 'none'; return; }
    if (xhr.status >= 200 && xhr.status < 300) {
      const r = JSON.parse(xhr.responseText);
      bar.style.width = '100%';
      status.textContent = `✅ ${r.name} (${r.size_human}, ${r.container_count} contenedores)`;
      showToast(`Backup importado: ${r.name}`, 'success');
      loadBackups();
    } else {
      let detail = xhr.responseText;
      try { detail = JSON.parse(xhr.responseText).detail; } catch (e) {}
      status.textContent = `❌ ${detail}`;
      showToast(`Error importando: ${detail}`, 'error');
    }
  };
  xhr.onerror = () => {
    status.textContent = '❌ Error de conexión';
    showToast('Error de conexión durante la subida', 'error');
  };
  xhr.send(fd);
}

async function _loadRestorePathMap(name) {
  const group = document.getElementById('restore-pathmap-group');
  const list = document.getElementById('restore-pathmap-list');
  group.style.display = 'none';
  list.innerHTML = '';
  try {
    const manifest = await API.backups.manifest(name);
    const sources = [...new Set((manifest.volumes || [])
      .filter(v => v.type === 'bind' && v.source)
      .map(v => v.source))];
    if (!sources.length) return;
    list.innerHTML = sources.map((s, i) => `
      <div style="display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:center">
        <code class="text-sm" style="overflow:hidden;text-overflow:ellipsis" title="${escapeHtml(s)}">${escapeHtml(s)}</code>
        <span class="text-muted">→</span>
        <input type="text" data-original="${escapeHtml(s)}" value="${escapeHtml(s)}"
          style="background:var(--surface2);border:1px solid var(--border);color:var(--text);
                 padding:6px 8px;border-radius:6px;font-size:12px;font-family:monospace">
      </div>`).join('');
    group.style.display = 'block';
  } catch (e) { /* manifest unreadable — restore can still proceed without remap */ }
}

function openRestoreModal(name, prefix = '') {
  _restoreTarget = name;
  _restorePrefix = prefix;
  const backup = state.backups.find(b => b.name === name);
  const modal = document.getElementById('restore-modal');
  _loadRestorePathMap(name);

  // Test mode banner
  const testBanner = modal.querySelector('.test-mode-banner');
  const prefixInput = document.getElementById('opt-prefix');
  if (prefix) {
    testBanner.style.display = 'flex';
    prefixInput.value = prefix;
    document.getElementById('opt-start').checked = true;
    document.getElementById('opt-overwrite').checked = false;
  } else {
    testBanner.style.display = 'none';
    prefixInput.value = '';
  }

  modal.querySelector('.restore-backup-name').textContent = backup?.label || name;

  // Build container checklist
  const containers = backup?.containers || [];
  const checkList = document.getElementById('restore-container-list');
  if (containers.length) {
    checkList.innerHTML = containers.map(c => `
      <label class="form-check">
        <input type="checkbox" checked data-cname="${c.name}">
        <span>${c.name}</span>
        <span class="text-muted text-sm">${c.image}</span>
      </label>`).join('');
  } else {
    checkList.innerHTML = '<div class="text-muted text-sm">All containers in backup</div>';
  }

  modal.style.display = 'flex';
}

function closeRestoreModal() {
  document.getElementById('restore-modal').style.display = 'none';
  _restoreTarget = null;
}

async function startRestore() {
  if (!_restoreTarget) return;

  const overwrite = document.getElementById('opt-overwrite').checked;
  const start = document.getElementById('opt-start').checked;
  const prefix = document.getElementById('opt-prefix').value.trim();

  const checks = document.querySelectorAll('#restore-container-list input[type=checkbox]');
  const selected = [...checks].filter(c => c.checked).map(c => c.dataset.cname);

  // Collect modified bind mount paths → path_map
  const pathMap = {};
  document.querySelectorAll('#restore-pathmap-list input[data-original]').forEach(inp => {
    const orig = inp.dataset.original;
    const val = inp.value.trim();
    if (val && val !== orig) pathMap[orig] = val;
  });

  const target = _restoreTarget;
  closeRestoreModal();

  try {
    const { job_id } = await API.restore.start({
      backup_id: target,
      container_names: selected.length ? selected : null,
      overwrite_existing: overwrite,
      start_after_restore: start,
      name_prefix: prefix || null,
      path_map: Object.keys(pathMap).length ? pathMap : null,
    });
    openJobModal(job_id, `Restore — ${target}`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Restore error: ${e.message}`, 'error');
  }
}

// ─── DEPLOY TAB: server environment ──────────────────────────────────────────
let _deployEnv = null;

function _condenseRanges(ports) {
  // [80,81,82,443,8080] → "80-82, 443, 8080"
  if (!ports.length) return '';
  const out = [];
  let start = ports[0], prev = ports[0];
  for (let i = 1; i <= ports.length; i++) {
    const p = ports[i];
    if (p === prev + 1) { prev = p; continue; }
    out.push(start === prev ? `${start}` : `${start}-${prev}`);
    start = prev = p;
  }
  return out.join(', ');
}

async function loadDeployEnv() {
  const box = document.getElementById('deploy-env');
  box.innerHTML = `<span class="text-muted text-sm">${t('Cargando entorno…')}</span>`;
  try {
    _deployEnv = await API.deploy.environment();
    const e = _deployEnv;
    const sections = [];

    sections.push(`
      <div style="margin-bottom:10px">
        <strong style="font-size:12px">🔌 ${t('Puertos ocupados')}</strong>
        <span class="text-muted text-sm">(${e.used_ports.length})</span>
        <div class="text-sm" style="margin-top:4px;word-break:break-all;font-family:monospace;font-size:11.5px">
          ${escapeHtml(_condenseRanges(e.used_ports)) || t('ninguno')}
        </div>
      </div>`);

    if (e.bind_roots?.length) {
      sections.push(`
      <div style="margin-bottom:10px">
        <strong style="font-size:12px">📁 ${t('Rutas de datos usadas por otros contenedores')}</strong>
        <div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap">
          ${e.bind_roots.map(r => `<span class="badge" style="font-family:monospace">${escapeHtml(r.path)} <span class="text-muted">×${r.count}</span></span>`).join('')}
        </div>
      </div>`);
    }

    if (e.common_networks?.length) {
      sections.push(`
      <div>
        <strong style="font-size:12px">🌐 ${t('Redes compartidas')}</strong>
        <div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap">
          ${e.common_networks.map(n => `<span class="badge">${escapeHtml(n.name)} <span class="text-muted">×${n.count}</span></span>`).join('')}
        </div>
      </div>`);
    }

    box.innerHTML = sections.join('');
  } catch (err) {
    box.innerHTML = `<div class="alert error">${escapeHtml(err.message)}</div>`;
  }
}

function checkPort() {
  const input = document.getElementById('port-check-input');
  const result = document.getElementById('port-check-result');
  const port = parseInt(input.value, 10);
  if (!port || port < 1 || port > 65535) { result.textContent = ''; return; }
  if (!_deployEnv) { result.textContent = '…'; return; }
  const inDocker = _deployEnv.docker_ports.includes(port);
  const onHost = _deployEnv.host_ports.includes(port);
  if (inDocker || onHost) {
    const who = inDocker ? 'Docker' : t('un proceso del host');
    result.innerHTML = `<span style="color:var(--error)">✕ ${port} ${t('ocupado por')} ${who}</span>`;
  } else {
    result.innerHTML = `<span style="color:var(--success)">✓ ${port} ${t('disponible')}</span>`;
  }
}

// ─── DEPLOY TAB ───────────────────────────────────────────────────────────────
let _templates = [];
let _deployTarget = null;

async function loadTemplates() {
  const grid = document.getElementById('templates-grid');
  grid.innerHTML = '<div class="text-muted text-sm">Loading templates…</div>';
  try {
    _templates = await API.deploy.templates();
    renderTemplates();
  } catch (e) {
    grid.innerHTML = `<div class="alert error">Failed to load templates: ${e.message}</div>`;
  }
}

function renderTemplates() {
  const grid = document.getElementById('templates-grid');
  if (!_templates.length) {
    grid.innerHTML = '<div class="empty-state"><div class="icon">🚀</div><div>No templates available</div></div>';
    return;
  }
  grid.innerHTML = _templates.map(t => `
    <div class="card" style="display:flex;flex-direction:column">
      <div class="card-body" style="flex:1">
        <div style="font-size:36px;margin-bottom:10px">${t.icon}</div>
        <div style="font-size:15px;font-weight:700;margin-bottom:6px">${escapeHtml(t.name)}</div>
        <div class="text-muted text-sm" style="margin-bottom:12px">${escapeHtml(t.description)}</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px">
          ${(t.services || []).map(s => `<span class="badge">🐳 ${escapeHtml(s)}</span>`).join('')}
        </div>
      </div>
      <div style="padding:0 18px 16px">
        <button class="btn btn-primary" style="width:100%" onclick="openDeployModal('${t.id}')">
          🚀 Desplegar
        </button>
      </div>
    </div>`).join('');
}

function openDeployModal(templateId) {
  _deployTarget = _templates.find(t => t.id === templateId);
  if (!_deployTarget) return;

  const modal = document.getElementById('deploy-modal');
  modal.querySelector('.deploy-template-name').textContent = _deployTarget.name;
  modal.querySelector('.deploy-template-icon').textContent = _deployTarget.icon;

  // Build form fields
  const form = document.getElementById('deploy-fields');
  form.innerHTML = _deployTarget.fields.map(f => `
    <div class="form-group">
      <label class="form-label">
        ${escapeHtml(f.label)}
        ${f.required ? '' : ' <span class="text-muted">(opcional)</span>'}
      </label>
      <input
        id="deploy-field-${f.key}"
        type="${f.type === 'password' ? 'password' : 'text'}"
        value="${escapeHtml(f.default)}"
        placeholder="${escapeHtml(f.placeholder || f.default)}"
        style="width:100%;background:var(--surface2);border:1px solid var(--border);
               color:var(--text);padding:8px 10px;border-radius:6px;
               font-size:13px;font-family:inherit"
        autocomplete="${f.type === 'password' ? 'new-password' : 'off'}"
      >
      ${f.hint ? `<div class="form-hint">${escapeHtml(f.hint)}</div>` : ''}
    </div>`).join('');

  modal.style.display = 'flex';
}

function closeDeployModal() {
  document.getElementById('deploy-modal').style.display = 'none';
  _deployTarget = null;
}

async function startDeploy() {
  if (!_deployTarget) return;
  const target = _deployTarget; // save before closeDeployModal() nulls it

  const config = {};
  for (const f of target.fields) {
    const el = document.getElementById(`deploy-field-${f.key}`);
    if (el) config[f.key] = el.value.trim();
  }

  // Basic required validation
  const missing = target.fields
    .filter(f => f.required && !config[f.key])
    .map(f => f.label);
  if (missing.length) {
    showToast(`Faltan campos: ${missing.join(', ')}`, 'warning');
    return;
  }

  closeDeployModal();
  try {
    const { job_id } = await API.deploy.start({
      template_id: target.id,
      config,
    });
    openJobModal(job_id, `Deploy ${target.icon} ${target.name}`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Deploy error: ${e.message}`, 'error');
  }
}

// ─── CUSTOM DEPLOY (compose / dockerfile) ────────────────────────────────────

function switchCustomTab(tab) {
  document.getElementById('custom-panel-compose').style.display  = tab === 'compose'    ? '' : 'none';
  document.getElementById('custom-panel-dockerfile').style.display = tab === 'dockerfile' ? '' : 'none';
  document.getElementById('custom-tab-compose').style.cssText    = tab === 'compose'
    ? 'background:var(--blue);color:white'
    : 'background:transparent;color:var(--text);border:1px solid var(--border)';
  document.getElementById('custom-tab-dockerfile').style.cssText = tab === 'dockerfile'
    ? 'background:var(--blue);color:white'
    : 'background:transparent;color:var(--text);border:1px solid var(--border)';
}

// ─── Deploy cards collapse ──────────────────────────────────────────────────
function applyDeployCard(key) {
  const body = document.getElementById(`deploy-body-${key}`);
  const chevron = document.querySelector(`.deploy-chevron[data-card="${key}"]`);
  const collapsed = viewPrefs.isCollapsed(`deploy_${key}`);
  if (body) body.style.display = collapsed ? 'none' : '';
  if (chevron) chevron.textContent = collapsed ? '▶' : '▼';
}

function toggleDeployCard(key, ev) {
  if (ev) ev.stopPropagation();
  viewPrefs.setCollapsed(`deploy_${key}`, !viewPrefs.isCollapsed(`deploy_${key}`));
  applyDeployCard(key);
}

// ─── MelodY (visual compose generator) ──────────────────────────────────────
function openMelody() {
  const frame = document.getElementById('melody-frame');
  // .src property resolves src="" to the page URL (never falsy) — use a flag
  if (frame.dataset.loaded !== '1') {
    frame.src = '/static/melody.html';
    frame.dataset.loaded = '1';
  }
  document.getElementById('melody-modal').style.display = 'flex';
}

function closeMelody() {
  document.getElementById('melody-modal').style.display = 'none';
}

// Inline ${VAR}/$VAR using MelodY's .env text — comeback's compose deploy has
// no separate .env, so unresolved refs would land literally in the container
function _inlineEnv(yaml, envText) {
  if (!envText) return yaml;
  const vars = {};
  for (const line of envText.split('\n')) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (m) vars[m[1]] = m[2];
  }
  return yaml.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g,
    (full, a, b) => { const k = a || b; return k in vars ? vars[k] : full; });
}

// MelodY posts the generated stack here when its "Desplegar en comeback" is used
window.addEventListener('message', (ev) => {
  if (ev.origin !== window.location.origin) return;   // MelodY is same-origin
  const msg = ev.data;
  if (!msg || msg.type !== 'melody-deploy') return;
  closeMelody();
  // Fill the compose panel (keeps the YAML visible/editable if deploy fails)
  switchCustomTab('compose');
  const name = (msg.name || 'melody-stack').trim();
  const yaml = _inlineEnv(msg.yaml || '', msg.env || '');
  document.getElementById('compose-name').value = name;
  document.getElementById('compose-yaml').value = yaml;
  document.querySelector('[data-tab="deploy"]')?.scrollIntoView?.();
  if (yaml) startComposeDeploy();
});

async function startComposeDeploy() {
  const name  = document.getElementById('compose-name').value.trim();
  const yaml  = document.getElementById('compose-yaml').value.trim();
  if (!name)  { showToast('Indica un nombre para el despliegue', 'warning'); return; }
  if (!yaml)  { showToast('Pega el contenido del docker-compose.yml', 'warning'); return; }
  try {
    const { job_id } = await API.deploy.compose({ name, yaml_content: yaml });
    openJobModal(job_id, `Compose deploy — ${name}`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Compose deploy error: ${e.message}`, 'error');
  }
}

function _parsePorts(text) {
  const ports = {};
  for (const line of text.split('\n')) {
    const p = line.trim();
    if (!p) continue;
    const parts = p.split(':');
    if (parts.length === 2) {
      const [host, container] = parts;
      const proto = container.includes('/') ? '' : '/tcp';
      ports[`${container.trim()}${proto}`] = parseInt(host.trim(), 10);
    }
  }
  return ports;
}

async function startDockerfileDeploy() {
  const name       = document.getElementById('df-name').value.trim();
  const image_tag  = document.getElementById('df-image-tag').value.trim();
  const dockerfile = document.getElementById('df-content').value.trim();
  const portsText  = document.getElementById('df-ports').value;
  const envText    = document.getElementById('df-env').value;
  const restart    = document.getElementById('df-restart').value;

  if (!name)       { showToast('Indica el nombre del contenedor', 'warning'); return; }
  if (!image_tag)  { showToast('Indica el tag de la imagen', 'warning'); return; }
  if (!dockerfile) { showToast('Pega el contenido del Dockerfile', 'warning'); return; }

  const ports = _parsePorts(portsText);
  const environment = envText.split('\n').map(l => l.trim()).filter(Boolean);

  try {
    const { job_id } = await API.deploy.dockerfile({
      name, image_tag, dockerfile_content: dockerfile, ports, environment, restart,
    });
    openJobModal(job_id, `Dockerfile deploy — ${name}`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Dockerfile deploy error: ${e.message}`, 'error');
  }
}

// ─── CLEANUP ──────────────────────────────────────────────────────────────────
let _testResources = { containers: [], volumes: [], prefixes: [] };

async function loadTestResources() {
  try {
    _testResources = await API.cleanup.list();
    renderTestResources();
  } catch (e) {
    document.getElementById('test-resources').innerHTML =
      `<div class="alert error">Failed to load: ${e.message}</div>`;
  }
}

function renderTestResources() {
  const el = document.getElementById('test-resources');
  const { containers, volumes, prefixes } = _testResources;

  if (!containers.length && !volumes.length) {
    el.innerHTML = '<div class="text-muted text-sm" style="padding:8px 0">No test containers or volumes found.</div>';
    return;
  }

  const byPrefix = {};
  prefixes.forEach(p => byPrefix[p] = { containers: [], volumes: [] });
  containers.forEach(c => byPrefix[c.prefix]?.containers.push(c));
  volumes.forEach(v => byPrefix[v.prefix]?.volumes.push(v));

  el.innerHTML = Object.entries(byPrefix).map(([prefix, res]) => `
    <div style="background:var(--surface2);border:1px solid var(--yellow);border-radius:var(--radius);padding:12px 14px;margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
        <span style="font-size:16px">🧪</span>
        <strong>Prefix: <code style="color:var(--yellow)">${escapeHtml(prefix)}</code></strong>
        <button class="btn btn-danger btn-sm ml-auto" onclick="cleanupPrefix('${escapeHtml(prefix)}')">
          🗑 Remove all
        </button>
      </div>
      <div style="display:grid;gap:6px">
        ${res.containers.map(c => `
          <div style="display:flex;align-items:center;gap:8px;font-size:13px">
            <div class="status-dot ${c.status === 'running' ? 'running' : 'exited'}"></div>
            <span>🐳 <strong>${escapeHtml(c.name)}</strong></span>
            <span class="text-muted text-sm">${escapeHtml(c.image)}</span>
            <span class="badge" style="margin-left:auto">${c.status}</span>
          </div>`).join('')}
        ${res.volumes.map(v => `
          <div style="display:flex;align-items:center;gap:8px;font-size:13px">
            <span>💾 <strong>${escapeHtml(v.name)}</strong></span>
            <span class="text-muted text-sm">← ${escapeHtml(v.original)}</span>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

async function cleanupPrefix(prefix) {
  if (!confirm(`Remove all containers and volumes with prefix "${prefix}"?`)) return;
  try {
    const result = await API.cleanup.remove(prefix);
    const total = result.removed_containers.length + result.removed_volumes.length;
    showToast(`Removed ${total} resource(s)`, 'success');
    if (result.errors.length) showToast(result.errors.join('\n'), 'warning');
    loadTestResources();
  } catch (e) {
    showToast(`Cleanup failed: ${e.message}`, 'error');
  }
}

// ─── UPDATES TAB ──────────────────────────────────────────────────────────────
const UPDATE_STATUS = {
  update:  { label: 'Actualización disponible', cls: 'warning', icon: '⬆️' },
  current: { label: 'Al día',                   cls: 'success', icon: '✅' },
  pinned:  { label: 'Fijada por digest',        cls: '',        icon: '📌' },
  local:   { label: 'Imagen local',             cls: '',        icon: '🔨' },
  unknown: { label: 'Desconocido',              cls: '',        icon: '❓' },
};

function _updateRow(u) {
  const st = UPDATE_STATUS[u.status] || UPDATE_STATUS.unknown;
  let action = '';
  if (u.status === 'update') {
    action = u.is_self
      ? `<button class="btn btn-outline btn-sm" onclick="startUpdate('${u.id}', '${escapeHtml(u.name)}')">⬇ Solo pull</button>`
      : `<button class="btn btn-primary btn-sm" onclick="startUpdate('${u.id}', '${escapeHtml(u.name)}')">⬆️ Actualizar</button>`;
  }
  const statusHtml = u.status === 'checking'
    ? `<span class="text-muted text-sm">${t('⏳ comprobando…')}</span>`
    : badge(`${st.icon} ${t(st.label)}`, st.cls);
  return `
    <div style="display:flex;align-items:center;gap:12px;justify-content:space-between;width:100%">
      <div style="display:flex;align-items:center;gap:10px;min-width:0">
        ${statusDot(u.running)}
        <div style="min-width:0">
          <div><strong>${escapeHtml(u.name)}</strong>${u.is_self ? ' ' + badge('comeback', '') : ''}</div>
          <div class="text-muted text-sm" style="overflow:hidden;text-overflow:ellipsis">${escapeHtml(u.image)}</div>
          ${u.detail ? `<div class="text-muted text-sm">${escapeHtml(u.detail)}</div>` : ''}
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
        ${statusHtml}
        ${action}
      </div>
    </div>`;
}

async function loadUpdates() {
  const list = document.getElementById('updates-list');
  list.innerHTML = '<div class="text-muted text-sm">Cargando contenedores…</div>';
  try {
    // Fast container list first — rows render at once, checks fill in one by one
    const containers = await API.containers.list();
    if (!containers.length) {
      list.innerHTML = '<div class="text-muted text-sm">No hay contenedores.</div>';
      return;
    }

    list.innerHTML = `
      <div id="updates-progress" style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
          <span class="text-muted">Consultando registries…</span>
          <span id="updates-progress-count" class="text-muted">0/${containers.length}</span>
        </div>
        <div class="stat-bar-track"><div id="updates-progress-bar" class="stat-bar" style="width:0%;background:var(--blue)"></div></div>
      </div>
      <div id="updates-bulk"></div>
      ${containers.map(c => `
        <div class="container-item" id="update-row-${c.id}" style="cursor:default">
          ${_updateRow({ id: c.id, name: c.name, image: c.image, running: c.running, is_self: false, status: 'checking', detail: null })}
        </div>`).join('')}`;

    // Sequential-ish checks (3 at a time) so the page stays responsive
    const results = [];
    let done = 0;
    const queue = [...containers];
    async function worker() {
      while (queue.length) {
        const c = queue.shift();
        let u;
        try {
          u = await API.updates.check(c.id);
        } catch (e) {
          u = { id: c.id, name: c.name, image: c.image, running: c.running, is_self: false, status: 'unknown', detail: e.message };
        }
        results.push(u);
        done++;
        const row = document.getElementById(`update-row-${c.id}`);
        if (row) row.innerHTML = _updateRow(u);
        const bar = document.getElementById('updates-progress-bar');
        const count = document.getElementById('updates-progress-count');
        if (bar) bar.style.width = `${Math.round(done / containers.length * 100)}%`;
        if (count) count.textContent = `${done}/${containers.length}`;
      }
    }
    await Promise.all([worker(), worker(), worker()]);

    const progress = document.getElementById('updates-progress');
    if (progress) progress.remove();
    const updatable = results.filter(u => u.status === 'update' && !u.is_self);
    const bulk = document.getElementById('updates-bulk');
    if (bulk && updatable.length > 1) {
      bulk.innerHTML = `
        <div style="display:flex;justify-content:flex-end;margin-bottom:12px">
          <button class="btn btn-primary" onclick="startUpdateAll(${JSON.stringify(updatable.map(u => u.id)).replace(/"/g, '&quot;')})">
            ⬆️ Actualizar todos (${updatable.length})
          </button>
        </div>`;
    }
  } catch (e) {
    list.innerHTML = `<div class="alert error">Error comprobando actualizaciones: ${escapeHtml(e.message)}</div>`;
  }
}

async function startUpdateAll(containerIds) {
  const backupFirst = document.getElementById('update-backup-first').checked;
  if (!confirm(`¿Actualizar ${containerIds.length} contenedores en serie?${backupFirst ? '\nSe hará backup previo de cada uno.' : '\n⚠️ SIN backups previos.'}`)) return;
  try {
    const { job_id } = await API.updates.startAll({ container_ids: containerIds, backup_first: backupFirst });
    openJobModal(job_id, `Update masivo — ${containerIds.length} contenedores`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Update error: ${e.message}`, 'error');
  }
}

async function startUpdate(containerId, name) {
  const backupFirst = document.getElementById('update-backup-first').checked;
  if (!confirm(`¿Actualizar "${name}"?${backupFirst ? '\nSe hará un backup previo.' : '\n⚠️ SIN backup previo.'}`)) return;
  try {
    const { job_id } = await API.updates.start({ container_id: containerId, backup_first: backupFirst });
    openJobModal(job_id, `Update: ${name}`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Update error: ${e.message}`, 'error');
  }
}

// ─── JOBS TAB ─────────────────────────────────────────────────────────────────
async function loadJobs() {
  const list = document.getElementById('jobs-list');
  try {
    state.jobs = await API.jobs.list();
    renderJobs();
  } catch (e) {
    list.innerHTML = `<div class="alert error">Failed to load jobs: ${e.message}</div>`;
  }
}

function renderJobs() {
  const list = document.getElementById('jobs-list');
  if (!state.jobs.length) {
    list.innerHTML = '<div class="empty-state"><div class="icon">📋</div><div>No jobs yet</div></div>';
    return;
  }

  list.innerHTML = state.jobs.map(j => `
    <div class="backup-item" style="cursor:pointer" onclick="viewJob('${j.id}')">
      <div class="backup-item-header">
        <span style="font-size:20px">${j.type === 'backup' ? '📦' : j.type === 'restore' ? '🔄' : j.type === 'deploy' ? '🚀' : j.type === 'update' ? '⬆️' : '✅'}</span>
        <div style="flex:1">
          <div class="backup-name">${j.label}</div>
          <div class="text-sm text-muted">${fmt(j.created_at)}</div>
        </div>
        ${jobStatusBadge(j.status)}
        ${j.status === 'running' ? `<div class="spinner"></div>` : ''}
      </div>
      ${j.status === 'running' ? `
      <div class="progress-bar-container" style="margin:8px 0 0">
        <div class="progress-bar" style="width:${j.progress}%"></div>
      </div>` : ''}
    </div>`).join('');
}

async function viewJob(id) {
  try {
    const job = await API.jobs.get(id);
    openJobModal(id, job.label);
  } catch (e) {
    showToast(`Cannot open job: ${e.message}`, 'error');
  }
}

// ─── Theme ────────────────────────────────────────────────────────────────────
function applyTheme() {
  const pref = localStorage.getItem('cb_theme') || 'dark';
  const resolved = pref === 'system'
    ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
    : pref;
  document.documentElement.dataset.theme = resolved;
  document.querySelectorAll('.theme-btn').forEach(b =>
    b.classList.toggle('btn-primary', b.dataset.themePref === pref));
}

function setTheme(pref) {
  localStorage.setItem('cb_theme', pref);
  applyTheme();
}

window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
  if ((localStorage.getItem('cb_theme') || 'dark') === 'system') applyTheme();
});

// ─── Settings ─────────────────────────────────────────────────────────────────
function openSettings() {
  document.getElementById('settings-modal').style.display = 'flex';
  document.getElementById('settings-monitoring').checked = monitoringEnabled();
  applyTheme();   // refresh button highlight
  API.system.info().then(info => {
    document.getElementById('settings-version').textContent = `v${info.version}`;
    document.getElementById('settings-instance').textContent = info.instance_name;
  }).catch(() => {});
}

function closeSettings() {
  document.getElementById('settings-modal').style.display = 'none';
  document.getElementById('pw-error').style.display = 'none';
  ['pw-current', 'pw-new', 'pw-confirm'].forEach(id => document.getElementById(id).value = '');
}

async function changePassword() {
  const errBox = document.getElementById('pw-error');
  errBox.style.display = 'none';
  const current = document.getElementById('pw-current').value;
  const nw = document.getElementById('pw-new').value;
  const confirm_ = document.getElementById('pw-confirm').value;
  if (nw !== confirm_) {
    errBox.textContent = 'Las contraseñas nuevas no coinciden';
    errBox.style.display = 'block';
    return;
  }
  try {
    await API.auth.changePassword({ current_password: current, new_password: nw });
    closeSettings();
    showToast('Contraseña cambiada — el resto de sesiones se han cerrado', 'success');
  } catch (e) {
    let detail = e.message;
    try { detail = JSON.parse(e.message).detail; } catch (_) {}
    errBox.textContent = detail;
    errBox.style.display = 'block';
  }
}

async function doLogout() {
  try { await API.auth.logout(); } catch (e) {}
  closeSettings();
  showLogin();
}

// ─── Monitor ──────────────────────────────────────────────────────────────────
function monitoringEnabled() { return localStorage.getItem('cb_monitoring') !== '0'; }
function setMonitoring(on) {
  localStorage.setItem('cb_monitoring', on ? '1' : '0');
  if (state.tab === 'monitor') loadMonitor();
  pollHostMonitor();
}

let _monitorTimer = null;

function setMonitorSort(v) {
  localStorage.setItem('cb_msort', v);
  loadMonitor();
}

function _sortStats(stats) {
  const sort = localStorage.getItem('cb_msort') || 'cpu';
  const favs = viewPrefs.favorites;
  return [...stats].sort((a, b) => {
    if (sort === 'cpu')  return (b.cpu_pct || 0) - (a.cpu_pct || 0);
    if (sort === 'ram')  return (b.mem_usage || 0) - (a.mem_usage || 0);
    if (sort === 'net')  return ((b.net_rx || 0) + (b.net_tx || 0)) - ((a.net_rx || 0) + (a.net_tx || 0));
    if (sort === 'disk') return ((b.block_read || 0) + (b.block_write || 0)) - ((a.block_read || 0) + (a.block_write || 0));
    if (sort === 'created') return (b.created || '').localeCompare(a.created || '');
    if (sort === 'favs') {
      const fa = favs.has(a.name) ? 0 : 1, fb = favs.has(b.name) ? 0 : 1;
      return fa !== fb ? fa - fb : a.name.localeCompare(b.name);
    }
    return a.name.localeCompare(b.name);   // name
  });
}

function _statBar(pct, color) {
  const w = Math.min(Math.max(pct, 0), 100);
  return `<div class="stat-bar-track"><div class="stat-bar" style="width:${w}%;background:${color}"></div></div>`;
}

function _fmtBytes(n) {
  if (n == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

async function loadMonitor() {
  clearTimeout(_monitorTimer);
  const list = document.getElementById('monitor-list');
  const statusEl = document.getElementById('monitor-status');

  if (!monitoringEnabled()) {
    list.innerHTML = '<div class="alert info">⏸ Monitoreo desactivado — actívalo en ⚙️ Ajustes.</div>';
    statusEl.textContent = 'desactivado';
    return;
  }

  const sortSel = document.getElementById('monitor-sort');
  if (sortSel) sortSel.value = localStorage.getItem('cb_msort') || 'cpu';

  try {
    const stats = _sortStats(await API.stats.list());
    statusEl.textContent = `actualizado ${new Date().toLocaleTimeString('es-ES')} · cada 5s`;
    if (!stats.length) {
      list.innerHTML = '<div class="text-muted text-sm">No hay contenedores en ejecución.</div>';
    } else {
      list.innerHTML = `
      <div style="display:grid;gap:10px">
        ${stats.map(s => {
          if (s.error) return `<div class="container-item"><strong>${escapeHtml(s.name)}</strong><span class="text-muted text-sm">${escapeHtml(s.error)}</span></div>`;
          const cpuColor = s.cpu_pct > 80 ? 'var(--error)' : s.cpu_pct > 50 ? 'var(--warning)' : 'var(--success)';
          const memColor = s.mem_pct > 80 ? 'var(--error)' : s.mem_pct > 50 ? 'var(--warning)' : 'var(--blue)';
          return `
          <div class="container-item" style="display:grid;grid-template-columns:minmax(140px,1fr) 2fr 2fr auto;gap:14px;align-items:center;cursor:default">
            <div style="min-width:0">
              <div class="container-name">${escapeHtml(s.name)}</div>
              <div class="text-muted" style="font-size:11px">${s.pids} pids</div>
            </div>
            <div>
              <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px">
                <span class="text-muted">CPU</span><span>${s.cpu_pct}%</span>
              </div>
              ${_statBar(s.cpu_pct, cpuColor)}
            </div>
            <div>
              <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px">
                <span class="text-muted">RAM</span><span>${_fmtBytes(s.mem_usage)} / ${_fmtBytes(s.mem_limit)} (${s.mem_pct}%)</span>
              </div>
              ${_statBar(s.mem_pct, memColor)}
            </div>
            <div class="text-muted" style="font-size:11px;text-align:right;line-height:1.6">
              🌐 ↓${_fmtBytes(s.net_rx)} ↑${_fmtBytes(s.net_tx)}<br>
              💾 R ${_fmtBytes(s.block_read)} · W ${_fmtBytes(s.block_write)}
            </div>
          </div>`;
        }).join('')}
      </div>`;
    }
  } catch (e) {
    list.innerHTML = `<div class="alert error">Error obteniendo estadísticas: ${escapeHtml(e.message)}</div>`;
  }

  if (state.tab === 'monitor' && monitoringEnabled()) {
    _monitorTimer = setTimeout(loadMonitor, 5000);
  }
}

// ─── Host monitor (header) ───────────────────────────────────────────────────
let _hostMonTimer = null;

async function pollHostMonitor() {
  clearTimeout(_hostMonTimer);
  const box = document.getElementById('host-monitor');
  if (!monitoringEnabled()) { box.style.display = 'none'; return; }

  try {
    const h = await API.stats.host();
    const l1 = [];
    if (h.cpu_pct != null) l1.push(`🖥 CPU ${h.cpu_pct}%`);
    if (h.mem) l1.push(`RAM ${_fmtBytes(h.mem.used)}/${_fmtBytes(h.mem.total)} (${h.mem.pct}%)`);
    if (h.disk) l1.push(`💾 ${_fmtBytes(h.disk.used)}/${_fmtBytes(h.disk.total)} (${h.disk.pct}%)`);

    const l2 = [];
    if (h.load) l2.push(`⚖ ${h.load.map(x => x.toFixed(2)).join(' ')}`);
    if (h.temp_c != null) l2.push(`🌡 ${h.temp_c}°C`);
    if (h.net) l2.push(`🌐 ↓${_fmtBytes(h.net.rx_s)}/s ↑${_fmtBytes(h.net.tx_s)}/s`);
    if (h.io) l2.push(`💿 R ${_fmtBytes(h.io.read_s)}/s · W ${_fmtBytes(h.io.write_s)}/s`);

    if (l1.length || l2.length) {
      document.getElementById('host-monitor-l1').textContent = l1.join(' · ');
      document.getElementById('host-monitor-l2').textContent = l2.join(' · ');
      box.style.display = 'flex';
    } else {
      box.style.display = 'none';
    }
  } catch (e) {
    box.style.display = 'none';   // 401 or host /proc unreachable — hide quietly
  }
  _hostMonTimer = setTimeout(pollHostMonitor, 10000);
}

// ─── Auth ─────────────────────────────────────────────────────────────────────
function showLogin() {
  document.getElementById('login-overlay').style.display = 'flex';
}

function hideLogin() {
  document.getElementById('login-overlay').style.display = 'none';
}

function _applyInstanceName(name) {
  if (!name) return;
  const sub = document.querySelector('.logo-sub');
  if (sub) sub.textContent = name;
  document.title = `${name} — uverse comeback`;
  const loginSub = document.getElementById('login-instance');
  if (loginSub) { loginSub.textContent = name; loginSub.style.display = 'block'; }
}

async function initAuth() {
  try {
    const r = await fetch('/api/auth/status');
    const s = await r.json();
    _applyInstanceName(s.instance_name);
    if (s.auth_enabled && !s.authenticated) {
      showLogin();
      return false;
    }
  } catch (e) { /* backend not ready — let normal error handling kick in */ }
  return true;
}

async function submitLogin(ev) {
  ev.preventDefault();
  const errBox = document.getElementById('login-error');
  errBox.style.display = 'none';
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: document.getElementById('login-user').value,
        password: document.getElementById('login-pass').value,
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      errBox.textContent = data.detail || `Error ${r.status}`;
      errBox.style.display = 'block';
      return;
    }
    hideLogin();
    document.getElementById('login-pass').value = '';
    navigate(state.tab || 'backup');
    pollHostMonitor();
  } catch (e) {
    errBox.textContent = 'Error de conexión';
    errBox.style.display = 'block';
  }
}

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  applyTheme();
  applyLang();
  document.getElementById('login-form').addEventListener('submit', submitLogin);
  const authed = await initAuth();
  if (authed) pollHostMonitor();

  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => navigate(btn.dataset.tab));
  });

  navigate('backup');

  // Poll only while there are running jobs
  let _pollActive = false;
  async function tickJobsBadge() {
    const jobs = await API.jobs.list().catch(() => []);
    const running = jobs.filter(j => j.status === 'running').length;
    const badge = document.getElementById('jobs-badge');
    if (running > 0) {
      badge.textContent = running;
      badge.style.display = 'inline-flex';
      if (state.tab === 'jobs') { state.jobs = jobs; renderJobs(); }
      setTimeout(tickJobsBadge, 2000);
      _pollActive = true;
    } else {
      badge.style.display = 'none';
      if (state.tab === 'jobs' && _pollActive) { state.jobs = jobs; renderJobs(); }
      _pollActive = false;
    }
  }

  // Kick off badge polling after any job starts
  const _origOpenJobModal = openJobModal;
  window._scheduleJobPoll = () => { if (!_pollActive) tickJobsBadge(); };
  tickJobsBadge();
});
