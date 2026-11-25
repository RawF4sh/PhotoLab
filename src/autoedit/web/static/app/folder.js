// Start screen and Folder screen: open, recent, grid, side panel, the batch job and its event stream.
// Note: main.js and this file import each other; nothing here may use main.js at load time, only inside functions.
import { el, api, state, store, nice, thumbUrl, joinPath, baseName, getSchema, go, toast, openSheet, setBar, backButton, gearButton } from './main.js';
import { runOptions, openSettings } from './settings.js';
import { loadEntries, textOf, marksOf, hideRequest } from './photo.js';
import { runTool } from './tools.js';
import { openShare, exportsDir } from './share.js';
import { getPhoto, getPhotoValues, updatePhoto, loadProject, persistProject, scheduleProjectSave, folderSettingsChanged, syncRecipeReview } from './project.js';
import { openPhotoPresets } from './presets.js';
import { workflowSteps, readinessCard } from './workflow.js';

const stemOf = n => n.replace(/\.[^./\\]+$/, '').toLowerCase();
const MAX_RECENT = 8;
const DATA_IMG = /^data:image\//;
const PAGE_SIZE = 100;

const view = { note: '', kind: '', filter: 'all', out: '', hits: null, findNote: '', ui: null, last: -1, page: 0, anchor: -1, burst: '' };   // ui: the live elements, null when the screen is away
let stream = null;
let opening = false;
let batchSettings = new Map();

function stopStream() { if (stream) { stream.close(); stream = null; } }
const running = () => !!state.job && (state.job.status === 'running' || state.job.status === 'paused');

/* ---- opening a folder ------------------------------------------------------------------------ */
export async function loadFolderPath(path, { photo } = {}) {
  if (running()) {   // a started batch keeps its folder; ponytail: no background handle, so refuse instead of losing Pause and Cancel
    if (path === state.dir) go('folder'); else toast('A batch is running. Cancel or finish it before opening another folder.');
    return;
  }
  if (opening) return;
  opening = true;
  try {
    if (await persistProject() === false) { toast('Your changes could not be saved. Keep this folder open and try again.'); return; }
    const g = await api('/groups?dir=' + encodeURIComponent(path)).catch(e => {
      if (e.status === 403 && e.code === 'forbidden') reopenFolder(path, photo);
      else toast(e.message);
      return null;
    });
    if (!g) return;
    if (!Array.isArray(g) || !g.length) { toast('No photos in this folder.'); return; }
    let saved;
    try { saved = await loadProject(path, { initialize: () => {
      stopStream();
      Object.assign(state, { dir: path, job: null, reviewQueue: false, look: '', genre: '', strength: null, runText: '', exportPreset: '', folderSliders: {}, recipes: [] });
      state.files = g.map(x => ({ name: x.raw || x.pixel, path: joinPath(path, x.raw || x.pixel),
        thumb: joinPath(path, x.pixel || x.raw), isRaw: Boolean(x.raw), checked: true, status: '', captureTime: x.capture_time }));
      // A library tile represents the RAW pair. An explicitly picked companion JPEG has its own pixels and project.
      if (photo && state.editorMode) {
        const pair = state.files.find(f => f.thumb === photo && f.path !== photo);
        if (pair) state.files.push({name:baseName(photo), path:photo, thumb:photo, isRaw:false, checked:false, status:'', captureTime:pair.captureTime});
      }
    } }); }
    catch (error) { toast('This project could not be opened: ' + error.message); return; }
    const last = Number.isInteger(saved.last_index) ? Math.min(saved.last_index, state.files.length - 1) : -1;
    Object.assign(view, { note: '', kind: '', filter: 'all', out: '', hits: null, findNote: '', last, page: Math.max(0, Math.floor(last / PAGE_SIZE)), anchor: -1, burst: '' });
    assignBursts();
    const output = exportsDir();
    api('/list?dir=' + encodeURIComponent(output)).then(names => {   // exports should not delay opening the workspace
      if (state.dir !== path) return;
      const done = new Map(names.map(n => [stemOf(n), joinPath(output, n)]));
      state.files.forEach(f => { const p = done.get(stemOf(f.name)); if (p) { if (!f.status) f.status = 'finished'; f.exported = p; } });
      scheduleProjectSave(); paintAll();
    }).catch(() => { /* nothing exported yet */ });
    store.set('recent', [{ path, name: baseName(path), count: g.length, at: Date.now() },
      ...recents().filter(r => r.path !== path)].slice(0, MAX_RECENT));
    scheduleProjectSave();
    if (photo) {
      const exact = state.files.findIndex(f => f.path === photo);
      const index = exact >= 0 ? exact : state.files.findIndex(f => f.thumb === photo);
      if (index >= 0) { view.last = index; go('photo', { index }); return true; }
    }
    go('folder');
    return true;
  } finally { opening = false; }
}

