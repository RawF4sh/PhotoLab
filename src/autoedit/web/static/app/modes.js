// Automatic exports use the same local pipeline as Run AI Edit in the photo editor.
import { el, api, go, state, setBar, backButton, gearButton, baseName, joinPath, thumbUrl, toast } from './main.js';
import { runTool } from './tools.js';

const button = (label, action, cls = 'line', disabled = false) =>
  el('button', { type: 'button', class: cls, disabled, on: { click: action } }, label);

export const home = {
  mount(root) {
    setBar(el('span', { class: 'title grow' }, 'AutoEdit'), button('Guided workspace', () => go('workspace'), 'link'));
    root.append(el('section', { class: 'mode-home' }, el('h1', null, 'How would you like to edit?'),
      el('nav', { class: 'mode-choices', 'aria-label': 'Editing mode' },
        el('a', { href: '#/automatic', class: 'mode-choice auto-choice' },
          el('span', { class: 'mode-glyph', 'aria-hidden': 'true' }, '✦'), 'Automatic edit'),
        el('a', { href: '#/editor', class: 'mode-choice' },
          el('span', { class: 'mode-glyph manual-glyph', 'aria-hidden': 'true' }, '◩'), 'Photo editor'))));
  },
};

const receiptKey = 'autoedit.automatic.job';
let jobId;
try { jobId = sessionStorage.getItem(receiptKey); } catch (_) { /* Storage is optional. */ }
let panel = null, selection = null, snapshot = null, pending = '', error = '', timer = 0, generation = 0;
let cancelling = false;
let styles = [], style = 'standard', styleRequest = null, styleError = '';
const finished = s => s && ['done', 'error', 'cancelled'].includes(s.status);
const photoCount = n => n + (n === 1 ? ' photo' : ' photos');

function remember(id) {
  jobId = id;
  try {
    if (id) sessionStorage.setItem(receiptKey, id);
    else sessionStorage.removeItem(receiptKey);
  } catch (_) { /* The current page can still track the export. */ }
}

function previewStrip(rows, original = false) {
  const single = original && rows.length === 1;
  return el('div', { class: 'automatic-thumbs' + (single ? ' single-original' : '') },
    ...rows.slice(0, 6).map(row => el('img', {
      src: thumbUrl(row.thumb || row.path, single ? 960 : 320),
      alt: (original ? 'Original ' : 'Edited ') + baseName(row.path), loading: 'lazy' })));
}

function referenceFor(entry) {
  return entry.reference ?? state.settings.reference;
}

async function chooseReference() {
  if (!selection || pending || jobId) return;
  const entry = selection, turn = generation;
  entry.referenceOpen = true;
  pending = 'Choosing reference…'; error = ''; paint();
  try {
    const { path } = await api('/pick?kind=file', {});
    if (path && entry === selection && generation === turn) entry.reference = path;
  } catch (err) { if (entry === selection && generation === turn) error = err.message; }
  finally { pending = ''; paint(); }
}

function referenceControl(entry) {
  const path = referenceFor(entry);
  const filename = el('span', { class: 'automatic-reference-name' }, path ? ' · ' + baseName(path) : '');
  const remove = button('Remove reference', () => {
    entry.reference = ''; entry.referenceOpen = true; paint();
  }, 'link', !!pending);
  remove.hidden = !path;
  return el('details', { class: 'automatic-reference', open: entry.referenceOpen,
    on: { toggle: e => {
      if (!e.currentTarget.isConnected) return;
      entry.referenceOpen = e.currentTarget.open;
      const current = referenceFor(entry);
      filename.textContent = current ? ' · ' + baseName(current) : '';
      remove.hidden = !current;
    } } },
    el('summary', null, 'Match a reference photo', filename),
    el('div', { class: 'automatic-actions' }, button('Choose reference', chooseReference, 'line', !!pending), remove));
}

async function choose(kind) {
  if (pending || jobId) return;
  pending = 'Opening…'; error = ''; paint();
  try {
    const path = kind === 'sample' ? (await api('/sample', {})).dir : (await api('/pick?kind=' + kind, {})).path;
    if (!path) return;
    if (kind === 'file') {
      const slash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
      const dir = path.slice(0, slash + 1);
      selection = { label: baseName(path), dir, files: [{ path }] };
    } else {
      const groups = await api('/groups?dir=' + encodeURIComponent(path));
      const files = groups.map(g => ({ path: joinPath(path, g.raw || g.pixel), thumb: joinPath(path, g.pixel || g.raw) }));
      selection = files.length ? { label: baseName(path), dir: path, files } : null;
      if (!files.length) error = 'There are no supported photos in this folder.';
    }
    loadStyles();
  } catch (err) { error = err.message; }
  finally { pending = ''; paint(); }
}

