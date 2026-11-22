// Start screen and Folder screen: open, recent, grid, side panel, the batch job and its event stream.
// Note: main.js and this file import each other; nothing here may use main.js at load time, only inside functions.
import { el, api, state, store, nice, thumbUrl, joinPath, baseName, getSchema, go, toast, openSheet, setBar, backButton, gearButton } from './main.js';
import { runOptions } from './settings.js';
import { loadEntries, textOf, marksOf } from './photo.js';
import { runTool } from './tools.js';
import { openShare, exportsDir } from './share.js';

const stemOf = n => n.replace(/\.[^./\\]+$/, '').toLowerCase();
const MAX_RECENT = 8;
const DATA_IMG = /^data:image\//;

const view = { note: '', kind: '', filter: 'all', out: '', hits: null, findNote: '', ui: null, last: -1 };   // ui: the live elements, null when the screen is away
let stream = null;

function stopStream() { if (stream) { stream.close(); stream = null; } }
const running = () => !!state.job && (state.job.status === 'running' || state.job.status === 'paused');

/* ---- opening a folder ------------------------------------------------------------------------ */
export async function loadFolderPath(path) {
  if (running()) {   // a started batch keeps its folder; ponytail: no background handle, so refuse instead of losing Pause and Cancel
    if (path === state.dir) go('folder'); else toast('A batch is running. Cancel or finish it before opening another folder.');
    return;
  }
  const g = await api('/groups?dir=' + encodeURIComponent(path)).catch(e => { toast(e.message); return null; });
  if (!g) return;
  if (!Array.isArray(g) || !g.length) { toast('No photos in this folder.'); return; }
  stopStream();
  Object.assign(state, { dir: path, job: null, look: '', genre: '', strength: null, runText: '', exportPreset: '' });
  state.files = g.map(x => ({ name: x.raw || x.pixel, path: joinPath(path, x.raw || x.pixel),
    thumb: joinPath(path, x.pixel || x.raw), checked: true, status: '' }));
  Object.assign(view, { note: '', kind: '', filter: 'all', out: '', hits: null, findNote: '', last: -1 });
  try {   // photos already exported show as finished; no exports folder yet is fine
    const done = new Map((await api('/list?dir=' + encodeURIComponent(exportsDir()))).map(n => [stemOf(n), joinPath(exportsDir(), n)]));
    state.files.forEach(f => { const p = done.get(stemOf(f.name)); if (p) { f.status = 'finished'; f.exported = p; } });
  } catch (_) { /* nothing exported yet */ }
  store.set('recent', [{ path, name: baseName(path), count: g.length, at: Date.now() },
    ...recents().filter(r => r.path !== path)].slice(0, MAX_RECENT));
  go('folder');
}

export async function openFolderDialog() {
  try { const r = await api('/pick?kind=folder', {}); if (r.path) await loadFolderPath(r.path); }
  catch (e) { toast(e.message); }
}

const recents = () => {   // whatever is stored, only well-formed rows
  const r = store.get('recent', []);
  return Array.isArray(r) ? r.filter(x => x && typeof x.path === 'string' && typeof x.name === 'string' && Number.isFinite(x.count) && Number.isFinite(x.at)) : [];
};
const ago = ms => {
  const d = Math.floor((Date.now() - ms) / 86400000);
  return d < 1 ? 'today' : d === 1 ? 'yesterday' : d + ' days ago';
};

export const screen = {   // Start
  mount(root) {
    setBar(el('span', { class: 'title grow' }, 'AutoEdit'), gearButton());
    const recent = recents();
    root.append(el('div', { class: 'start' },
      el('button', { type: 'button', class: 'primary', on: { click: openFolderDialog } }, 'Open folder'),
      el('p', { class: 'fine' }, 'Your photos stay on this computer.'),
      recent.length ? el('div', { class: 'recent' }, el('h3', null, 'Recent'),
        ...recent.map(r => el('button', { type: 'button', on: { click: () => loadFolderPath(r.path) } },
          r.name, el('small', null, r.count + ' photos · opened ' + ago(r.at))))) : null));
  },
};

/* ---- the batch job --------------------------------------------------------------------------- */
function etaText(s) {
  if (s.eta_s == null || s.status !== 'running') return '';
  return s.eta_s >= 90 ? 'about ' + Math.round(s.eta_s / 60) + ' min' : 'about ' + Math.max(1, s.eta_s) + ' s';
}