export async function loadPhotoPath(path) {
  const slash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  if (slash < 0) { toast('Choose a photo using Open photo.'); return; }
  return loadFolderPath(path.slice(0, slash) || '/', { photo: path });
}

function reopenFolder(path, photo) {
  openSheet('Reopen ' + baseName(path), (body, close) => {
    const choose = el('button', { type: 'button', class: 'primary', on: { click: async () => {
      choose.disabled = true;
      try { if (await openFolderDialog({ photo })) close(); }
      finally { if (choose.isConnected) choose.disabled = false; }
    } } }, 'Choose folder');
    body.append(el('p', null, 'Choose this folder again to reopen it with your saved edits.'),
      el('p', { class: 'reopen-folder-path' }, path),
      el('p', { class: 'fine' }, 'Your edits stay saved if you cancel.'),
      choose, el('button', { type: 'button', class: 'line', on: { click: close } }, 'Not now'));
  });
}

let picking = false;   // one dialog at a time
export async function openFolderDialog({ photo } = {}) {
  if (picking) return;
  picking = true;
  try { const r = await api('/pick?kind=folder', {}); if (r.path) return await loadFolderPath(r.path, { photo }); }
  catch (e) { toast(e.message); }
  finally { picking = false; }
}

async function openPhotoDialog() {
  if (picking) return;
  picking = true;
  try { const r = await api('/pick?kind=file', {}); if (r.path) await loadPhotoPath(r.path); }
  catch (e) { toast(e.message); }
  finally { picking = false; }
}

