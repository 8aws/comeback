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
};

// ─── Router ──────────────────────────────────────────────────────────────────
function navigate(tab) {
  state.tab = tab;
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `tab-${tab}`));
  if (tab === 'backup') loadContainers();
  if (tab === 'restore') { loadBackups(); loadTestResources(); }
  if (tab === 'jobs') loadJobs();
  if (tab === 'deploy') loadTemplates();
  if (tab === 'updates') loadUpdates();
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

  logViewer.innerHTML = '';
  progressBar.style.width = '0%';
  progressPct.textContent = '0%';
  statusEl.textContent = 'running';
  statusEl.className = 'job-status-inline job-status running';

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

  modal.querySelector('.modal-close').onclick = () => {
    stopPolling();
    modal.style.display = 'none';
  };
}

// ─── BACKUP TAB ───────────────────────────────────────────────────────────────
async function loadContainers() {
  const grid = document.getElementById('container-grid');
  grid.innerHTML = '<div class="text-muted text-sm">Loading containers...</div>';
  try {
    state.containers = await API.containers.list();
    renderContainers();
  } catch (e) {
    grid.innerHTML = `<div class="alert error">Failed to load containers: ${e.message}</div>`;
  }
}

function renderContainers() {
  const grid = document.getElementById('container-grid');
  if (!state.containers.length) {
    grid.innerHTML = '<div class="empty-state"><div class="icon">🐳</div><div>No containers found</div></div>';
    return;
  }

  grid.innerHTML = state.containers.map(c => {
    const sel = state.selectedContainers.has(c.id);
    const volCount = (c.volumes || []).length;
    const badges = [
      c.db_type ? badge(c.db_type, 'db') : '',
      volCount ? badge(`${volCount} vol`, 'vol') : '',
    ].filter(Boolean).join('');

    return `
      <div class="container-item ${sel ? 'selected' : ''}" data-id="${c.id}" onclick="toggleContainer('${c.id}')">
        <div class="container-check"></div>
        ${statusDot(c.running)}
        <div class="flex" style="flex:1;min-width:0;flex-direction:column">
          <div class="container-name">${c.name}</div>
          <div class="container-image text-muted text-sm">${c.image}</div>
        </div>
        <div class="container-badges">${badges}</div>
      </div>`;
  }).join('');

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

function renderBackups() {
  const list = document.getElementById('backup-list');
  if (!state.backups.length) {
    list.innerHTML = '<div class="empty-state"><div class="icon">📦</div><div>No backups found</div><div class="text-sm text-muted mt-2">Create a backup from the Backup tab</div></div>';
    return;
  }

  list.innerHTML = state.backups.map(b => `
    <div class="backup-item">
      <div class="backup-item-header">
        <span style="font-size:20px">📦</span>
        <div style="flex:1;min-width:0">
          <div class="backup-name">${b.label || b.name}</div>
          <div class="text-sm text-muted">${b.name}</div>
        </div>
        <div class="backup-actions">
          <button class="btn btn-outline btn-sm" onclick="verifyBackup('${b.name}')">✅ Verify</button>
          <button class="btn btn-outline btn-sm" style="color:var(--yellow);border-color:var(--yellow)" onclick="openRestoreModal('${b.name}','test-')">🧪 Test</button>
          <button class="btn btn-blue btn-sm" onclick="openRestoreModal('${b.name}')">🔄 Restore</button>
          <a class="btn btn-outline btn-sm" href="/api/backups/${b.name}/download">⬇ Download</a>
          <button class="btn btn-danger btn-sm" onclick="deleteBackup('${b.name}')">🗑</button>
        </div>
      </div>
      <div class="backup-meta">
        <span>📅 ${fmt(b.created_at)}</span>
        <span>🖥 ${b.source_hostname}</span>
        <span>📦 ${b.size_human}</span>
        <span>🐳 ${b.container_count} containers</span>
        <span>💾 ${b.volume_count} volumes</span>
        <span>🗄 ${b.db_count} databases</span>
      </div>
      ${b.containers?.length ? `
      <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">
        ${b.containers.map(c => `<span class="badge">${c.name}</span>`).join('')}
      </div>` : ''}
    </div>`).join('');
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

function openRestoreModal(name, prefix = '') {
  _restoreTarget = name;
  _restorePrefix = prefix;
  const backup = state.backups.find(b => b.name === name);
  const modal = document.getElementById('restore-modal');

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

  closeRestoreModal();

  try {
    const { job_id } = await API.restore.start({
      backup_id: _restoreTarget,
      container_names: selected.length ? selected : null,
      overwrite_existing: overwrite,
      start_after_restore: start,
      name_prefix: prefix || null,
    });
    openJobModal(job_id, `Restore — ${_restoreTarget}`);
    window._scheduleJobPoll?.();
  } catch (e) {
    showToast(`Restore error: ${e.message}`, 'error');
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

async function loadUpdates() {
  const list = document.getElementById('updates-list');
  list.innerHTML = '<div class="text-muted text-sm">Consultando registries…</div>';
  try {
    const items = await API.updates.list();
    if (!items.length) {
      list.innerHTML = '<div class="text-muted text-sm">No hay contenedores.</div>';
      return;
    }
    list.innerHTML = items.map(u => {
      const st = UPDATE_STATUS[u.status] || UPDATE_STATUS.unknown;
      let action = '';
      if (u.status === 'update') {
        action = u.is_self
          ? `<button class="btn btn-outline btn-sm" onclick="startUpdate('${u.id}', '${escapeHtml(u.name)}')">⬇ Solo pull</button>`
          : `<button class="btn btn-primary btn-sm" onclick="startUpdate('${u.id}', '${escapeHtml(u.name)}')">⬆️ Actualizar</button>`;
      }
      return `
      <div class="container-item" style="display:flex;align-items:center;gap:12px;justify-content:space-between">
        <div style="display:flex;align-items:center;gap:10px;min-width:0">
          ${statusDot(u.running)}
          <div style="min-width:0">
            <div><strong>${escapeHtml(u.name)}</strong>${u.is_self ? ' ' + badge('comeback', '') : ''}</div>
            <div class="text-muted text-sm" style="overflow:hidden;text-overflow:ellipsis">${escapeHtml(u.image)}</div>
            ${u.detail ? `<div class="text-muted text-sm">${escapeHtml(u.detail)}</div>` : ''}
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-shrink:0">
          ${badge(`${st.icon} ${st.label}`, st.cls)}
          ${action}
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div class="alert error">Error comprobando actualizaciones: ${escapeHtml(e.message)}</div>`;
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

// ─── Auth ─────────────────────────────────────────────────────────────────────
function showLogin() {
  document.getElementById('login-overlay').style.display = 'flex';
}

function hideLogin() {
  document.getElementById('login-overlay').style.display = 'none';
}

async function initAuth() {
  try {
    const r = await fetch('/api/auth/status');
    const s = await r.json();
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
  } catch (e) {
    errBox.textContent = 'Error de conexión';
    errBox.style.display = 'block';
  }
}

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  document.getElementById('login-form').addEventListener('submit', submitLogin);
  await initAuth();

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