function applySnap(s) {
  state.job = { id: s.id, status: s.status };
  (s.items || []).forEach(it => {
    const f = state.files.find(x => x.path === it.src);
    if (f) { f.status = it.status; f.error = it.error || ''; }
  });
  const bits = [];
  if (s.review) bits.push(s.review + ' to look at');
  if (s.failed) bits.push(s.failed + ' failed');
  view.kind = '';
  if (s.status === 'done') {
    view.kind = 'ok';
    view.note = 'Done: ' + s.done + (s.done === 1 ? ' photo' : ' photos') + ' in AutoEdit Exports' + (bits.length ? ' · ' + bits.join(' · ') : '');
  } else if (s.status === 'cancelled') view.note = 'Stopped after ' + s.processed + ' of ' + s.total + '.';
  else if (s.status === 'error' || s.status === 'failed') {
    const bad = (s.items || []).find(i => i.error); view.kind = 'bad';
    view.note = 'The batch stopped: ' + ((bad && bad.error) || s.status);
  } else {
    const head = (s.status === 'paused' ? 'Paused at ' : 'Editing ') + s.processed + ' of ' + s.total;
    view.note = [head, etaText(s), ...bits].filter(Boolean).join(' · ');
  }
  view.out = s.out_dir || '';
  if (!running()) stopStream();
  paintAll();
}

function startStream(id) {
  const es = stream = new EventSource('/api/v1/jobs/' + encodeURIComponent(id) + '/events');
  const take = ev => { try { applySnap(JSON.parse(ev.data)); } catch (_) { /* a broken frame */ } };
  es.addEventListener('snapshot', take);
  es.addEventListener('done', ev => { take(ev); es.close(); });
  es.onerror = () => {   // stream lost: ask every two seconds until the job ends
    es.close();
    const poll = () => {
      if (!running() || state.job.id !== id) return;
      api('/jobs/' + encodeURIComponent(id)).then(applySnap).catch(() => {}).finally(() => setTimeout(poll, 2000));
    };
    setTimeout(poll, 2000);
  };
}

async function editPhotos() {
  const files = state.files.filter(f => f.checked);
  if (!files.length || running()) return;
  state.files.forEach(f => { f.status = ''; f.error = ''; });
  view.note = 'Starting…'; view.kind = ''; paintAll();
  try {
    const photo_edits = {}, photo_marks = {};   // each photo's own sentences and marks, as the Photo screen keeps them
    files.forEach(f => {
      const l = loadEntries(f.path), t = textOf(l), m = marksOf(l);
      if (t) photo_edits[f.path] = t;
      if (m.length) photo_marks[f.path] = m;
    });
    const s = await api('/jobs', { input_dir: state.dir, files: files.map(f => f.path), ...runOptions(),
      ...(Object.keys(photo_edits).length ? { photo_edits } : {}), ...(Object.keys(photo_marks).length ? { photo_marks } : {}) });
    applySnap(s); startStream(s.id);
  } catch (e) { state.job = null; view.note = e.message; view.kind = 'bad'; paintAll(); }
}

const control = action => state.job && api('/jobs/' + encodeURIComponent(state.job.id) + '/control', { action })
  .then(applySnap).catch(e => { view.note = e.message; view.kind = 'bad'; paintAll(); });

/* ---- painting -------------------------------------------------------------------------------- */
function paintAll() {
  const u = view.ui; if (!u) return;
  const n = state.files.filter(f => f.checked).length, total = state.files.length;
  state.files.forEach(f => {
    if (!f.dot) return;
    if (f.status) f.dot.dataset.status = f.status; else delete f.dot.dataset.status;
    f.cell.hidden = !(view.filter === 'all' || (view.filter === 'review' && f.status === 'review') || (view.filter === 'failed' && f.status === 'failed'));
    if (view.hits && !view.hits.has(f.path)) f.cell.hidden = true;
    f.box.checked = f.checked;
    f.btn.title = f.error || '';
  });
  u.all.checked = n === total; u.all.indeterminate = n > 0 && n < total;
  u.allText.textContent = 'All · ' + total;
  u.run.textContent = 'Edit ' + n + (n === 1 ? ' photo' : ' photos');
  u.run.disabled = !n || running();
  u.note.textContent = view.note; u.line.className = 'statusline' + (view.kind ? ' ' + view.kind : '');
  u.line.title = view.out;
  u.findNote.textContent = view.findNote; u.clear.hidden = !view.hits;
  u.pause.hidden = u.cancel.hidden = !running();
  u.pause.textContent = state.job && state.job.status === 'paused' ? 'Resume' : 'Pause';
}

