// Durable editing workspaces. No image data or account credentials are stored here.
// Imports are circular with main.js; access shared state only from functions/events.
import { api, state, toast } from './main.js';

const PHOTO_KEYS = ['style', 'ai_enabled', 'manual', 'look', 'intensity', 'neutral', 'sliders', 'entries', 'hide', 'hideMethod', 'reference', 'reference_strength', 'base', 'region_settings', 'mask_refinements', 'photo_geometry', 'local_only', 'edit_steps'];
const FOLDER_KEYS = ['look', 'genre', 'strength', 'runText', 'exportPreset', 'finder', 'folderSliders', 'recipes'];
const HISTORY_LIMIT = 30;
let activeDir = '', project = fresh(), timer = 0, saving = Promise.resolve(), revision = 0, lastSaved = '';
let warned = false;
// The server owns the native blob. Only an explicit history move can change its pointer.
let manualMoves = new Map(), confirmedManual = new Map();
const listeners = new Set();
const clone = value => JSON.parse(JSON.stringify(value));
const object = value => value && typeof value === 'object' && !Array.isArray(value);
function fresh() { return { version: 1, folder: {}, photos: {}, files: {}, last_index: 0 }; }
function emit(path, type) { listeners.forEach(fn => fn({ path, type })); }
function photoValues(value) {
  const out = {};
  PHOTO_KEYS.forEach(key => { if (value && value[key] !== undefined) out[key] = clone(value[key]); });
  return out;
}
function safeSettings(settings) {
  // The server validates again. Never put a credential accidentally entered in settings into a project.
  return Object.fromEntries(Object.entries(settings || {}).filter(([key]) => !/key|secret|token|password|credential/i.test(key)));
}

// Planned measurements provide an initial warning. A full export can correct that observation.
export function syncRecipeReview(file, photo = {}) {
  if (!file) return;
  const checks = file.objectChecks || {};
  const missing = new Set();
  const inspect = (regions, expected = []) => {
    for (const [name, ops] of Object.entries(regions || {})) {
      if (Object.values(ops || {}).some(Boolean) && (Object.hasOwn(checks, name) ? !checks[name] : expected.includes(name))) missing.add(name);
    }
  };
  inspect(photo.region_settings);
  for (const step of photo.edit_steps || []) {
    if (step.on && step.amount) inspect(step.regions, step.plan?.missing || []);
  }
  file.recipeReview = missing.size ? ('Object not found: ' + [...missing].join(', ')).slice(0, 2048) : '';
}

