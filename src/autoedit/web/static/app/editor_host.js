// Native fine-tuning for the current AutoEdit project photo.
import { nativeStage } from './editor_native.js';
import { setupEditorAssist } from './editor_assist.js';
import { comparePhoto, thumbnail } from './editor_compare.js';

const $ = id => document.getElementById(id);
const frame = $('editor');
const embedded = location.pathname === '/editor/panel/' && window.parent !== window;
function notifyHost(type, detail) {
  if (embedded) window.frameElement?.dispatchEvent(new CustomEvent(type, {detail}));
}
function focusCanvas() {
  if (!embedded || !window.frameElement?.inert) frame.focus();
}
if (embedded) document.body.classList.add('embedded-editor');
let ready = false;
let dirty = false;
let bootWatch;
let toastTimer;
let monitor;
let recovery = false;
let downloadAllowed = false;   // set by "Download and open", for this page only
let filter = localStorage.getItem('autoedit.lightcraft.filter') || '';
const outstanding = new Set();
const approvedPaths = new Map();
const photoActions = ['export', 'ask-gemini', 'compare', 'detail'];

function lockPhotoActions(locked) {
  for (const id of photoActions) $(id).disabled = locked;
}

async function catalogMatch(key) {
  const record = JSON.parse(localStorage.getItem(key) || 'null');
  if (!record) return null;
  try {
    const photo = await command('photo.inspect', {id:record.id});
    return !photo.deleted && photo.source.path === record.source ?
      {...photo, managed:true, hostOwned:record.hostOwned === true} : null;
  } catch { return null; }
}

// A photo first imported with LightCraft's own picker has no AutoEdit digest entry.
// Its storing backend identifies originals by content hash in the pinned web release.
async function storedOriginal(storage, hash) {
  if (storage === 'opfs') {
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle('originals');
    const handle = await dir.getFileHandle(hash);
    return (await handle.getFile()).arrayBuffer();
  }
  if (storage === 'idb') {
    return new Promise((resolve, reject) => {
      const open = indexedDB.open('lightcraft');
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result;
        const tx = db.transaction('files', 'readonly');
        tx.oncomplete = tx.onabort = () => db.close();
        const get = tx.objectStore('files').get('originals/' + hash);
        get.onsuccess = () => resolve(get.result);
        get.onerror = () => reject(get.error);
      };
    });
  }
  return null;
}

async function findOriginal(name, size, digest) {
  const {photos} = await command('catalog.query', {filter:{text:name}, limit:100});
  const matches = photos.filter(p => !p.copyOf && p.fileName === name);
  if (!matches.length) return null;
  const {storage} = await command('web.stats');
  for (const candidate of matches) {
    const photo = await command('photo.inspect', {id:candidate.id});
    if (photo.file_size !== size || !photo.content_hash) continue;
    const bytes = await storedOriginal(storage, photo.content_hash).catch(() => null);
    if (!bytes) continue;
    const other = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    if (digest.every((byte, i) => byte === other[i])) return photo;
  }
  return null;
}

function displayFilter(value) {
  filter = value;
  if (value) localStorage.setItem('autoedit.lightcraft.filter', value);
  else localStorage.removeItem('autoedit.lightcraft.filter');
  $('filtered').hidden = !value;
  $('filtered-label').textContent = value === '.tif' ? 'Showing TIFF copies' : 'Showing ' + value;
}

async function focusFilter(value) {
  await command('library.filter', {text:value});
  displayFilter(value);
}

async function showPhoto(id) {
  await command('library.select', {ids:[id]});
  await command('view.detail');
  focusCanvas();
}

async function api(route, body) {
  const response = await fetch('/api/v1' + route, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

function tell(message, keep = false) {
  clearTimeout(toastTimer);
  $('status').textContent = message;
  if (message && !keep) toastTimer = setTimeout(() => { $('status').textContent = ''; }, 7000);
}

function act(id, action) {
  const button = $(id);
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await action(); }
    catch (error) { tell(error.message || String(error)); }
    finally {
      button.disabled = !ready && (photoActions.includes(id) || id === 'open');
    }
  });
}

