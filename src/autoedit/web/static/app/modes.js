// Automatic jobs use the same local editing pipeline as the guided workspace with their own output folder.
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
          el('span', { class: 'mode-glyph', 'aria-hidden': 'true' }, '✦'), 'Automatic'),
        el('a', { href: '/editor/', class: 'mode-choice' },
          el('span', { class: 'mode-glyph manual-glyph', 'aria-hidden': 'true' }, '◩'), 'Photo editor'))));
  },
};

const receiptKey = 'autoedit.automatic.job';
let jobId;
try { jobId = sessionStorage.getItem(receiptKey); } catch (_) { /* Storage is optional. */ }
let panel = null, selection = null, snapshot = null, pending = '', error = '', timer = 0, generation = 0;
let cancelling = false;
const finished = s => s && ['done', 'error', 'cancelled'].includes(s.status);
const photoCount = n => n + (n === 1 ? ' photo' : ' photos');

function remember(id) {
  jobId = id;
  try {
    if (id) sessionStorage.setItem(receiptKey, id);
    else sessionStorage.removeItem(receiptKey);
  } catch (_) { /* The current page can still track the export. */ }
}

function thumbnail(path, name) {
  return el('img', { src: thumbUrl(path, 320), alt: name, title: name, loading: 'lazy' });
}

function previewStrip(rows) {
  return el('div', { class: 'automatic-thumbs' },
    ...rows.slice(0, 6).map(row => thumbnail(row.thumb || row.path, baseName(row.path))));
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
  } catch (err) { error = err.message; }
  finally { pending = ''; paint(); }
}

async function startJob() {
  if (!selection || pending || jobId) return;
  pending = 'Starting export…'; error = ''; paint();
  try {
    const stamp = new Date().toISOString().replaceAll(':', '-');
    const out = joinPath(selection.dir, 'AutoEdit Exports/Automatic ' + stamp + ' ' + crypto.getRandomValues(new Uint32Array(1))[0].toString(16));
    // What the Settings sheet shows applies here too: the region finder, RAW, quality and export choices.
    // A Guided folder's own look, strength and sentence do not, and nothing is sent to a cloud model.
    snapshot = await api('/jobs', { ...state.settings, backend: state.finder, input_dir: selection.dir,
      files: selection.files.map(f => f.path), out_dir: out, local_only: true, translator: 'off' });
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
  const children = [el('h1', null, 'Automatic'), pick,
    !jobId && selection ? el('div', { class: 'automatic-selection' },
      el('h2', null, selection.label), el('p', null, photoCount(selection.files.length)),
      previewStrip(selection.files),
      button('Edit and export', startJob, 'primary', !!pending)) : null,
    !jobId && !selection ? button('Try a sample set', () => choose('sample'), 'link', !!pending) : null,
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
    if (jobId && !finished(snapshot)) refresh();
  },
  unmount() { panel = null; generation++; clearTimeout(timer); },
  openFolder() { choose('folder'); },
};