async function openSample(event) {
  const button = event.currentTarget; button.disabled = true;
  try {
    const sample = await api('/sample', {});
    await loadFolderPath(sample.dir);
    if (state.dir === sample.dir) { state.finder = 'stub'; scheduleProjectSave(); }
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

export async function dropped(event) {
  event.preventDefault(); event.currentTarget.classList?.remove('dragging');
  if (['', '#/start'].includes(location.hash)) state.editorMode = true;
  const file = event.dataTransfer?.files?.[0];
  // Some desktop hosts expose an actual local path; browsers deliberately do not.
  if (file?.path) {
    try { await api('/editor/import', {path:file.path}); await loadPhotoPath(file.path); return; }
    catch (error) { if (error.status !== 403) { toast(error.message); return; } }
  }
  if (!file) return;
  if (picking) return;
  picking = true;
  try {
    const r = await fetch('/api/v1/editor/drop?name=' + encodeURIComponent(file.name), { method: 'POST', body: file });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'Could not open the dropped photo.');
    const drops = store.get('browserDrops', []);
    store.set('browserDrops', [...(Array.isArray(drops) ? drops.filter(x => x.path !== data.path) : []),
      {path:data.path, sha256:data.sha256}].slice(-64));
    await loadPhotoPath(data.path);
  } catch (error) { toast(error.message); }
  finally { picking = false; }
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
  mount(root, { editor = false } = {}) {
    setBar(backButton('Start', 'start'), el('span', { class: 'title grow' }, editor ? 'Photo editor' : 'Guided workspace'), gearButton());
    const recent = recents();
    root.append(el('div', { class: 'start' },
      el('h1', null, editor ? 'Open a photo' : 'Guided workspace'),
      editor ? null : workflowSteps('open'),
      el('div', { class: 'start-actions', on: { dragover: e => { e.preventDefault(); e.currentTarget.classList.add('dragging'); }, dragleave: e => e.currentTarget.classList.remove('dragging'), drop: dropped } },
        el('button', { type: 'button', class: 'primary', on: { click: openPhotoDialog } }, 'Open photo'),
        el('button', { type: 'button', class: 'line', on: { click: openFolderDialog } }, 'Open folder')),
      el('button', { type: 'button', class: 'link', on: { click: openSample } }, 'Try a sample set'),
      editor ? null : readinessCard(openSettings),
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

function photoSignature(path) {
  const own = getPhotoValues(path);
  return JSON.stringify({ folder: runOptions(), photo: own,
    sliders: own.sliders === undefined ? state.folderSliders : own.sliders });
}

// A completed export can refer to an older edit while the photographer is still working.
export function applyBatchItem(file, item, submitted, currentSignature) {
  if (item.out) file.exported = item.out;
  if ((!submitted && file.dirty && item.auto_shoot) || (submitted && submitted.signature !== currentSignature)) {
    if (submitted && ['waiting', 'editing'].includes(file.status)) {
      file.status = submitted.status;
      file.error = submitted.error;
    }
    file.dirty = true;
    return;
  }
  file.status = item.status;
  file.error = item.error || item.warn || '';
  if (!item.out) return;
  const prior = getPhotoValues(file.path).base || {};
  if (item.auto_shoot || prior.auto_shoot) {
    const base = { ...prior };
    if (item.auto_shoot) base.auto_shoot = item.auto_shoot;
    else delete base.auto_shoot;
    updatePhoto(file.path, {base:Object.keys(base).length ? base : undefined}, {history:false});
  }
  file.dirty = false;
  if (item.object_checks) {
    // The quality verdict belongs to the export. A missing object applies only while
    // its adjustment is active and must disappear when that step is removed or disabled.
    file.status = item.quality_warn ? 'review' : 'finished';
    file.error = item.quality_warn || '';
    file.objectChecks = item.object_checks;
    syncRecipeReview(file, getPhotoValues(file.path));
  }
}

function applySnap(s) {
  state.job = { id: s.id, status: s.status };
  const byPath = new Map(state.files.map(file => [file.path, file]));
  for (const item of s.items || []) {
    const file = byPath.get(item.src);
    if (!file) continue;
    const submitted = batchSettings.get(file.path);
    const receipt = JSON.stringify([item.status, item.out, item.warn, item.error, item.quality_warn, item.object_checks, item.auto_shoot]);
    if (submitted && submitted.receipt === receipt) continue;
    if (submitted) submitted.receipt = receipt;
    const completed = item.out || item.status === 'failed';
    const signature = completed && submitted ? photoSignature(file.path) : submitted?.signature;
    applyBatchItem(file, item, submitted, signature);
  }
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
  scheduleProjectSave();
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
  view.note = 'Starting…'; view.kind = ''; paintAll();
  try {
    batchSettings = new Map(files.map(f => [f.path, {
      signature: photoSignature(f.path), status: f.status, error: f.error || '',
    }]));
    const photo_edits = {}, photo_marks = {}, photo_hide = {}, photo_options = {};   // exactly the same per-photo settings as Preview and Save
    files.forEach(f => {
      const l = loadEntries(f.path), t = textOf(l), m = marksOf(l), h = hideRequest(f.path);
      if (t) photo_edits[f.path] = t;
      if (m.length) photo_marks[f.path] = m;
      if (h) photo_hide[f.path] = h;
      const own = getPhoto(f.path), options = {};
      ['style', 'scene', 'look', 'intensity', 'neutral', 'reference', 'reference_strength'].forEach(k => { if (own[k] !== undefined) options[k] = own[k]; });
      const sliders = own.sliders === undefined ? state.folderSliders : own.sliders;
      if (sliders && Object.keys(sliders).length) options.photo_settings = sliders;
      if (own.base !== undefined) options.photo_base = own.base;
      ['region_settings', 'mask_refinements', 'photo_geometry', 'local_only', 'edit_steps'].forEach(key => { if (own[key] !== undefined) options[key] = own[key]; });
      if (Object.keys(options).length) photo_options[f.path] = options;
    });
    const s = await api('/jobs', { input_dir: state.dir, files: files.map(f => f.path), ...runOptions(),
      ...(Object.keys(photo_edits).length ? { photo_edits } : {}), ...(Object.keys(photo_marks).length ? { photo_marks } : {}),
      ...(Object.keys(photo_hide).length ? { photo_hide } : {}), ...(Object.keys(photo_options).length ? { photo_options } : {}) });
    applySnap(s); startStream(s.id);
  } catch (e) { state.job = null; view.note = e.message; view.kind = 'bad'; paintAll(); }
}

const control = action => state.job && api('/jobs/' + encodeURIComponent(state.job.id) + '/control', { action })
  .then(applySnap).catch(e => { view.note = e.message; view.kind = 'bad'; paintAll(); });

/* ---- painting -------------------------------------------------------------------------------- */
function assignBursts() {
  let group = [], id = 0;
  const time = value => typeof value === 'number' ? value * (value < 1e11 ? 1000 : 1) : Date.parse(value || '');
  const finish = () => { if (group.length > 1) { const key = 'burst' + ++id; group.forEach(f => { f.burst = key; }); } group = []; };
  for (const f of [...state.files].sort((a, b) => time(a.captureTime) - time(b.captureTime))) {
    const t = time(f.captureTime);
    if (!Number.isFinite(t)) { finish(); continue; }
    if (group.length && (t - time(group[group.length - 1].captureTime) > 2000)) finish();
    group.push(f);
  }
  finish();
}

function visibleStatus(file) {
  return file.recipeReview && !['failed', 'editing', 'waiting'].includes(file.status) ? 'review' : file.status;
}

function visibleFiles() {
  return state.files.filter(f => (view.filter === 'all' || visibleStatus(f) === view.filter || (view.filter === 'attention' && ['review', 'failed'].includes(visibleStatus(f))))
    && (!view.hits || view.hits.has(f.path)) && (!view.burst || f.burst === view.burst));
}

function paintGrid() {
  const u = view.ui; if (!u) return;
  const shown = visibleFiles(), pages = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
  view.page = Math.min(Math.max(0, view.page), pages - 1);
  const page = shown.slice(view.page * PAGE_SIZE, (view.page + 1) * PAGE_SIZE);
  const sig = page.map(f => f.path).join('\n');
  if (sig !== u.grid.dataset.signature) {
    state.files.forEach(f => { f.dot = f.cell = f.box = f.btn = null; });
    u.grid.replaceChildren(...page.map(f => tile(f, state.files.indexOf(f))));
    u.grid.dataset.signature = sig;
  }
  u.empty.hidden = !!shown.length;
  u.pageText.textContent = shown.length ? `${view.page * PAGE_SIZE + 1}–${Math.min((view.page + 1) * PAGE_SIZE, shown.length)} of ${shown.length}` : 'No photos';
  u.prev.disabled = view.page === 0; u.next.disabled = view.page === pages - 1;
  u.pager.hidden = shown.length <= PAGE_SIZE;
}

function paintAll() {
  const u = view.ui; if (!u) return;
  paintGrid();
  const n = state.files.filter(f => f.checked).length, total = state.files.length;
  state.files.forEach(f => {
    if (!f.dot) return;
    if (visibleStatus(f)) f.dot.dataset.status = visibleStatus(f); else delete f.dot.dataset.status;
    f.box.checked = f.checked;
    const photo = getPhoto(f.path), names = (photo.edit_steps || []).filter(step => step.origin === 'recipe' && step.on).map(step => step.label);
    if (photo.manual) names.unshift('Finish in Photo editor');
    f.recipeLabel.textContent = names.join(', '); f.recipeLabel.hidden = !names.length;
    f.btn.title = [f.recipeReview, f.error].filter(Boolean).join(' · ');
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
  const attention = state.files.filter(f => ['review', 'failed'].includes(visibleStatus(f))).length;
  u.review.textContent = 'Review ' + attention; u.review.disabled = !attention;
  for (const option of u.filter.options) {
    const labels = { all: 'All', review: 'Needs review', failed: 'Failed', attention: 'Review queue' };
    const count = option.value === 'all' ? total : option.value === 'attention' ? attention : state.files.filter(f => visibleStatus(f) === option.value).length;
    option.textContent = labels[option.value] + ' · ' + count;
  }
}

function tile(f, i) {
  f.btn = el('button', { type: 'button', 'aria-label': 'Open ' + f.name, on: { click: () => { view.last = i; state.reviewQueue = false; go(getPhoto(f.path).manual ? 'edit' : 'photo', { index: i }); } } },
    el('img', { src: thumbUrl(f.thumb, 320), alt: '', loading: 'lazy' }));
  f.box = el('input', { type: 'checkbox', checked: f.checked, 'aria-label': 'Include ' + f.name,
    on: { click: e => {
      const checked = e.target.checked, visible = visibleFiles();
      const a = visible.indexOf(state.files[view.anchor]), b = visible.indexOf(f);
      if (e.shiftKey && a >= 0) visible.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(x => { x.checked = checked; });
      else f.checked = checked;
      view.anchor = i; scheduleProjectSave(); paintAll();
    } } });
  f.dot = el('span', { class: 'dot' });
  f.recipeLabel = el('small', { class: 'recipe-tile-label' });
  f.cell = el('li', { class: 'cell' }, f.btn, f.box, f.dot, f.recipeLabel,
    el('span', { class: 'cell-name' }, f.name), f.burst ? el('span', { class: 'burst-label', title: 'Photos taken within two seconds of the next frame' }, 'Sequence') : null);
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
            lookSelect.value = state.look = name; folderSettingsChanged(); close();
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
  const lookSel = select([['', 'Default']], '', v => { state.look = v; folderSettingsChanged(); }, 'look');
  const genreSel = select([['', 'None']], state.genre, v => { state.genre = v; folderSettingsChanged(); }, 'genre');
  const genreField = field('Kind of photo', 'genre', genreSel); genreField.hidden = true;
  const range = el('input', { type: 'range', id: 'strength', min: 0, max: 2, step: 0.05, value: 0.8 });
  const out = el('span', { class: 'value num' }), reset = el('button', { type: 'button', class: 'link', hidden: true }, 'Reset');
  let dflt = 0.8;
  const paintStrength = () => { out.textContent = Number(range.value).toFixed(2); reset.hidden = state.strength === null; };
  range.addEventListener('input', () => { state.strength = Number(range.value); paintStrength(); folderSettingsChanged(); });
  reset.addEventListener('click', () => { state.strength = null; range.value = dflt; paintStrength(); folderSettingsChanged(); });
  const text = el('textarea', { id: 'runText', rows: 3, value: state.runText, placeholder: 'e.g. darken the sky',
    on: { input: e => { state.runText = e.target.value; folderSettingsChanged(); } } });
  const exportSel = select([['', 'Default']], state.exportPreset, v => { state.exportPreset = v; folderSettingsChanged(); }, 'export');
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
    field('Look', 'look', lookSel, el('button', { type: 'button', class: 'link', on: { click: () => surprise(lookSel) } }, 'Surprise me'),
      el('button', { type: 'button', class: 'link', on: { click: () => openPhotoPresets({
        getOptions: () => ({ look: state.look, intensity: state.strength === null ? dflt : state.strength, sliders: state.folderSliders || {} }),
        folder: true, repaint: paintAll,
      }) } }, 'Recipes')),
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
    setBar(backButton('Workspace', 'workspace'),
      el('span', { class: 'title' }, baseName(state.dir) + ' · ' + state.files.length + (state.files.length === 1 ? ' photo' : ' photos')),
      el('span', { class: 'grow' }),
      findBox,
      el('button', { type: 'button', on: { click: openShare } }, 'Share'),
      el('button', { type: 'button', on: { click: () => go('tools') } }, 'Tools'),
      gearButton());
    const all = el('input', { type: 'checkbox', id: 'all', on: { change: e => { state.files.forEach(f => { f.checked = e.target.checked; }); scheduleProjectSave(); paintAll(); } } });
    const allText = el('span');
    const filter = select([['all', 'All'], ['attention', 'Review queue'], ['review', 'Needs review'], ['failed', 'Failed']], view.filter, v => { view.filter = v; view.page = 0; paintAll(); }, 'show');
    const review = el('button', { type: 'button', class: 'line', on: { click: () => {
      const index = state.files.findIndex(f => ['review', 'failed'].includes(visibleStatus(f)));
      if (index >= 0) { view.filter = 'attention'; view.last = index; state.reviewQueue = true; go('photo', { index }); }
    } } }, 'Review');
    const groups = new Map();
    state.files.filter(f => f.burst).forEach(f => groups.set(f.burst, (groups.get(f.burst) || 0) + 1));
    const burst = select([['', 'All photos'], ...[...groups].map(([key, n], index) => [key, 'Time group ' + (index + 1) + ' · ' + n + ' photos'])],
      view.burst, value => { view.burst = value; view.page = 0; paintAll(); }, 'burst');
    const burstControl = el('div', { class: 'burst-control', hidden: !groups.size }, el('label', { htmlFor: 'burst' }, 'Sequence'), burst);
    const note = el('span', { class: 'grow', role: 'status' });
    const findNote = el('span', { class: 'findnote', role: 'status' });
    const clear = el('button', { type: 'button', class: 'link', hidden: true, on: { click: () => clearFind(findBox) } }, 'Clear');
    const pause = el('button', { type: 'button', hidden: true, on: { click: () => control(state.job.status === 'paused' ? 'resume' : 'pause') } }, 'Pause');
    const cancel = el('button', { type: 'button', hidden: true, on: { click: () => control('cancel') } }, 'Cancel');
    const line = el('div', { class: 'statusline' }, note, pause, cancel);
    const grid = el('ul', { class: 'grid', 'aria-label': 'Photos' });
    const empty = el('p', { class: 'grid-empty', hidden: true }, 'No photos match this view. Choose All to see your folder.');
    const pageText = el('span', { class: 'num', 'aria-live': 'polite' });
    const changePage = delta => { view.page += delta; paintAll(); grid.scrollIntoView({ block: 'start' }); };
    const prev = el('button', { type: 'button', class: 'line', on: { click: () => changePage(-1) } }, 'Previous page');
    const next = el('button', { type: 'button', class: 'line', on: { click: () => changePage(1) } }, 'Next page');
    const pager = el('nav', { class: 'grid-pages', 'aria-label': 'Photo pages' }, prev, pageText, next);
    const { side, run } = sidePanel();
    root.append(el('div', { class: 'folder' },
      el('div', { class: 'main' },
        el('div', { class: 'folder-guide' },
          el('strong', null, 'Start with one photo'),
          el('button', { type: 'button', class: 'line', on: { click: () => { const i = state.files.findIndex(file => file.checked); go('photo', { index: i < 0 ? 0 : i }); } } }, 'Preview a photo')),
        el('div', { class: 'tools' }, el('label', null, all, ' ', allText), el('span', { class: 'grow' }),
          burstControl, el('label', { htmlFor: 'show' }, 'Show '), filter, review, findNote, clear),
        line, grid, empty, pager),
      side));
    view.ui = { all, allText, run, note, line, pause, cancel, findNote, clear, filter, review, grid, empty, pageText, prev, next, pager };
    paintAll();
  },
  focus() {   // back from a photo: the tile it came from; else the first one shown
    const previous = state.files[view.last];
    const shown = visibleFiles(), position = shown.indexOf(previous);
    if (position >= 0 && Math.floor(position / PAGE_SIZE) !== view.page) { view.page = Math.floor(position / PAGE_SIZE); paintAll(); }
    const f = previous?.btn ? previous : state.files.find(x => x.btn);
    if (f) f.btn.focus();
  },
  unmount() { scheduleProjectSave(); view.ui = null; },
  key(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); editPhotos(); return true; }
    return false;
  },
};
