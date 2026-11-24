// The shell: helpers, shared state, hash router, sheets, toast, keys. No framework; text is never parsed as markup.
import { screen as workspace, openFolderDialog, loadFolderPath, folder } from './folder.js';
import { automatic, home } from './modes.js';
import { screen as photo } from './photo.js';
import { openSettings, applySurround } from './settings.js';
import { rememberPhoto } from './project.js';

const KEY = 'autoedit.app.v1';
export const store = {
  get(k, d) { try { const v = JSON.parse(localStorage.getItem(KEY) || '{}')[k]; return v === undefined ? d : v; } catch (_) { return d; } },
  set(k, v) { try { const o = JSON.parse(localStorage.getItem(KEY) || '{}'); o[k] = v; localStorage.setItem(KEY, JSON.stringify(o)); } catch (_) { /* storage blocked */ } },
};

export const state = {
  dir: '', files: [], look: '', genre: '', strength: null, runText: '', exportPreset: '', folderSliders: {},
  finder: store.get('finder', 'auto'), settings: store.get('settings', {}), job: null,
};

// Older settings sheets could store a manually entered key. Keys are session-only now;
// remove any legacy credential fields instead of carrying them into another workspace.
state.settings = Object.fromEntries(Object.entries(state.settings || {}).filter(([key]) => !/key|secret|token|password|credential/i.test(key)));
store.set('settings', state.settings);

export const $ = (sel, root = document) => root.querySelector(sel);

// el('button', {class: 'primary', on: {click: fn}}, 'Text', child). Strings become text nodes.
export function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'on') for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
    else if (k === 'style') Object.assign(n.style, v);
    else if (k === 'data') Object.assign(n.dataset, v);
    else if (k === 'role' || k.startsWith('aria-')) n.setAttribute(k, v);
    else n[k] = v;
  }
  n.append(...kids.flat().filter(c => c !== undefined && c !== null && c !== false));
  return n;
}

export async function api(path, body, { signal } = {}) {
  let r;
  try {
    r = await fetch('/api/v1' + path, body === undefined ? { signal } :
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('The AutoEdit server is not reachable.');
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) {
    const error = new Error(j.error || 'The server answered ' + r.status);
    error.status = r.status; error.code = j.code;
    throw error;
  }
  return j;
}

export const nice = s => { const t = String(s).replaceAll('_', ' ').replace(/^bw\b/, 'B&W'); return t.charAt(0).toUpperCase() + t.slice(1); };
export const thumbUrl = (path, px) => '/api/v1/thumb?px=' + px + '&path=' + encodeURIComponent(path);
export const joinPath = (dir, name) => dir.replace(/\/+$/, '') + '/' + name;
export const baseName = p => p.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || p;

let schema = null;   // fetched once; a failure is not cached
export function getSchema() {
  if (!schema) schema = api('/settings/schema').catch(e => { schema = null; throw e; });
  return schema;
}

/* ---- toast and sheet ------------------------------------------------------------------------- */
let toastTimer = 0;
export function toast(msg) {   // inside the open sheet when there is one: the modal backdrop would hide it
  const t = $('#toast'), host = $('#sheet').open ? $('#sheet') : document.body;
  if (t.parentNode !== host) host.append(t);
  t.textContent = ''; t.classList.add('show');
  setTimeout(() => { t.textContent = msg; }, 50);   // text after the move, so the alert is announced
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 4000);
}

export function closeSheet() { const d = $('#sheet'); if (d.open) d.close(); }
export function openSheet(title, build) {
  const d = $('#sheet'), body = el('div', { class: 'sheet-body' });
  document.body.append($('#toast'));   // out of the dialog before it is rebuilt
  d.replaceChildren(
    el('div', { class: 'sheet-head' }, el('h2', { id: 'sheetTitle' }, title),
      el('button', { type: 'button', 'aria-label': 'Close', on: { click: closeSheet } }, '×')),
    body);
  build(body, closeSheet);
  if (!d.open) d.showModal();
  return body;
}

/* ---- router ---------------------------------------------------------------------------------- */
const screens = {};
let current = null;
export function register(name, s) { screens[name] = s; }

export function setBar(...nodes) { $('#bar').replaceChildren(...nodes.flat().filter(Boolean)); }
export const backButton = (label, target) =>
  el('button', { type: 'button', 'aria-label': 'Back to ' + label, on: { click: () => go(target) } }, '‹ ' + label);
export const gearButton = () =>
  el('button', { type: 'button', title: 'Settings', 'aria-label': 'Settings', on: { click: openSettings } }, '⚙');

export function go(name, params) {
  const h = '#/' + name + (params && params.index !== undefined ? '/' + params.index : '');
  if (location.hash === h) render(); else location.hash = h;
}

function render() {
  const [, name = 'start', idx] = location.hash.split('/');
  let target = screens[name] ? name : 'start';
  if (!['start', 'automatic', 'workspace'].includes(target) && !state.dir) target = 'start';
  if (current && current.unmount) current.unmount();
  const root = $('#view'); root.replaceChildren(); setBar();
  current = screens[target];
  if (target === 'photo') rememberPhoto(idx === undefined ? 0 : Number(idx) || 0);
  current.mount(root, { index: idx === undefined ? 0 : Number(idx) || 0 });
  if (current.focus) current.focus();
  document.title = 'AutoEdit';
}

/* ---- keys ------------------------------------------------------------------------------------ */
function showKeys() {
  openSheet('Keys', body => body.append(el('div', { class: 'keys' },
    ...[['Ctrl/Cmd + O', 'Open a folder'], ['← →', 'Previous or next photo'], ['Enter', 'Open the photo, or send what you typed'],
      ['Esc', 'Back, or cancel a mark'], ['B (hold)', 'Show the original'], ['M', 'Mark an oval'],
      ['Enter on the photo (Mark on)', 'Place an oval: arrows move it, Shift + arrows resize'],
      ['Shift + click a checkbox', 'Select a range of photos'], ['Arrows while cropping', 'Move the crop, or hold Shift to resize it'],
      ['Ctrl/Cmd + S', 'Save this photo'], ['Ctrl/Cmd + Enter', 'Edit the checked photos'], ['?', 'This list']]
      .flatMap(([k, v]) => [el('kbd', null, k), el('span', null, v)]))));
}
const typing = e => /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) && e.target.type !== 'checkbox';

document.addEventListener('keydown', e => {
  if ($('#sheet').open) return;   // a sheet handles Esc itself
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 'o') { e.preventDefault(); if (current === automatic) automatic.openFolder(); else openFolderDialog(); return; }
  if (current && current.key && current.key(e)) return;
  if (typing(e)) { if (e.key === 'Escape') e.target.blur(); return; }
  if (e.key === '?') showKeys();
  else if (e.key === 'Escape') {
    if (location.hash.startsWith('#/photo') || location.hash === '#/tools') go('folder'); else if (location.hash === '#/folder') go('workspace');
    else if (['#/workspace', '#/automatic'].includes(location.hash)) go('start');
  }
});

/* ---- start ----------------------------------------------------------------------------------- */
register('start', home);
register('workspace', workspace);
register('automatic', automatic);
register('folder', folder);
register('photo', photo);
window.addEventListener('hashchange', render);
window.autoedit = { loadFolderPath, go, state };
applySurround();
render();
