/* Plain browser UI; all file and ArcGIS work stays behind the Python bridge. */
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const emptyState = () => ({projects: [], running: false, has_result: false, last_refreshed: null});
let api, settings, state = emptyState(), selected = null, activeProject = null, polling = false;
let savedPaths = [], busy = false, loadingPath = false, pathDirty = false, pendingRoot = '';
let detailSequence = 0, selectionSequence = 0, selectionJob = Promise.resolve();
const isDemo = location.hash === '#demo';
const notice = text => { $('notice').textContent = text; $('notice').hidden = !text; };
const error = text => { $('alert').textContent = text; $('alert').hidden = !text; };
const hasReadIssue = p => p.open_error || p.read_issues || p.errors?.length;
const environmentBadge = value => `<span class="badge ${['dev','test','prod'].includes(String(value).toLowerCase()) ? String(value).toLowerCase() : 'unknown'}">${escapeHtml(value)}</span>`;
function values() { return {...settings, root: $('folder').value.trim(), recursive: $('recursive').checked}; }
function bind(id, handler) { $(id).addEventListener('click', async () => { try { await handler(); } catch(e) { error('The action could not finish: ' + (e.message || e)); } }); }
function dialog(id) { $(id).showModal(); }
function resetDetails() {
  ++detailSequence;
  selected = null; activeProject = null;
  $('details').close(); $('search').value = '';
}
function renderSavedPaths(entries = savedPaths) {
  savedPaths = entries;
  const root = pendingRoot || settings.root;
  const paths = [...new Set([root, ...entries.map(entry => entry.root)].filter(Boolean))];
  $('folder').replaceChildren(...paths.map(path => new Option(path, path)));
  $('folder').add(new Option('Browse…', '__browse__'));
  $('folder').value = root;
  $('folder').title = root;
}
async function browseFolder() {
  renderSavedPaths();
  const path = await api.choose_folder();
  if (path) await switchPath(path);
}

function render() {
  const projects = state.projects;
  $('projectCount').textContent = projects.length;
  $('scanBtn').hidden = state.running; $('stopBtn').hidden = !state.running;
  $('scanBtn').textContent = state.has_result ? 'Refresh' : 'Scan';
  $('scanBtn').disabled = busy || !$('folder').value.trim();
  for (const id of ['browseBtn','folder']) $(id).disabled = state.running || busy;
  for (const id of ['recursive','settingsBtn','diagnosticsBtn']) $(id).disabled = state.running || busy || loadingPath;
  for (const id of ['exportBtn','clearBtn']) $(id).disabled = state.running || busy || loadingPath || pathDirty || !state.has_result;
  $('saveDiagnosticsBtn').hidden = state.running || !state.diagnostic_scan;
  $('progressArea').hidden = !state.running;
  $('progressText').textContent = state.message || '';
  $('progressCount').textContent = state.total ? `${projects.length} / ${state.total}` : '';
  if (state.total) { $('progress').max = state.total; $('progress').value = projects.length; } else $('progress').removeAttribute('value');
  $('empty').hidden = !!projects.length;
  $('empty').textContent = loadingPath ? 'Loading saved list…' : state.has_result ? 'No projects in this list.' : 'Choose a folder and scan.';
  $('filters').hidden = !projects.length;
  $('footerStatus').textContent = loadingPath ? 'Loading…' : state.running ? 'Refreshing…' : state.save_error ? 'List not saved' : state.has_result && !state.complete ? (state.last_refreshed ? 'Incomplete refresh · previous saved list kept' : 'Incomplete refresh · not saved') : state.message || 'Ready';
  const refreshed = state.last_refreshed && new Date(state.last_refreshed);
  $('lastRefreshed').textContent = refreshed ? 'Last refreshed: ' + refreshed.toLocaleString([], {dateStyle:'short', timeStyle:'short'}) : 'Not refreshed';
  $('lastRefreshed').dateTime = state.last_refreshed || '';
  $('lastRefreshed').title = refreshed ? refreshed.toLocaleString() : '';
  $('scanWarnings').hidden = !state.warnings?.length;
  $('scanWarnings').textContent = state.warnings?.join('\n') || '';
  renderProjects();
}
function switchPath(root) {
  const sequence = ++selectionSequence;
  pendingRoot = root;
  pathDirty = true; loadingPath = !!root.trim(); state = emptyState();
  resetDetails(); error(''); notice(''); renderSavedPaths(); render();
  // Serialize selections, skipping superseded requests, so fast path changes
  // cannot display one folder's results beneath another folder's path.
  selectionJob = selectionJob.catch(() => {}).then(async () => {
    if (sequence !== selectionSequence || !root.trim()) return;
    try {
      const response = await api.select_path(root);
      if (sequence !== selectionSequence) return;
      if (!response.ok) { error(response.error); return; }
      settings = response.settings; state = response.state; pathDirty = false; pendingRoot = '';
      $('folder').value = settings.root; $('recursive').checked = settings.recursive;
      renderSavedPaths(response.saved_paths); error(response.warning || '');
    } catch(e) { if (sequence === selectionSequence) error('Could not load the saved list: ' + e.message); }
    finally { if (sequence === selectionSequence) { loadingPath = false; render(); } }
  });
  return selectionJob;
}