async function command(id, params = {}) {
  if (!ready) throw new Error('Wait for the editor to open.');
  // A stopped WASM app cannot answer commands queued at the time of a decoder failure.
  let cancel;
  const stopped = new Promise((_resolve, reject) => { cancel = reject; outstanding.add(reject); });
  try {
    return JSON.parse(await Promise.race([frame.contentWindow.lightcraft.command(id, JSON.stringify(params)), stopped]));
  } finally { outstanding.delete(cancel); }
}

async function active() {
  const library = await command('library.state');
  if (library.selection.active == null) throw new Error('Select a photo in the library first.');
  return library.selection.active;
}

async function activeSource() {
  if (!ready) return null;
  const {selection} = await command('library.state');
  if (selection.active == null) return null;
  const photo = await command('photo.inspect', {id:selection.active});
  return {photo, link:approvedPaths.get(photo.source.path)};
}

async function importPicture(source) {
  const {name, path} = source;
  const response = await fetch(source.url);
  if (!response.ok) throw new Error('Could not read the working picture. Reopen the original.');
  const bytes = await response.arrayBuffer();
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  const key = 'autoedit.lightcraft.' + Array.from(hash, b => b.toString(16).padStart(2, '0')).join('');
  const size = bytes.byteLength;
  const existing = await catalogMatch(key) || await findOriginal(name, size, hash);
  if (existing) {
    await showPhoto(existing.id);
    approvedPaths.set(existing.source.path, {path});
    return existing;
  }
  const previous = (await command('library.state')).selection.active;
  if (filter || source.working_copy) await focusFilter(name);
  frame.contentWindow.postMessage({type:'lightcraft:open', name, bytes}, location.origin, [bytes]);
  focusCanvas();
  const start = Date.now();
  while (Date.now() - start < 90000) {
    const {selection} = await command('library.state');
    if (selection.active != null && selection.active !== previous) {
      const photo = await command('photo.inspect', {id:selection.active});
      if (photo.file_name === name && photo.file_size === size) {
        photo.hostOwned = !!source.working_copy;
        photo.managed = true;
        localStorage.setItem(key, JSON.stringify({id:photo.id, source:photo.source.path, hostOwned:photo.hostOwned}));
        approvedPaths.set(photo.source.path, {path});
        await showPhoto(photo.id);
        return photo;
      }
    }
    await new Promise(resolve => setTimeout(resolve, 140));
  }
  if (previous != null) await showPhoto(previous);
  throw new Error('The editor could not open the picture. Your previous photo is still selected.');
}

async function removePicture(photo) {
  if (!photo.hostOwned || !photo.content_hash) return;
  await command('photo.delete', {ids:[photo.id]});
  await command('photo.deletePermanently', {ids:[photo.id]});
  const hash = photo.content_hash;
  // Only delete stored pixels after checking the entire catalog for another user of that hash.
  const catalog = await command('catalog.query', {limit:10000});
  if ((catalog.total ?? catalog.photos.length) >= 10000) return;
  for (const row of catalog.photos) {
    const other = await command('photo.inspect', {id:row.id});
    if (other.content_hash === hash) return;
  }
  const {storage} = await command('web.stats');
  if (storage === 'opfs') {
    const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('originals');
    await dir.removeEntry(hash).catch(() => {});
  } else if (storage === 'idb') {
    await new Promise((resolve, reject) => {
      const open = indexedDB.open('lightcraft');
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result, tx = db.transaction('files','readwrite');
        tx.objectStore('files').delete('originals/' + hash);
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
      };
    });
  }
}

const stage = nativeStage({command,api,tell,importPicture,removePicture,
  onSave:(path,manual) => notifyHost('manual-saved', {path,manual})});
