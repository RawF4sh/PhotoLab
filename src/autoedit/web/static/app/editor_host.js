// The upstream editor owns the catalog, edits and history. The host only opens originals,
// forwards explicit actions and receives the files the editor asks to save.
import { setupEditorAssist } from './editor_assist.js';
import { comparePhoto, thumbnail } from './editor_compare.js';

const $ = id => document.getElementById(id);
const frame = $('editor');
let ready = false;
let dirty = false;
let bootWatch;
let toastTimer;
const approvedPaths = new Map();
const photoActions = ['auto', 'ask-gemini', 'mask', 'compare', 'export'];

function lockPhotoActions(locked) {
  for (const id of photoActions) $(id).disabled = locked;
}

async function catalogMatch(key) {
  const record = JSON.parse(localStorage.getItem(key) || 'null');
  if (!record) return null;
  try {
    const photo = await command('photo.inspect', {id:record.id});
    return !photo.deleted && photo.source.path === record.source ? photo : null;
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

async function showPhoto(id) {
  await command('library.select', {ids:[id]});
  await command('view.detail');
  frame.focus();
}

async function api(route, body) {
  const response = await fetch('/api/v1' + route, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

function tell(message) {
  clearTimeout(toastTimer);
  $('status').textContent = message;
  if (message) toastTimer = setTimeout(() => { $('status').textContent = ''; }, 7000);
}

function act(id, action) {
  const button = $(id);
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await action(); }
    catch (error) { tell(error.message || String(error)); }
    finally { button.disabled = false; }
  });
}

async function command(id, params = {}) {
  if (!ready) throw new Error('Wait for the editor to open.');
  return JSON.parse(await frame.contentWindow.lightcraft.command(id, JSON.stringify(params)));
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
  return approvedPaths.get(photo.source.path);
}

async function openPhoto(path, asTiff = false) {
  lockPhotoActions(true);
  $('open').disabled = $('open-tiff').disabled = true;
  if (asTiff) tell('Preparing full-resolution TIFF…');
  try {
    const query = new URLSearchParams({path});
    if (asTiff) query.set('as_tiff', 'true');
    const response = await fetch('/api/v1/editor/original?' + query);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || 'Could not open the selected photo.');
    }
    const bytes = await response.arrayBuffer();
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    const key = 'autoedit.lightcraft.' + Array.from(digest, b => b.toString(16).padStart(2, '0')).join('');
    const name = path.split(/[/\\]/).pop() + (asTiff ? '.tif' : '');
    const size = bytes.byteLength;
    const existing = await catalogMatch(key) || await findOriginal(name, size, digest);
    if (existing) {
      localStorage.setItem(key, JSON.stringify({id:existing.id, source:existing.source.path}));
      approvedPaths.set(existing.source.path, {path, raw:asTiff || existing.kind === 'raw'});
      await showPhoto(existing.id);
      return;
    }

    const previous = (await command('library.state')).selection.active;
    frame.contentWindow.postMessage({type:'lightcraft:open', name, bytes}, location.origin, [bytes]);
    frame.focus();
    // LightCraft imports on its next free frame and selects the newly added photo.
    // Keep edit actions locked while its first-run notice or a large RAW delays import.
    const start = Date.now();
    while (Date.now() - start < 45000) {
      const {selection} = await command('library.state');
      if (selection.active != null && selection.active !== previous) {
        const photo = await command('photo.inspect', {id:selection.active});
        if (photo.file_name === name && photo.file_size === size) {
          localStorage.setItem(key, JSON.stringify({id:photo.id, source:photo.source.path}));
          approvedPaths.set(photo.source.path, {path, raw:asTiff || photo.kind === 'raw'});
          await showPhoto(photo.id);
          return;
        }
      }
      await new Promise(resolve => setTimeout(resolve, 400));
    }
    tell('Select the photo in the library. If an editor dialog is open, close it to finish importing.');
  } finally {
    lockPhotoActions(false);
    $('open').disabled = $('open-tiff').disabled = false;
  }
}

function deliverFile({name, bytes}) {
  if (typeof name !== 'string' || !(bytes instanceof ArrayBuffer)) return;
  const url = URL.createObjectURL(new Blob([bytes]));
  const link = document.createElement('a');
  link.href = url;
  link.download = name.split(/[/\\]/).pop() || 'LightCraft export';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function showBoot(title, message = '', retry = false) {
  frame.hidden = true;
  $('boot').hidden = false;
  $('boot-title').textContent = title;
  $('boot-message').textContent = message;
  $('retry').hidden = !retry;
}

function failed(message) {
  clearInterval(bootWatch);
  showBoot('Photo editor could not open', message, true);
}

async function start() {
  clearInterval(bootWatch);
  showBoot('Opening photo editor…');
  try {
    const state = await api('/editor/status');
    $('native').hidden = !state.native;
    if (!state.ready) {
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
    $('open').disabled = $('open-tiff').disabled = false;
    lockPhotoActions(false);
    const path = sessionStorage.getItem('autoedit.editor.open');
    if (path) {
      try { await openPhoto(path); sessionStorage.removeItem('autoedit.editor.open'); }
      catch (error) { tell(error.message); }
    }
  } else if (data.type === 'lightcraft:dirty') {
    dirty = Boolean(data.dirty);
  } else if (data.type === 'lightcraft:failed') {
    if (ready) tell(data.error); else failed(data.error);
  } else if (data.type === 'lightcraft:save') {
    deliverFile(data);
  }
});

window.addEventListener('beforeunload', event => {
  if (dirty) { event.preventDefault(); event.returnValue = ''; }
});

setupEditorAssist({command, renderThumbnail: async id => {
  const photo = await command('photo.inspect', {id});
  return thumbnail(frame, photo, await command('web.stats'));
}});

act('retry', start);
act('open', async () => {
  const {path} = await api('/pick?kind=file', {});
  if (path) await openPhoto(path);
});
act('open-tiff', async () => {
  const linked = await activeSource();
  const path = linked?.raw ? linked.path : (await api('/pick?kind=file', {})).path;
  if (path) await openPhoto(path, true);
});
act('auto', async () => {
  await active();
  await command('view.detail');
  await command('develop.auto');
  frame.focus();
});
act('mask', async () => {
  await active();
  await command('panel.masking');
  frame.focus();
});
act('compare', async () => {
  const id = await active();
  await comparePhoto(frame, await command('photo.inspect', {id}), await command('web.stats'));
});
act('native-split', async () => {
  $('comparison').close();
  await command('view.detail');
  await command('view.beforeAfterSplit');
  frame.focus();
});
act('export', async () => {
  await active();
  await command('dialog.export');
  frame.focus();
});
act('native', async () => {
  const source = await activeSource();
  await api('/editor/native', source ? {path:source.path} : {});
});
start();
