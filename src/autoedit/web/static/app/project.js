// Durable editing workspaces. No image data or account credentials are stored here.
// Imports are circular with main.js; access shared state only from functions/events.
import { api, state, toast } from './main.js';

const PHOTO_KEYS = ['look', 'intensity', 'neutral', 'sliders', 'entries', 'hide', 'hideMethod', 'reference', 'reference_strength', 'base', 'region_settings', 'mask_refinements', 'photo_geometry', 'local_only'];
const FOLDER_KEYS = ['look', 'genre', 'strength', 'runText', 'exportPreset', 'finder', 'folderSliders'];
const HISTORY_LIMIT = 30;
let activeDir = '', project = fresh(), timer = 0, saving = Promise.resolve(), revision = 0, lastSaved = '';
let warned = false;
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
  emit(path, 'change'); scheduleProjectSave();
}

function travel(path, undo) {
  const current = getPhoto(path), from = undo ? 'history' : 'future', to = undo ? 'future' : 'history';
  if (!current[from]?.length) return false;
  const source = current[from].slice(), next = source.pop();
  project.photos[path] = { ...photoValues(next), [from]: source,
    [to]: [...(current[to] || []), photoValues(current)].slice(-HISTORY_LIMIT) };
  const file = state.files.find(f => f.path === path); if (file?.exported) file.dirty = true;
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
      ...(f.exported ? { out: f.exported } : {}),
    }]));
    const match = location.hash.match(/^#\/photo\/(\d+)/);
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
  if (encoded === lastSaved) return saving;
  // A request is captured before joining the queue: switching folders cannot redirect it.
  saving = saving.catch(() => {}).then(async () => {
    try {
      await api('/project', { dir, project: current });
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
  activeDir = dir; lastSaved = ''; warned = false; revision++;
  const folder = object(project.folder) ? project.folder : {};
  FOLDER_KEYS.forEach(key => { if (folder[key] !== undefined) state[key] = folder[key]; });
  if (object(folder.settings)) state.settings = safeSettings(folder.settings);
  state.files.forEach(f => {
    const savedFile = project.files[f.path];
    if (!object(savedFile)) return;
    if (typeof savedFile.checked === 'boolean') f.checked = savedFile.checked;
    if (['finished', 'review', 'failed', 'saved'].includes(savedFile.status)) f.status = savedFile.status;
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
  const body = JSON.stringify({ dir: activeDir, project: snapshot() });
  fetch('/api/v1/project', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
});
window.addEventListener('hashchange', scheduleProjectSave);