if (embedded) {
  window.autoeditManual = {
    open:openPhoto, flush:() => stage.flush(),
    async exportPhoto() {
      await stage.flush();
      if (!stage.current?.manual && !stage.current?.useAI) {
        throw Error('Make an adjustment or run AI Edit before exporting.');
      }
      return stage.exportPhoto();
    },
    get busy() { return stage.busy; },
  };
}
function localOptions() {
  try { return JSON.parse(localStorage.getItem('autoedit.app.v1') || '{}').settings || {}; }
  catch { return {}; }
}
async function openPhoto(path, options = localOptions(), useAI = false) {
  options = Object.fromEntries(Object.entries(options).filter(([key]) =>
    !/password|credential|api.?key|token|secret/i.test(key)));
  lockPhotoActions(true);
  $('open').disabled = true;
  tell(/\.(?:cr[23]|nef|arw|raf|dng|rw2|orf|pef|srw)$/i.test(path) ? 'Opening RAW…' : 'Opening photo…', true);
  try {
    const opened = await stage.open(path, options, useAI);
    $('filename').textContent = path.split(/[/\\]/).pop();
    if (opened?.lost) tell('The saved manual edit for this photo could not be read and was removed. Fine-tune continues from what this browser still has.', true);
    else tell('');
    return opened;
  } finally {
    $('open').disabled = !ready;
    lockPhotoActions(!ready || !stage.current);
  }
}

function showBoot(title, message = '', retry = false) {
  frame.hidden = true;
  $('filtered').hidden = true;
  $('boot').hidden = false;
  $('boot-title').textContent = title;
  $('boot-message').textContent = message;
  $('retry').hidden = !retry;
  $('install').hidden = true;
  notifyHost(retry ? 'manual-attention' : 'manual-loading');
}

function failed(message) {
  clearInterval(bootWatch);
  showBoot('Photo editor could not open', message, true);
}

function watchEditor() {
  let checking = false;
  clearInterval(monitor);
  monitor = setInterval(async () => {
    const message = frame.contentDocument?.getElementById('lightcraft_blocking')?.textContent || '';
    if (ready && (!embedded || !window.frameElement?.inert)) stage.tick();
    if (message.startsWith('LightCraft stopped')) {
      clearInterval(monitor);
      ready = false;
      dirty = false;
      recovery = true;
      for (const cancel of outstanding) cancel(new Error('The photo editor stopped.'));
      lockPhotoActions(true);
      $('open').disabled = true;
      $('retry').textContent = 'Recover photo editor';
      showBoot('Photo editor stopped', 'Recover to open the library with only TIFF copies visible. Your saved edits are kept.', true);
    } else if (filter && !checking) {
      checking = true;
      try {
        const library = await command('library.state');
        if (library.filter.text !== filter) displayFilter('');
      } catch { /* The next tick checks whether the editor stopped. */ }
      finally { checking = false; }
    }
  }, 1000);
}

async function start() {
  clearInterval(bootWatch);
  clearInterval(monitor);
  showBoot('Opening photo editor…');
  try {
    const state = await api('/editor/status');
    $('native').hidden = !state.native;
    if (!state.ready) {
      if (!downloadAllowed) {   // like the SAM 3 weights: a download never starts by itself
        const size = state.download_mb ? ' (about ' + state.download_mb + ' MB)' : '';   // absent until the app restarts
        showBoot('Download the photo editor?', 'Photo editor is LightCraft ' + state.version +
          ', a separate open-source editor. AutoEdit downloads it once from its GitHub releases' + size +
          ', checks it against the published checksum and keeps it on this computer.');
        $('install').hidden = false;
        notifyHost('manual-attention');
        return;
      }
      showBoot('Installing photo editor…', 'Downloading LightCraft ' + state.version + ' for local use.');
      await api('/editor/install', {});
    }
    // eframe needs a nonzero canvas size while it is starting.
    $('boot').hidden = true;
    frame.hidden = false;
    frame.src = '/editor/release/index.html?host=parent';
    const since = Date.now();
    bootWatch = setInterval(() => {
      const text = frame.contentDocument?.getElementById('lightcraft_loading')?.textContent || '';
      if (/fail|error|unavailable/i.test(text)) failed(text);
      else if (Date.now() - since > 90000) failed('The editor took too long to start. Check that WebGL 2 is enabled in this browser, then try again.');
    }, 1000);
  } catch (error) { failed(error.message); }
}