function tile(f, i) {
  f.btn = el('button', { type: 'button', 'aria-label': 'Open ' + f.name, on: { click: () => { view.last = i; go('photo', { index: i }); } } },
    el('img', { src: thumbUrl(f.thumb, 320), alt: '', loading: 'lazy' }));
  f.box = el('input', { type: 'checkbox', checked: f.checked, 'aria-label': 'Include ' + f.name,
    on: { change: e => { f.checked = e.target.checked; paintAll(); } } });
  f.dot = el('span', { class: 'dot' });
  f.cell = el('li', { class: 'cell' }, f.btn, f.box, f.dot);
  return f.cell;
}

/* ---- find ------------------------------------------------------------------------------------ */
// A hit is an exported photo or its sidecar; it belongs to the source whose stem it starts with.
const baseOf = p => baseName(p).toLowerCase();

async function runFind(input) {
  const words = input.value.trim().split(/\s+/).filter(Boolean);
  if (!words.length) { clearFind(input); return; }
  view.findNote = 'Looking…'; paintAll();
  try {
    const { hits } = await runTool('find', { folder: exportsDir(), words });
    const names = hits.map(h => baseOf(h.image || h.sidecar));
    const mine = state.files.filter(f => { const s = stemOf(f.name); return names.some(b => b.startsWith(s) && !/[a-z0-9]/.test(b.charAt(s.length))); });
    view.hits = new Set(mine.map(f => f.path));
    view.findNote = mine.length ? mine.length + ' of ' + state.files.length + ' photos' : 'No photo shows all of: ' + words.join(', ');
  } catch (e) { view.hits = null; view.findNote = e.message; }
  paintAll();
}

function clearFind(input) { view.hits = null; view.findNote = ''; if (input) input.value = ''; paintAll(); }

/* ---- the side panel -------------------------------------------------------------------------- */
function select(options, value, onChange, id) {
  const s = el('select', { id, on: { change: e => onChange(e.target.value) } }, ...options.map(([v, t]) => el('option', { value: v }, t)));
  s.value = value; return s;
}
const field = (label, id, ...kids) => el('div', { class: 'field' }, el('label', { htmlFor: id }, label), ...kids);

function surprise(lookSelect) {
  const first = state.files.find(f => f.checked) || state.files[0];
  openSheet('Surprise me', (body, close) => {
    const note = el('small', null, 'Rendering six looks…'), grid = el('div', { class: 'looks' });
    const more = el('button', { type: 'button', class: 'line', disabled: true }, 'Six more');
    body.append(note, grid, more);
    const run = async () => {
      more.disabled = true; grid.replaceChildren(); note.textContent = 'Rendering six looks…';
      try {
        const o = runOptions(); delete o.edit; delete o.export;
        const j = await api('/variants', { file: first.path, surprise: true, ...o });
        note.textContent = 'Seed ' + j.seed + '. The same seed gives the same six. Click one to use it.';
        (j.looks || []).forEach(name => {
          const v = (j.variants || []).find(x => x.name === name) || {};
          grid.append(el('button', { type: 'button', on: { click: () => {
            if (![...lookSelect.options].some(o => o.value === name)) lookSelect.add(new Option(nice(name), name));
            lookSelect.value = state.look = name; close();
          } } }, el('img', { alt: nice(name), src: DATA_IMG.test(v.after) ? v.after : '' }), el('span', null, nice(name)),
          v.needs_review ? el('small', null, 'may need a look on this photo') : null));
        });
      } catch (e) { note.textContent = e.message; }
      more.disabled = false;
    };
    more.addEventListener('click', run); run();
  });
}