function renderProjects() {
  const query = $('search').value.toLowerCase();
  const filtered = state.projects.map((p, index) => ({...p,index})).filter(p => (!$('hideEmpty').checked || p.tsmis_connections || hasReadIssue(p)) && [p.path,...p.versions,...p.environments,...(p.services || [])].join(' ').toLowerCase().includes(query));
  $('projectsBody').innerHTML = filtered.map(p => `<tr data-index="${p.index}" class="${selected === p.index ? 'selected' : ''}"><td><button class="project-link" data-project="${p.index}">${escapeHtml(p.name)}</button>${hasReadIssue(p) ? '<span class="read-issue" title="Some project connections could not be read" aria-label="Read issue"> ⚠</span>' : ''}<span class="path" title="${escapeHtml(p.path)}">${escapeHtml(p.path)}</span></td><td>${p.environments.length ? p.environments.map(environmentBadge).join('') : '<span class="muted">—</span>'}</td><td class="mono">${p.versions.length ? p.versions.map(escapeHtml).join('<br>') : '<span class="muted">Not reported</span>'}</td><td>${p.services?.length ? p.services.map(escapeHtml).join('<br>') : '<span class="muted">—</span>'}</td><td aria-hidden="true">›</td></tr>`).join('');
  $('noMatches').hidden = !state.projects.length || !!filtered.length;
}
async function selectProject(index) {
  const sequence = ++detailSequence;
  const project = await api.get_project(index);
  if (!project || sequence !== detailSequence) return;
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
  if (busy || state.running) return;
  busy = true; error(''); notice(''); render();
  try {
    if (pathDirty || loadingPath) await switchPath(pendingRoot || $('folder').value);
    else await selectionJob;
    if (pathDirty) return;
    const response = await api.start_scan(values(), diagnostics);
    if (!response.ok) { error(response.error); return; }
    settings = values(); resetDetails(); state.running = true;
    await poll();
  } finally { busy = false; render(); }
}
async function poll() {
  if (polling) return;
  polling = true;
  const sequence = selectionSequence;
  try {
    const previousRunning = state.running;
    const next = await api.get_scan_state();
    if (sequence !== selectionSequence) return;
    state = next; error(state.error || state.save_error || ''); render();
    if (previousRunning && !state.running) {
      const paths = await api.get_saved_paths();
      if (sequence !== selectionSequence) return;
      renderSavedPaths(paths); render();
      if (state.diagnostic_scan) notice('Diagnostics ready. Settings → Test / diagnostics → Save diagnostic ZIP.');
    }
  } catch(e) { if (sequence === selectionSequence) error('Lost contact with the app: ' + e.message); }
  finally { polling = false; }
}
async function clearList() {
  if (busy || state.running || pathDirty) return;
  busy = true; render();
  try {
    const response = await api.clear_list(settings.root);
    if (!response.ok) { error(response.error); return; }
    state = response.state; resetDetails(); renderSavedPaths(response.saved_paths);
    error(''); notice('Saved list cleared.');
  } finally { busy = false; render(); }
}