export function getPhotoValues(path) { return photoValues(project.photos[path]); }
export function getPhoto(path) { return clone(project.photos[path] || {}); }
export function subscribeProject(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function canUndo(path) { return !!getPhoto(path).history?.length; }
export function canRedo(path) { return !!getPhoto(path).future?.length; }

export function updatePhoto(path, patch, { history = true } = {}) {
  if (!path || !object(patch)) return;
  const previous = getPhoto(path), before = photoValues(previous), after = photoValues(previous);
  PHOTO_KEYS.forEach(key => {
    if (!Object.prototype.hasOwnProperty.call(patch, key)) return;
    if (patch[key] === undefined) delete after[key]; else after[key] = clone(patch[key]);
  });
  if (JSON.stringify(before) === JSON.stringify(after)) return;
  project.photos[path] = { ...after,
    history: history ? [...(previous.history || []), before].slice(-HISTORY_LIMIT) : previous.history || [],
    future: history ? [] : previous.future || [] };
  const file = state.files.find(f => f.path === path); if (file?.exported) file.dirty = true;
  // A new plan has freshly measured its own targets. Prefer that evidence until the next export.
  const beforePlan = before.edit_steps?.find(step => step.plan);
  const afterPlan = after.edit_steps?.find(step => step.plan);
  if (file?.objectChecks && afterPlan &&
      (beforePlan?.id !== afterPlan.id || JSON.stringify(beforePlan.plan) !== JSON.stringify(afterPlan.plan))) {
    for (const name of Object.keys(afterPlan.regions)) delete file.objectChecks[name];
  }
  syncRecipeReview(file, after);
  emit(path, 'change'); scheduleProjectSave();
}

function travel(path, undo) {
  const current = getPhoto(path), from = undo ? 'history' : 'future', to = undo ? 'future' : 'history';
  if (!current[from]?.length) return false;
  const source = current[from].slice(), next = source.pop();
  if ((next.manual?.sha256 || null) !== (current.manual?.sha256 || null)) {
    if (!confirmedManual.has(path)) confirmedManual.set(path, current.manual?.sha256 || null);
    manualMoves.set(path, next.manual?.sha256 || null);
  }
  project.photos[path] = { ...photoValues(next), [from]: source,
    [to]: [...(current[to] || []), photoValues(current)].slice(-HISTORY_LIMIT) };
  const file = state.files.find(f => f.path === path); if (file?.exported) file.dirty = true;
  syncRecipeReview(file, next);
  emit(path, undo ? 'undo' : 'redo'); scheduleProjectSave(); return true;
}
export const undoPhoto = path => travel(path, true);
export const redoPhoto = path => travel(path, false);

export function folderSettingsChanged() {
  state.files.forEach(file => { if (file.exported) file.dirty = true; });
  scheduleProjectSave();
}

export function rememberPhoto(index) {
  if (!activeDir || !Number.isInteger(index) || index < 0 || index >= state.files.length) return;
  project.last_index = index; scheduleProjectSave();
}

function snapshot() {
  const result = clone(project);
  if (state.dir === activeDir) {
    result.folder = Object.fromEntries(FOLDER_KEYS.map(key => [key, state[key]]));
    result.folder.settings = safeSettings(state.settings);
    result.files = Object.fromEntries(state.files.map(f => [f.path, {
      checked: !!f.checked, dirty: !!f.dirty, status: ['finished', 'review', 'failed', 'saved'].includes(f.status) ? f.status : '',
      ...(f.status === 'review' && f.error ? { qualityReason: f.error.slice(0, 2048) } : {}),
      ...(Object.keys(f.objectChecks || {}).length ? { objectChecks: f.objectChecks } : {}),
      ...(f.recipeReview ? { recipeReview: f.recipeReview } : {}),
      ...(f.exported ? { out: f.exported } : {}),
    }]));
    const match = location.hash.match(/^#\/(?:photo|edit)\/(\d+)/);
    if (match) result.last_index = Number(match[1]);
  }
  return result;
}

export function scheduleProjectSave() {
  if (!activeDir || state.dir !== activeDir) return;
  clearTimeout(timer); timer = setTimeout(() => { persistProject(); }, 350);
}

export async function persistProject() {
  clearTimeout(timer);
  if (!activeDir) return true;
  const dir = activeDir, current = snapshot(), encoded = JSON.stringify(current), seq = ++revision;
  // Keep the last workspace snapshot when the caller starts opening another folder. A second
  // flush after state.dir changes must not replace it with the original load-time values.
  Object.assign(project, { folder: current.folder, files: current.files, last_index: current.last_index });
  if (encoded === lastSaved && !manualMoves.size) return saving;
  const moves = new Map(manualMoves), confirmed = confirmedManual;
  // A request is captured before joining the queue: switching folders cannot redirect it.
  saving = saving.catch(() => {}).then(async () => {
    try {
      const expected = Object.fromEntries([...moves].filter(([path, desired]) => confirmed.get(path) !== desired)
        .map(([path]) => [path,confirmed.get(path)]));
      await api('/project', {dir, project:current, ...(Object.keys(expected).length ? {manual_expected:expected} : {})});
      for (const [path, desired] of moves) {
        confirmed.set(path,desired);
        if (confirmed === confirmedManual && manualMoves.get(path) === desired) manualMoves.delete(path);
      }
      if (dir === activeDir && seq === revision) { lastSaved = encoded; warned = false; emit(null, 'saved'); }
      return true;
    } catch (error) {
      emit(null, 'error');
      if (!warned) { warned = true; toast('Your changes are still open, but the project could not be saved: ' + error.message); }
      return false;
    }
  });
  return saving;
}

export async function loadProject(dir, { initialize } = {}) {
  if (await persistProject() === false) throw new Error('Save the current project before opening another folder.');
  const response = await api('/project?dir=' + encodeURIComponent(dir));
  // Keep the old workspace active while disk/network work is pending. Edits made during the
  // read get one final flush before the caller swaps the visible folder and photo list.
  if (await persistProject() === false) throw new Error('Save the current project before opening another folder.');
  const saved = dir === activeDir ? snapshot() : response?.project || fresh();
  if (initialize) initialize();
  project = { ...fresh(), ...saved, photos: object(saved.photos) ? saved.photos : {}, files: object(saved.files) ? saved.files : {} };
  // Bound old history too, so a long-lived project stays cheap to open and save.
  for (const [path, photo] of Object.entries(project.photos)) {
    project.photos[path] = { ...photoValues(photo),
      history: (Array.isArray(photo.history) ? photo.history : []).slice(-HISTORY_LIMIT).map(photoValues),
      future: (Array.isArray(photo.future) ? photo.future : []).slice(-HISTORY_LIMIT).map(photoValues) };
  }
  manualMoves = new Map(); confirmedManual = new Map();
  activeDir = dir; lastSaved = ''; warned = false; revision++;
  const folder = object(project.folder) ? project.folder : {};
  FOLDER_KEYS.forEach(key => { if (folder[key] !== undefined) state[key] = folder[key]; });
  if (object(folder.settings)) state.settings = safeSettings(folder.settings);
  state.files.forEach(f => {
    const savedFile = project.files[f.path];
    if (!object(savedFile)) return;
    if (typeof savedFile.checked === 'boolean') f.checked = savedFile.checked;
    if (['finished', 'review', 'failed', 'saved'].includes(savedFile.status)) f.status = savedFile.status;
    if (object(savedFile.objectChecks)) f.objectChecks = { ...savedFile.objectChecks };
    if (savedFile.qualityReason) f.error = savedFile.qualityReason;
    syncRecipeReview(f, project.photos[f.path]);
    if (typeof savedFile.dirty === 'boolean') f.dirty = savedFile.dirty;
    if (typeof savedFile.out === 'string') f.exported = savedFile.out;
  });
  emit(null, 'load');
  return project;
}

// A final same-origin request also works when the desktop window is closed. Normal edits are
// saved continuously; this is a last-chance flush, not the only persistence mechanism.
window.addEventListener('pagehide', () => {
  if (!activeDir) return;
  const expected = Object.fromEntries([...manualMoves.keys()].map(path => [path,confirmedManual.get(path)]));
  const body = JSON.stringify({dir:activeDir, project:snapshot(), ...(Object.keys(expected).length ? {manual_expected:expected} : {})});
  fetch('/api/v1/project', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
});
window.addEventListener('hashchange', scheduleProjectSave);
