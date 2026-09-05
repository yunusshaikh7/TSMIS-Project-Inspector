/* Plain browser UI; all file and ArcGIS work stays behind the Python bridge. */
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let api, settings, state = {projects: [], running: false}, selected = null, activeProject = null, polling = false;
const isDemo = location.hash === '#demo';
const notice = text => { $('notice').textContent = text; $('notice').hidden = !text; };
const error = text => { $('alert').textContent = text; $('alert').hidden = !text; };
const environmentBadge = value => `<span class="badge ${['dev','test','prod'].includes(String(value).toLowerCase()) ? String(value).toLowerCase() : 'unknown'}">${escapeHtml(value)}</span>`;
const statusClass = value => value === 'Identified' ? 'good' : ['Needs review','Mixed connections'].includes(value) ? 'review' : value === 'Could not open' ? 'bad' : '';
function values() { return {...settings, root: $('folder').value.trim(), recursive: $('recursive').checked}; }
function bind(id, handler) { $(id).addEventListener('click', async () => { try { await handler(); } catch(e) { error('The action could not finish: ' + (e.message || e)); } }); }
function dialog(id) { $(id).showModal(); }
function render() {
  const projects = state.projects;
  $('projectCount').textContent = projects.length;
  $('scanBtn').hidden = state.running; $('stopBtn').hidden = !state.running;
  for (const id of ['browseBtn','folder','recursive','settingsBtn','diagnosticsBtn']) $(id).disabled = state.running;
  $('exportBtn').disabled = state.running || !state.resultExists;
  $('saveDiagnosticsBtn').hidden = state.running || !state.diagnostic_scan;
  $('progressArea').hidden = !state.running;
  $('progressText').textContent = state.message;
  $('progressCount').textContent = state.total ? `${projects.length} / ${state.total}` : '';
  if (state.total) { $('progress').max = state.total; $('progress').value = projects.length; } else $('progress').removeAttribute('value');
  $('empty').hidden = !!projects.length;
  $('filters').hidden = !projects.length;
  $('footerStatus').textContent = state.running ? 'Scanning…' : state.error ? 'Scan incomplete' : state.resultExists ? state.message : 'Ready';
  $('scanWarnings').hidden = !state.warnings?.length;
  $('scanWarnings').textContent = state.warnings?.join('\n') || '';
  renderProjects();
}
function renderProjects() {
  const query = $('search').value.toLowerCase();
  const filtered = state.projects.map((p, index) => ({...p,index})).filter(p => (!$('tsmisOnly').checked || p.tsmis_connections) && [p.path,...p.versions,...p.environments,...p.folders,p.status].join(' ').toLowerCase().includes(query));
  $('projectsBody').innerHTML = filtered.map(p => `<tr data-index="${p.index}" class="${selected === p.index ? 'selected' : ''}"><td><button class="project-link" data-project="${p.index}">${escapeHtml(p.name)}</button><span class="path" title="${escapeHtml(p.path)}">${escapeHtml(p.path)}</span></td><td>${p.environments.length ? p.environments.map(environmentBadge).join('') : '<span class="muted">—</span>'}</td><td class="mono">${p.versions.length ? p.versions.map(escapeHtml).join('<br>') : '<span class="muted">Not reported</span>'}</td><td>${p.folders.length ? p.folders.map(escapeHtml).join('<br>') : '<span class="muted">—</span>'}</td><td><span class="status ${statusClass(p.status)}">${escapeHtml(p.status)}</span></td><td aria-hidden="true">›</td></tr>`).join('');
  $('noMatches').hidden = !state.projects.length || !!filtered.length;
}
async function selectProject(index) {
  const project = await api.get_project(index);
  if (!project) return;
  selected = index; activeProject = project;
  $('allLayers').checked = !project.tsmis_connections;
  renderDetails(); renderProjects();
  if (!$('details').open) $('details').showModal();
}
function renderDetails() {
  const p = activeProject;
  if (!p) return;
  $('detailsHeading').textContent = p.name;
  $('detailPath').textContent = p.path;
  $('detailSummary').textContent = `${p.tsmis_connections} TSMIS connection${p.tsmis_connections === 1 ? '' : 's'} · ${p.status}`;
  $('projectErrors').hidden = !p.errors.length;
  $('projectErrors').textContent = p.errors.join('\n');
  const rows = p.rows.filter(row => $('allLayers').checked || row.is_tsmis);
  $('detailsBody').innerHTML = rows.length ? rows.map(r => `<tr><td><strong>${escapeHtml(r.layer)}</strong><small>${escapeHtml(r.map)} · ${escapeHtml(r.kind)}${r.is_tsmis ? '' : ' · Other connection'}</small></td><td><span title="${escapeHtml(r.environment_evidence || '')}">${environmentBadge(r.environment)}</span></td><td>${escapeHtml(r.service || '—')}<small>${escapeHtml(r.folder || 'No service folder')}</small></td><td><span class="mono">${escapeHtml(r.version || 'Not exposed')}</span><small>${escapeHtml(r.version_kind || r.status)}</small></td><td><div class="source">${escapeHtml(r.url || r.workspace || 'No source reported')}</div><small>${escapeHtml(r.error || r.status)}</small>${r.dataset ? `<small>Dataset ${escapeHtml(r.dataset)}</small>` : ''}</td></tr>`).join('') : '<tr><td colspan="5" class="muted">No TSMIS connections. Select “All layers and tables” to see other sources.</td></tr>';
}
async function startScan(diagnostics) {
  error(''); notice('');
  const response = await api.start_scan(values(), diagnostics);
  if (!response.ok) { error(response.error); return; }
  settings = values(); selected = null; activeProject = null; $('details').close();
  state.resultExists = true;
  await poll();
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const previousRunning = state.running;
    state = {...await api.get_scan_state(), resultExists: state.resultExists};
    if (state.error) error(state.error);
    render();
    if (previousRunning && !state.running && state.diagnostic_scan) notice('Diagnostics ready. Settings → Test / diagnostics → Save diagnostic ZIP.');
  } catch(e) { error('Lost contact with the app: ' + e.message); }
  finally { polling = false; }
}
async function saveResults(diagnostics) {
  const result = await api.save_results(diagnostics);
  if (result.ok) { notice('Saved: ' + result.path); if (diagnostics) $('diagnosticsDialog').close(); }
  else if (!result.cancelled) error(result.error);
}
async function boot(bridge) {
  api = bridge;
  const initial = await api.get_initial_state();
  settings = initial.settings;
  $('folder').value = settings.root; $('recursive').checked = settings.recursive;
  $('version').textContent = 'v' + initial.version;
  $('runtimeStatus').textContent = initial.arcgis_found ? 'ArcGIS Python located' : 'ArcGIS Pro required';
  $('demoBanner').hidden = !isDemo;
  bind('browseBtn', async () => { const path = await api.choose_folder(); if (path) $('folder').value = path; });
  bind('scanBtn', () => startScan(false));
  bind('stopBtn', () => api.stop_scan());
  bind('exportBtn', () => saveResults(false));
  bind('settingsBtn', () => { $('pythonPath').value = settings.python; $('match').value = settings.match; dialog('settingsDialog'); });
  bind('pythonBrowseBtn', async () => { const path = await api.choose_python(); if (path) $('pythonPath').value = path; });
  bind('saveSettingsBtn', async () => {
    const next = {...values(), python: $('pythonPath').value.trim(), match: $('match').value.trim()};
    const result = await api.save_settings(next);
    if (result.ok) { settings = next; $('settingsDialog').close(); notice('Settings saved.'); $('runtimeStatus').textContent = settings.python ? 'ArcGIS Python selected' : 'ArcGIS Pro required'; }
    else { $('settingsDialog').close(); error(result.error); }
  });
  bind('diagnosticsBtn', () => { $('settingsDialog').close(); dialog('diagnosticsDialog'); });
  bind('runDiagnosticsBtn', async () => { $('diagnosticsDialog').close(); await startScan(true); });
  bind('saveDiagnosticsBtn', () => saveResults(true));
  bind('closeDetailsBtn', () => { $('details').close(); selected = null; activeProject = null; renderProjects(); });
  $('projectsBody').addEventListener('click', event => { const row = event.target.closest('[data-index]'); if(row) selectProject(Number(row.dataset.index)).catch(e => error(e.message)); });
  $('search').addEventListener('input', renderProjects); $('tsmisOnly').addEventListener('change', renderProjects); $('allLayers').addEventListener('change', renderDetails);
  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
  bind('updatesBtn', async () => {
    $('settingsDialog').close(); dialog('updatesDialog'); $('updateMessage').textContent = 'Checking for updates…';
    $('downloadUpdateBtn').hidden = true; $('openUpdateBtn').hidden = true;
    const result = await api.check_updates();
    $('updateMessage').textContent = result.message || result.error;
    $('downloadUpdateBtn').hidden = !result.available;
  });
  bind('downloadUpdateBtn', async () => {
    $('downloadUpdateBtn').disabled = true; $('updateMessage').textContent = 'Downloading and checking the update…';
    try { const result = await api.download_update(); $('updateMessage').textContent = result.message || result.error; $('openUpdateBtn').hidden = !result.ok; if (result.ok) $('downloadUpdateBtn').hidden = true; }
    finally { $('downloadUpdateBtn').disabled = false; }
  });
  bind('openUpdateBtn', async () => { const result = await api.open_update(); $('updateMessage').textContent = result.message || result.error; });
  document.body.dataset.ready = 'true';
  await poll();
  setInterval(() => { if(state.running) poll(); }, 600);
}
if (isDemo) { boot(window.demoApi).catch(e => error(e.message)); }
else {
  let started = false;
  const connect = () => { if (!started && typeof window.pywebview?.api?.get_initial_state === 'function') { started = true; boot(window.pywebview.api).catch(e => error(e.message)); } };
  window.addEventListener('pywebviewready', connect);
  const timer = setInterval(() => { connect(); if(started) clearInterval(timer); }, 150);
}