async function startJob() {
  if (!selection || pending || jobId) return;
  pending = 'Starting export…'; error = ''; paint();
  try {
    const stamp = new Date().toISOString().replaceAll(':', '-');
    const out = joinPath(selection.dir, 'AutoEdit Exports/Automatic ' + stamp + ' ' + crypto.getRandomValues(new Uint32Array(1))[0].toString(16));
    const options = { consistent:style === 'auto', ...state.settings, backend: state.finder, input_dir: selection.dir,
      files: selection.files.map(f => f.path), out_dir: out, local_only: true, style,
      translator: 'off', decider: 'rules', allow_cloud: false };
    const reference = referenceFor(selection);
    if (reference) options.reference = reference;
    else delete options.reference;
    snapshot = await api('/jobs', options);
    remember(snapshot.id);
  } catch (err) { error = err.message; }
  finally { pending = ''; paint(); }
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(timer);
  if (panel && jobId && !finished(snapshot)) timer = setTimeout(refresh, 1200);
}

async function refresh() {
  const id = jobId, turn = ++generation;
  if (!panel || !id) return;
  clearTimeout(timer);
  try {
    const next = await api('/jobs/' + encodeURIComponent(id));
    if (!panel || generation !== turn || jobId !== id) return;
    const changed = !snapshot || snapshot.status !== next.status || snapshot.processed !== next.processed || !!error;
    snapshot = next; error = '';
    if (changed) paint();
  } catch (err) {
    if (!panel || generation !== turn || jobId !== id) return;
    if (err.status === 404) {
      remember(null); snapshot = null; cancelling = false;
      error = 'This export session has ended. Look in AutoEdit Exports for saved photos.';
    } else error = 'Reconnecting to the export…';
    paint();
  }
  if (generation === turn) schedulePoll();
}

async function cancelJob() {
  if (!jobId || cancelling || finished(snapshot)) return;
  cancelling = true; paint();
  try {
    await api('/jobs/' + encodeURIComponent(jobId) + '/control', { action: 'cancel' });
    await refresh();
  } catch (err) { cancelling = false; error = err.message; paint(); }
}

function reset() {
  clearTimeout(timer); generation++; remember(null);
  snapshot = null; selection = null; cancelling = false; error = ''; paint();
  loadStyles();
}

async function loadStyles() {
  if (styleRequest) return;
  styleError = '';
  try {
    styleRequest = api('/local-styles');
    const catalog = await styleRequest;
    if (!catalog.styles?.some(entry => entry.id === 'standard')) throw new Error();
    styles = catalog.styles;
    if (!styles.some(entry => entry.id === style)) style = 'standard';
  } catch (_) { styles = []; style = 'standard'; styleError = 'AI edit styles could not be loaded.'; }
  finally { styleRequest = null; paint(); }
}

function exportControls() {
  const count = selection?.files.length || 0;
  const select = el('select', { id: 'automatic-style', disabled: !!pending || !styles.length,
    on: { change: e => { style = e.target.value; } } },
    ...styles.map(entry => el('option', { value: entry.id }, entry.label)));
  select.value = style;
  return el('div', { class: 'automatic-controls' },
    styles.length ? el('label', { class: 'automatic-style', htmlFor: select.id }, 'Local style', select) :
      styleError ? el('div', { role: 'status' }, 'Simple is available. ', button('Reload styles', loadStyles, 'link')) :
        el('span', { role: 'status' }, 'Loading local styles…'),
    selection ? referenceControl(selection) : null,
    button('Run AI Edit & export', startJob, 'primary', !count || !!pending));
}

function aiReceipt(items) {
  const exported = items.filter(i => i.out && i.ai_status);
  if (!exported.length) return null;
  const counts = {};
  for (const item of exported) counts[item.ai_status] = (counts[item.ai_status] || 0) + 1;
  const labels = { applied: 'AI adjusted', unchanged: 'AI kept local edit', fallback: 'Used local fallback' };
  const summary = Object.entries(labels).filter(([key]) => counts[key]).map(([key, label]) => label + ' · ' + counts[key]);
  return el('details', { class: 'automatic-issues' }, el('summary', null, summary.join(' / ')),
    el('ul', null, ...exported.map(i => el('li', null, el('strong', null, i.name + ' — '),
      labels[i.ai_status], i.ai_reason ? '. ' + i.ai_reason : ''))));
}