window.addEventListener('message', async ({source, origin, data}) => {
  if (source !== frame.contentWindow || origin !== location.origin || !data) return;
  if (data.type === 'lightcraft:ready') {
    clearInterval(bootWatch);
    ready = true;
    watchEditor();
    if (recovery) {
      try { await focusFilter(filter || '.tif'); } catch (error) { tell(error.message || String(error)); }
      recovery = false;
      $('retry').textContent = 'Try again';
    } else if (filter) {
      try {
        const library = await command('library.state');
        displayFilter(library.filter.text === filter ? filter : '');
      } catch (error) { tell(error.message || String(error)); }
    }
    $('open').disabled = false;
    lockPhotoActions(true);
    if (embedded) {
      notifyHost('manual-ready');
      return;
    }
    const handed = sessionStorage.getItem('autoedit.editor.open');
    const path = handed || sessionStorage.getItem('autoedit.finetune.source');
    if (path) {
      try {
        const options = handed ? JSON.parse(sessionStorage.getItem('autoedit.editor.options') || 'null') :
          JSON.parse(sessionStorage.getItem('autoedit.finetune.stage') || 'null');
        await openPhoto(path, handed ? options || localOptions() : options?.options || localOptions(),
          handed ? !!options : !!options?.useAI);
        sessionStorage.removeItem('autoedit.editor.open');
        sessionStorage.removeItem('autoedit.editor.options');
      } catch (error) { tell(error.message, true); }
    }
  } else if (data.type === 'lightcraft:dirty') {
    dirty = Boolean(data.dirty);
  } else if (data.type === 'lightcraft:failed') {
    if (ready) tell(data.error); else failed(data.error);
  } else if (data.type === 'lightcraft:save') {
    if (!stage.receive(data)) tell('Use Export above the photo to save the picture and its AutoEdit sidecar.', true);
  }
});

window.addEventListener('beforeunload', event => {
  if (dirty || stage.busy) { event.preventDefault(); event.returnValue = ''; }
});

setupEditorAssist({command, onApplied: () => {
  tell('Gemini edit applied. Adjust the sliders, compare with the original, or undo.');
  focusCanvas();
}, renderThumbnail: async photo => thumbnail(frame, photo, await command('web.stats'))});

act('retry', start);
act('install', () => { downloadAllowed = true; return start(); });
act('open', async () => {
  const {path} = await api('/pick?kind=file', {});
  if (path) await openPhoto(path);
});
act('detail', async () => {
  tell('Opening full-resolution detail…', true);
  await stage.inspectDetail();
  tell('Full resolution · 100%');
  focusCanvas();
});
act('compare', async () => {
  const source = await activeSource();
  if (!source) return;
  const stats = await command('web.stats');
  if (source.photo.id === stage.current?.id) {
    await stage.compareOriginal(original => comparePhoto(frame, source.photo, stats, original));
  } else await comparePhoto(frame, source.photo, stats);
});
act('native-split', async () => {
  $('comparison').close();
  await command('view.detail');
  await command('view.beforeAfterSplit');
  focusCanvas();
});
act('export', async () => {
  tell('Preparing full-size export…', true);
  const result = await stage.exportPhoto();
  if (result) tell('Saved ' + result.image_out, true);
});
act('all-photos', () => focusFilter(''));
act('native', async () => {
  const source = await activeSource();
  await api('/editor/native', source?.link ? {path:source.link.path} : {});
});
document.querySelector('a.tools').addEventListener('click', async event => {
  if (!ready || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  // An unsupported file can stall LightCraft. Guided must still open in that case.
  if (stage.busy) return;
  const link = event.currentTarget.href;
  try { await stage.flush(); } catch (error) { tell(error.message,true); return; }
  try {
    const source = await Promise.race([activeSource().catch(() => null), new Promise(resolve => setTimeout(resolve, 600))]);
    if (source?.link) sessionStorage.setItem('autoedit.guided.open', source.link.path);
  } catch (_) { /* The workspace also has its own file picker. */ }
  location.assign(link);
});
start();
