const API = {
  _check(r) {
    if (r.status === 401 && typeof showLogin === 'function') showLogin();
    return r;
  },
  async get(url) {
    const r = API._check(await fetch(url));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async post(url, body) {
    const r = API._check(await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async put(url, body) {
    const r = API._check(await fetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async del(url) {
    const r = API._check(await fetch(url, { method: 'DELETE' }));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },

  containers: {
    list: () => API.get('/api/containers'),
    sizes: () => API.get('/api/containers/sizes'),
  },
  backups: {
    list: () => API.get('/api/backups'),
    manifest: (name) => API.get(`/api/backups/${name}/manifest`),
    delete: (name) => API.del(`/api/backups/${name}`),
    start: (body) => API.post('/api/backups/start', body),
  },
  restore: {
    start: (body) => API.post('/api/restore/start', body),
    verify: (body) => API.post('/api/restore/verify', body),
  },
  deploy: {
    templates: () => API.get('/api/deploy/templates'),
    start: (body) => API.post('/api/deploy/start', body),
    compose: (body) => API.post('/api/deploy/compose', body),
    dockerfile: (body) => API.post('/api/deploy/dockerfile', body),
  },
  updates: {
    list: () => API.get('/api/updates'),
    check: (id) => API.get(`/api/updates/check/${id}`),
    start: (body) => API.post('/api/updates/start', body),
    startAll: (body) => API.post('/api/updates/start-all', body),
  },
  system: {
    info: () => API.get('/api/system'),
  },
  stats: {
    list: () => API.get('/api/stats'),
    host: () => API.get('/api/stats/host'),
  },
  auth: {
    changePassword: (body) => API.post('/api/auth/change-password', body),
    logout: () => API.post('/api/auth/logout', {}),
  },
  schedules: {
    list: () => API.get('/api/schedules'),
    create: (body) => API.post('/api/schedules', body),
    update: (id, body) => API.put(`/api/schedules/${id}`, body),
    delete: (id) => API.del(`/api/schedules/${id}`),
    run: (id) => API.post(`/api/schedules/${id}/run`, {}),
  },
  cleanup: {
    list: () => API.get('/api/cleanup/test'),
    remove: (prefix) => API.del(`/api/cleanup/test/${encodeURIComponent(prefix)}`),
  },
  jobs: {
    list: () => API.get('/api/jobs'),
    get: (id) => API.get(`/api/jobs/${id}`),
    ws: (id) => {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      return new WebSocket(`${proto}//${location.host}/api/jobs/${id}/ws`);
    },
  },
};