async function reopenGroup(item) {
  const {loadPhotoPath} = await import('./folder.js');
  const {getPhotoValues, updatePhoto, persistProject} = await import('./project.js');
  const previousMode = state.editorMode;
  state.editorMode = true;
  try {
    if (!await loadPhotoPath(item.src)) { state.editorMode = previousMode; return; }
    const current = getPhotoValues(item.src);
    // Reopening must never replace work the photographer has already saved by hand.
    if (Object.keys(current).length) { toast('Opened this photo with its saved edits.'); return; }
    const shoot = item.auto_shoot;
    updatePhoto(item.src, {style:'auto', ai_enabled:true, look:'', reference:'', intensity:shoot.intensity,
      scene:shoot.scene_override || undefined, base:{auto_shoot:shoot}, ...(item.reopen || {})}, {history:false});
    await persistProject();
  } catch (err) { toast(err.message); }
}

function drawJob() {
  if (!snapshot) return el('p', { role: 'status' }, 'Loading export…');
  const s = snapshot, exported = s.done + s.review;
  if (!finished(s)) {
    return el('div', { class: 'automatic-result' },
      el('p', { role: 'status' }, 'Editing ' + s.processed + ' of ' + photoCount(s.total)),
      el('progress', { max: s.total, value: s.processed, 'aria-label': 'Export progress' }),
      button(cancelling ? 'Stopping…' : 'Stop export', cancelJob, 'line', cancelling));
  }
  const issues = s.items.filter(item => item.error || item.warn);
  return el('div', { class: 'automatic-result' },
    el('h2', { role: 'status' }, s.status === 'cancelled' ? 'Export stopped' : exported ? 'Export complete' : 'Export failed'),
    el('p', null, photoCount(exported) + ' exported' + (s.failed ? ' · ' + s.failed + ' failed' : '')),
    aiReceipt(s.items),
    s.items.some(i => i.out && i.auto_shoot) ? el('details', {class:'automatic-issues'},
      el('summary', null, 'Open original in Photo editor'), el('ul', null, ...s.items.filter(i => i.out && i.auto_shoot)
        .map(i => el('li', null, button(i.name, () => reopenGroup(i), 'link'))))) : null,
    exported ? el('p', { class: 'automatic-path' }, s.out_dir) : null,
    exported ? previewStrip(s.items.filter(i => i.out).slice(0, 6).map(i => ({ path: i.out }))) : null,
    issues.length ? el('details', { class: 'automatic-issues' }, el('summary', null, 'Review ' + photoCount(issues.length)),
      el('ul', null, ...issues.map(i => el('li', null, el('strong', null, i.name + ' '), i.error || i.warn)))) : null,
    el('div', { class: 'automatic-actions' },
      exported ? button('Show exports', () => runTool('reveal', { path: s.out_dir }).catch(e => toast(e.message)), 'primary') : null,
      button('Choose more photos', reset)));
}

function paint() {
  if (!panel) return;
  const pick = !jobId && el('div', { class: 'automatic-actions' },
    button('Choose photo', () => choose('file'), 'line', !!pending),
    button('Choose folder', () => choose('folder'), 'line', !!pending));
  const children = [el('h1', null, 'Automatic edit'), pick,
    !jobId && selection ? el('div', { class: 'automatic-selection' },
      el('h2', null, selection.label), el('p', null, photoCount(selection.files.length)),
      previewStrip(selection.files, true)) : null,
    !jobId && !selection ? button('Try a sample set', () => choose('sample'), 'link', !!pending) : null,
    !jobId ? exportControls() : null,
    jobId ? drawJob() : null,
    pending ? el('p', { role: 'status' }, pending) : null,
    error ? el('p', { role: 'status', class: 'automatic-message' }, error) : null];
  panel.replaceChildren(...children.filter(Boolean));
}

export const automatic = {
  mount(root) {
    setBar(backButton('Start', 'start'), el('span', { class: 'title grow' }, 'AutoEdit'), gearButton());
    panel = el('section', { class: 'automatic-panel' });
    root.append(panel); paint();
    if (!jobId) loadStyles();
    if (jobId && !finished(snapshot)) refresh();
  },
  unmount() { panel = null; generation++; clearTimeout(timer); },
  openFolder() { choose('folder'); },
};