function sidePanel() {
  const side = el('aside', { class: 'side', 'aria-label': 'Edit options' });
  const lookSel = select([['', 'Default']], '', v => { state.look = v; }, 'look');
  const genreSel = select([['', 'None']], state.genre, v => { state.genre = v; }, 'genre');
  const genreField = field('Kind of photo', 'genre', genreSel); genreField.hidden = true;
  const range = el('input', { type: 'range', id: 'strength', min: 0, max: 2, step: 0.05, value: 0.8 });
  const out = el('span', { class: 'value num' }), reset = el('button', { type: 'button', class: 'link', hidden: true }, 'Reset');
  let dflt = 0.8;
  const paintStrength = () => { out.textContent = Number(range.value).toFixed(2); reset.hidden = state.strength === null; };
  range.addEventListener('input', () => { state.strength = Number(range.value); paintStrength(); });
  reset.addEventListener('click', () => { state.strength = null; range.value = dflt; paintStrength(); });
  const text = el('textarea', { id: 'runText', rows: 3, value: state.runText, placeholder: 'e.g. darken the sky',
    on: { input: e => { state.runText = e.target.value; } } });
  const exportSel = select([['', 'Default']], state.exportPreset, v => { state.exportPreset = v; }, 'export');
  const exportField = field('Export as', 'export', exportSel); exportField.hidden = true;

  getSchema().then(sc => {
    const by = Object.fromEntries((sc.settings || []).map(s => [s.key, s]));
    (by.look && by.look.choices || []).forEach(n => lookSel.add(new Option(nice(n), n)));
    lookSel.value = state.look;
    if (by.intensity) { range.min = by.intensity.min; range.max = by.intensity.max; dflt = by.intensity.default; }
    range.value = state.strength === null ? dflt : state.strength; paintStrength();
    if (by.export && by.export.choices) {
      by.export.choices.forEach(n => exportSel.add(new Option(nice(n), n)));
      exportSel.value = state.exportPreset; exportField.hidden = false;
    }
  }).catch(e => toast('Settings could not be read: ' + e.message));
  api('/genres').then(list => {
    list.filter(g => g.offered).forEach(g => genreSel.add(new Option(nice(g.name), g.name)));
    genreSel.value = state.genre; genreField.hidden = genreSel.options.length < 2;
  }).catch(() => {});

  const run = el('button', { type: 'button', class: 'primary', on: { click: editPhotos } }, 'Edit photos');
  side.append(
    field('Look', 'look', lookSel, el('button', { type: 'button', class: 'link', on: { click: () => surprise(lookSel) } }, 'Surprise me')),
    genreField,
    el('div', { class: 'field' }, el('div', { class: 'row' }, el('label', { htmlFor: 'strength' }, 'Strength'), out, reset), range),
    field('What to change in every photo', 'runText', text),
    exportField,
    el('small', null, 'to AutoEdit Exports'),
    run);
  return { side, run };
}

/* ---- the screen ------------------------------------------------------------------------------ */
export const folder = {
  mount(root) {
    const findBox = el('input', { type: 'search', placeholder: 'Find', title: 'Words from what the photos show, then Enter', 'aria-label': 'Find',
      on: { keydown: e => { if (e.key === 'Enter') runFind(e.target); } } });
    setBar(backButton('Start', 'start'),
      el('span', { class: 'title' }, baseName(state.dir) + ' · ' + state.files.length + (state.files.length === 1 ? ' photo' : ' photos')),
      el('span', { class: 'grow' }),
      findBox,
      el('button', { type: 'button', on: { click: openShare } }, 'Share'),
      el('button', { type: 'button', on: { click: () => go('tools') } }, 'Tools'),
      gearButton());
    const all = el('input', { type: 'checkbox', id: 'all', on: { change: e => { state.files.forEach(f => { f.checked = e.target.checked; }); paintAll(); } } });
    const allText = el('span');
    const filter = select([['all', 'All'], ['review', 'Look at'], ['failed', 'Failed']], view.filter, v => { view.filter = v; paintAll(); }, 'show');
    const note = el('span', { class: 'grow', role: 'status' });
    const findNote = el('span', { class: 'findnote', role: 'status' });
    const clear = el('button', { type: 'button', class: 'link', hidden: true, on: { click: () => clearFind(findBox) } }, 'Clear');
    const pause = el('button', { type: 'button', hidden: true, on: { click: () => control(state.job.status === 'paused' ? 'resume' : 'pause') } }, 'Pause');
    const cancel = el('button', { type: 'button', hidden: true, on: { click: () => control('cancel') } }, 'Cancel');
    const line = el('div', { class: 'statusline' }, note, pause, cancel);
    const { side, run } = sidePanel();
    root.append(el('div', { class: 'folder' },
      el('div', { class: 'main' },
        el('ul', { class: 'grid' }, ...state.files.map(tile)),
        el('div', { class: 'tools' }, el('label', null, all, ' ', allText), el('span', { class: 'grow' }),
          el('label', { htmlFor: 'show' }, 'Show '), filter, findNote, clear),
        line),
      side));
    view.ui = { all, allText, run, note, line, pause, cancel, findNote, clear };
    paintAll();
  },
  focus() {   // back from a photo: the tile it came from; else the first one shown
    const f = state.files[view.last] || state.files.find(x => !x.cell.hidden);
    if (f) f.btn.focus();
  },
  unmount() { view.ui = null; },
  key(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); editPhotos(); return true; }
    return false;
  },
};