async function saveResults(diagnostics) {
  const result = await api.save_results(diagnostics);
  if (result.ok) { notice('Saved: ' + result.path); if (diagnostics) $('diagnosticsDialog').close(); }
  else if (!result.cancelled) error(result.error);
}
async function boot(bridge) {
  api = bridge;
  const initial = await api.get_initial_state();
  settings = initial.settings; state = initial.state;
  renderSavedPaths(initial.saved_paths); error(initial.warning || '');
  $('folder').value = settings.root; $('recursive').checked = settings.recursive;
  $('version').textContent = 'v' + initial.version;
  $('demoBanner').hidden = !isDemo;
  bind('browseBtn', browseFolder);
  $('folder').addEventListener('change', async () => {
    try {
      if ($('folder').value === '__browse__') await browseFolder();
      else await switchPath($('folder').value);
    } catch(e) { error('Could not choose the folder: ' + e.message); }
  });
  bind('scanBtn', () => startScan(false));
  bind('stopBtn', () => api.stop_scan());
  bind('exportBtn', () => saveResults(false));
  bind('clearBtn', clearList);
  bind('settingsBtn', () => { $('pythonPath').value = settings.python; $('match').value = settings.match; dialog('settingsDialog'); });
  bind('pythonBrowseBtn', async () => { const path = await api.choose_python(); if (path) $('pythonPath').value = path; });
  bind('saveSettingsBtn', async () => {
    const next = {...values(), python: $('pythonPath').value.trim(), match: $('match').value.trim()};
    const result = await api.save_settings(next);
    if (result.ok) { settings = next; $('settingsDialog').close(); notice('Settings saved. Refresh to apply them.'); }
    else { $('settingsDialog').close(); error(result.error); }
  });
  bind('diagnosticsBtn', () => { $('settingsDialog').close(); dialog('diagnosticsDialog'); });
  bind('runDiagnosticsBtn', async () => { $('diagnosticsDialog').close(); await startScan(true); });
  bind('saveDiagnosticsBtn', () => saveResults(true));
  bind('closeDetailsBtn', () => { $('details').close(); selected = null; activeProject = null; renderProjects(); });
  $('projectsBody').addEventListener('click', event => { const row = event.target.closest('[data-index]'); if(row) selectProject(Number(row.dataset.index)).catch(e => error(e.message)); });
  $('search').addEventListener('input', renderProjects); $('hideEmpty').addEventListener('change', renderProjects); $('allLayers').addEventListener('change', renderDetails);
  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
  bind('updatesBtn', async () => {
    $('settingsDialog').close(); dialog('updatesDialog'); $('updateMessage').textContent = 'Checking for updates…';
    $('downloadUpdateBtn').hidden = true; $('installUpdateBtn').hidden = true;
    const result = await api.check_updates();
    $('updateMessage').textContent = result.message || result.error;
    $('downloadUpdateBtn').hidden = !result.available;
  });
  bind('downloadUpdateBtn', async () => {
    $('downloadUpdateBtn').disabled = true; $('updateMessage').textContent = 'Downloading and checking the update…';
    try { const result = await api.download_update(); $('updateMessage').textContent = result.message || result.error; $('installUpdateBtn').hidden = !result.ok; if (result.ok) $('downloadUpdateBtn').hidden = true; }
    finally { $('downloadUpdateBtn').disabled = false; }
  });
  bind('installUpdateBtn', async () => {
    $('installUpdateBtn').disabled = true; $('updateMessage').textContent = 'Preparing to restart…';
    try { const result = await api.install_update(); $('updateMessage').textContent = result.message || result.error; if (!result.ok) $('installUpdateBtn').disabled = false; }
    catch(e) { $('installUpdateBtn').disabled = false; throw e; }
  });
  render();
  document.body.dataset.ready = 'true';
  setInterval(() => { if(state.running) poll(); }, 600);
}
if (isDemo) { boot(window.demoApi).catch(e => error(e.message)); }
else {
  let started = false;
  const connect = () => { if (!started && typeof window.pywebview?.api?.get_initial_state === 'function') { started = true; boot(window.pywebview.api).catch(e => error(e.message)); } };
  window.addEventListener('pywebviewready', connect);
  const timer = setInterval(() => { connect(); if(started) clearInterval(timer); }, 150);
}
