// Photo screen: preview, hold to compare, marks, chips, Masks, How it was done, Try it yourself, Save this photo.
// Ported from the Lab (lab.html, F38); request bodies are the Lab's. Text is never parsed as markup.
import { el, api, state, store, nice, thumbUrl, loadPhotoPath, go, toast, getSchema, setBar, backButton, gearButton, openSheet } from './main.js';
import { createEditorSidebar } from './editor_sidebar.js';
import { runOptions } from './settings.js';
import { runTool } from './tools.js';
import { exportsDir } from './share.js';
import { getPhoto, updatePhoto, undoPhoto, redoPhoto, canUndo, canRedo, subscribeProject, persistProject } from './project.js';
import { createPhotoTools } from './photo_tools.js';
import { openPhotoPresets } from './presets.js';

const MAX_TEXT = 2000, MAX_MARKS = 20, MIN_SIDE = 0.01;
const DATA = /^data:image\/(webp|jpeg|png);base64,[A-Za-z0-9+/=]+$/, PNG = /^data:image\/png;base64,[A-Za-z0-9+/=]+$/;
const SAY = 'Say what to change, e.g. warm the grass a little';
const SAY_OVAL = 'What should change in the oval? e.g. brighten this a little';
const STATUS_WORD = { applied: 'applied', clamped: 'held back in part', blocked: 'held back' };

let v = null;                       // the open screen, null when away
const previewSession = crypto.randomUUID();
let previewRevision = 0;             // one tab's later photo invalidates queued earlier work

/* ---- small helpers --------------------------------------------------------------------------- */
const isMark = e => e !== null && typeof e === 'object';
export const textOf = l => l.filter(e => !isMark(e)).map(s => s.trim().replace(/[.\s]+$/, '')).filter(Boolean).join('. ');
export const marksOf = l => l.filter(isMark).map(m => ({ box: m.box, kind: m.kind, edit: m.edit }));
const pic = s => (typeof s === 'string' && DATA.test(s) ? s : '');   // '' keeps the old picture
const norm = s => String(s).trim().toLowerCase().replace(/\s+/g, ' ');
const isTyping = t => !!t && !!t.matches && t.matches('input, select, textarea, [contenteditable]') && t.type !== 'checkbox';
const button = (text, on, attrs) => el('button', { type: 'button', ...attrs, on: { click: on } }, text);

function fmtVal(op, x) {
  if (typeof x !== 'number' || !isFinite(x)) return x == null ? '' : String(x);
  if (['exposure','shadows_ev','burn_ev'].includes(op)) return (x < 0 ? '-' : '+') + Math.abs(x).toFixed(2) + ' EV';
  return (x < 0 ? '-' : '+') + Math.abs(x).toFixed(1).replace(/\.0$/, '');
}
const pct = x => { const n = (+x || 0) * 100; return (n < 10 && n !== 0 ? n.toFixed(1).replace(/\.0$/, '') : Math.round(n)) + ' %'; };
const MARK_NAME = /mark:(thing|area):(?:\d(?:\.\d+)?,){3}\d(?:\.\d+)?(?::([^/@]*))?(?:\/([^@]*))?(?:@(.*))?$/;
function niceName(n) {
  const s = String(n), m = MARK_NAME.exec(s); if (!m) return s;
  const bits = ['marked ' + (m[1] === 'thing' && m[2] ? m[2] : 'area')];
  if (m[3]) bits.push(m[3] === 'guarded' ? 'protected part' : m[3]);
  if (m[4]) bits.push(m[4]);
  return bits.join(' · ');
}
const toolText = t => typeof t === 'string' ? t : ((t.name || t.op || '') + ' ' + fmtVal(t.op, t.value)).trim();
const whereName = w => w === 'global' ? 'Whole photo' : String(w).startsWith('instruction:') ? 'the ' + String(w).slice(12) : niceName(w);

/* ---- this photo's sentences and marks (the Lab's entry format) ------------------------------- */
const okMark = x => isMark(x) && Array.isArray(x.box) && x.box.length === 4
  && x.box.every(n => typeof n === 'number' && isFinite(n) && n >= 0 && n <= 1)
  && x.box[2] - x.box[0] >= MIN_SIDE && x.box[3] - x.box[1] >= MIN_SIDE && (x.kind === 'auto' || x.kind === 'area')
  && typeof x.edit === 'string' && x.edit.trim() !== '' && x.edit.length <= 300;
export function loadEntries(path) {   // only what the server would accept: 20 marks, 2000 characters of text
  const photo = getPhoto(path), legacy = store.get('entries', {})[path];
  const l = photo.entries === undefined ? legacy : photo.entries; if (!Array.isArray(l)) return [];
  const ok = []; let marks = 0;
  for (const e of l) {
    const s = typeof e === 'string' && e.trim() !== '' && e.length <= MAX_TEXT;
    if (!s && !okMark(e)) continue;
    const c = s ? e : { box: e.box, kind: e.kind, edit: e.edit };
    if (s ? textOf([...ok, c]).length > MAX_TEXT : ++marks > MAX_MARKS) continue;
    ok.push(c);
  }
  if (photo.entries === undefined && ok.length) updatePhoto(path, { entries: ok }, { history: false });
  return ok;
}
function saveEntries() { updatePhoto(v.file.path, { entries: v.list }); }

/* ---- the request ----------------------------------------------------------------------------- */
function options() {   // the Folder's merge, then this photo's own look and strength
  const o = runOptions(), p = getPhoto(v.file.path);
  if (p.style) o.style = p.style;
  if (v.primary) Object.assign(o, {local_only: true, decider: 'rules', allow_cloud: false, translator: 'off'});
  if (p.look !== undefined) { if (p.look) o.look = p.look; else delete o.look; }
  if (p.intensity !== undefined) o.intensity = p.intensity;
  if (p.reference !== undefined) { if (p.reference) o.reference = p.reference; else delete o.reference; }
  if (p.reference_strength !== undefined) o.reference_strength = p.reference_strength;
  if (p.local_only) { o.local_only = true; o.decider = 'rules'; o.allow_cloud = false; }
  if (p.neutral) o.neutral = p.neutral;   // F34: X,Y as shares of the frame
  Object.assign(o, hideRequest(v.file.path) || {});
  return o;
}
// F22: what the Hide control asked for on one photo: the whole list (the Settings kinds and this photo's) and the method.
// null when nothing was asked on this photo, so the run's own options apply. The Folder's batch sends it too.
export function hideRequest(path) {
  const p = getPhoto(path); if (!(p.hide || []).length && !p.hideMethod) return null;
  const r = {}, kinds = [...new Set([...settingKinds(runOptions()), ...(p.hide || [])])];
  if (kinds.length) r.hide = kinds;
  if (p.hideMethod) r.hide_method = p.hideMethod;
  return r;
}
const settingKinds = o => String(Array.isArray(o.hide) ? o.hide.join(',') : o.hide || '').split(/[,\s]+/).filter(Boolean);
function previewBody() {
  const b = { file: v.file.path, ...options() }, t = textOf(v.list), m = marksOf(v.list);
  if (t) b.photo_edit = t;
  if (m.length) b.photo_marks = m;
  const own = getPhoto(v.file.path), sliders = v.draftSliders || (own.sliders === undefined ? state.folderSliders : own.sliders);
  for (const key of ['region_settings', 'mask_refinements', 'photo_geometry', 'edit_steps']) if (own[key] !== undefined) b[key] = own[key];
  if (own.base) b.photo_base = own.base;
  if (sliders && Object.keys(sliders).length) b.photo_settings = { ...sliders };
  return b;
}
const BODIES = {
  edit: () => ({ ...previewBody(), ...(v.unframed ? { preview_unframed: true } : {}) }),
  how: () => ({ ...previewBody(), steps: true }),
  try: () => v.primary && v.practice ? practiceRequest() : ({ ...previewBody(), try: { sliders: { ...v.sliders }, compare: v.compare } }),
};
const SHOW = {
  edit: j => { v.answer = j; if (v.view === 'edit') paintEdit(); },
  how: j => { v.how = j; if (v.view === 'how') paintHow(); paintMasks(); },
  try: j => { v.tried = j; if (v.view === 'try') paintTry(); },
};
async function once(kind) {   // cancel obsolete client work; never paint a response for an old edit/photo
  if (!v || !v.editing) return;
  const me = v, f = me.flights[kind] ||= {}, body = BODIES[kind](), key = JSON.stringify(body);
  if ((key === f.shown && key === f.painted && !f.busy) || (f.busy && key === f.key)) return;
  if (f.controller) f.controller.abort();
  const controller = new AbortController();
  f.controller = controller; f.key = key; f.busy = true; setBusy(1);
  const current = () => v === me && f.controller === controller && !controller.signal.aborted && JSON.stringify(BODIES[kind]()) === key;
  try {
    // A cloud edit gets one render: the quick pass must not double its model calls or cost.
    const edges = kind === 'edit' && body.decider !== 'llm' ? [640, 2560] : [2560];
    for (const edge of edges) {
      const j = await api('/preview', { ...body, preview_edge: edge, preview_session: previewSession + '_' + kind, preview_revision: ++previewRevision }, { signal: controller.signal });
      if (!current()) return;
      SHOW[kind](j); f.painted = key;
      if (kind === 'edit') manualControls(j.editor && j.editor.sliders);
      if (edge === 640 && v.view === kind) say('Quick preview ready · refining detail…');
    }
    f.shown = key;
  } catch (e) { if (e.name !== 'AbortError' && current()) say(e.message, 'bad'); }
  finally {
    if (f.controller === controller) f.busy = false;
    if (v === me) setBusy(-1);
  }
}
function refresh() { if (!v?.editing) return; once(v.view); if (v.masks && v.view !== 'how') once('how'); }
function schedulePreview() { clearTimeout(v.previewTimer); v.previewTimer = setTimeout(refresh, 160); }
const curAnswer = () => v[{ edit: 'answer', how: 'how', try: 'tried' }[v.view]];

/* ---- status and busy ------------------------------------------------------------------------- */
function say(text, kind) { v.u.status.className = 'pv-status' + (kind ? ' ' + kind : ''); v.u.status.textContent = text || ''; }
function setBusy(d) {
  const was = v.busy > 0; v.busy = Math.max(0, v.busy + d);
  v.u.busy.hidden = v.busy === 0;
  if (v.busy > 0 && !was) {
    say('Working…'); v.busyFrom = Date.now();
    v.u.secs.textContent = ''; v.u.status.append(v.u.secs);   // aria-hidden: a ticking counter is not announced
    v.busyTimer = setInterval(() => {
      const s = Math.floor((Date.now() - v.busyFrom) / 1000);
      if (v.u.secs.isConnected) v.u.secs.textContent = s >= 3 ? ' ' + s + ' s' : '';
    }, 500);
  }
  if (v.busy === 0) {
    clearInterval(v.busyTimer);
    if (v.u.status.textContent.startsWith('Working…')) { const j = curAnswer(); if (j) metaLine(j); else say(''); }
  }
}
function metaLine(j) {
  const r = j.result || {}, rc = (r.audit || {}).run_config || {};
  const finder = rc.backend === 'sam3' ? 'SAM 3' : rc.backend || state.finder;
  const n = (j.instruction_problems || []).length + (j.marks || []).reduce((a, m) => a + ((m.problems || []).length ? 1 : 0), 0);
  const u = v.u; u.status.className = 'pv-status'; u.status.replaceChildren(...[   // replaceChildren writes null as text
    (r.scene || 'photo') + ' · regions by ' + finder,
    r.needs_review ? el('span', { class: 'warn' }, ' · worth a look: ' + (r.review_reason || 'see the report')) : null,
    n ? el('span', { class: 'warn' }, ' · ' + n + ' not understood') : null].filter(Boolean));
  v.scene = r.scene || ''; title();
}
function title() {
  v.u.title.textContent = [v.file.name.replace(/\.[^.]+$/, ''), v.scene, (v.index + 1) + ' of ' + state.files.length].filter(Boolean).join(' · ');
}

/* ---- the frame: fit, compare, ovals, tints --------------------------------------------------- */
function fit() {
  if (!v) return;
  const { after, canvas, frame } = v.u, w = after.naturalWidth, h = after.naturalHeight; if (!w || !h) return;
  const s = v.zoom ? 1 : Math.min((canvas.clientWidth - 32) / w, (canvas.clientHeight - 32) / h);
  canvas.classList.toggle('zoomed', v.zoom);
  frame.style.width = Math.max(1, Math.floor(w * s)) + 'px'; frame.style.height = Math.max(1, Math.floor(h * s)) + 'px';
}
function setImg(img, s) { const p = pic(s); if (p) img.src = p; }
function setThumbs() {
  const me = v, {before, after, opening} = me.u;
  if (!me.primary || !me.file.isRaw) {
    const t = thumbUrl(me.file.path,1600); before.src = t; after.src = t; return;
  }
  const decode = Object.fromEntries(Object.entries(runOptions()).filter(([key]) => key.startsWith('raw_')));
  const key = JSON.stringify(decode);
  if (me.originalKey === key) {
    if (me.original) before.src = after.src = me.original;
    return;
  }
  me.originalAbort?.abort();
  const controller = me.originalAbort = new AbortController();
  me.originalKey = key; me.original = null; opening.hidden = false; opening.textContent = 'Opening RAW…';
  api('/editor/source-preview', {path:me.file.path,options:decode}, {signal:controller.signal}).then(result => {
    if (v !== me || controller.signal.aborted) return;
    if (!pic(result.before)) throw Error('The source decoder did not return an image.');
    me.original = result.before; opening.hidden = true;
    if (result.working_copy) me.u.title.title = me.file.name + ' · decoded working preview' +
      (result.source_bits ? ' · ' + result.source_bits + '-bit source' : '');
    if (!me.editing || !curAnswer()) before.src = after.src = me.original;
  }).catch(error => {
    if (v !== me || controller.signal.aborted || (me.editing && curAnswer())) return;
    opening.hidden = false; opening.textContent = error.message;
  });
}
function showing() {
  const engine = v.view === 'try' && v.compare && v.which === 'engine' && v.tried && v.tried.compare;
  const s = !v.editing || v.hold ? 'before' : engine ? 'engine' : 'after', u = v.u;
  const split = v.split && s === 'after' && !!curAnswer();
  u.frame.dataset.showing = s;
  u.frame.classList.toggle('split', split);
  u.frame.style.setProperty('--split', v.splitAt + '%');
  u.splitControl.hidden = !split;
  u.splitControl.setAttribute('aria-valuenow', v.splitAt);
  u.splitControl.setAttribute('aria-valuetext', v.splitAt + '% before, ' + (100 - v.splitAt) + '% after');
  u.split.setAttribute('aria-pressed', String(v.split));
  u.split.disabled = !curAnswer() || !!engine;
  if (v.primary) {
    u.split.hidden = !curAnswer();
    u.splitBefore.textContent = v.view === 'how' ? 'Before this step' : 'Original';
    u.splitAfter.textContent = v.view === 'how' ? 'After this step' : v.view === 'try' ? 'Practice edit' : 'Current edit';
  }
  u.tag.hidden = !v.editing || s === 'after';
  u.tag.textContent = s === 'before' ? (v.view === 'how' ? 'Before this step' : 'Original') : "AutoEdit's edit";
  u.orig.setAttribute('aria-pressed', String(v.hold));
}
function setHold(on) { if (!v?.editing || v.hold === on) return; v.hold = on; showing(); }
function tint(list, color) {
  const t = v.u.tint; t.replaceChildren();
  (list || []).forEach(o => {
    const a = o && o.alpha; if (typeof a !== 'string' || !PNG.test(a)) return;
    const d = el('div', { class: 'tl' }), u = 'url("' + a + '")';
    d.style.backgroundColor = color; d.style.maskImage = u; d.style.webkitMaskImage = u; t.append(d);
  });
}
function paintMasks() { const masks = v.unframed ? v.answer : v.how; if (v.masks && masks) tint(masks.mask_overlays, 'var(--accent)'); else tint([]); }
function placeOval(n, box) {
  n.style.left = box[0] * 100 + '%'; n.style.top = box[1] * 100 + '%';
  n.style.width = (box[2] - box[0]) * 100 + '%'; n.style.height = (box[3] - box[1]) * 100 + '%';
}
function ghost(box) {
  if (Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)) { placeOval(v.u.ghost, box); v.u.ghost.hidden = false; }
  else v.u.ghost.hidden = true;
}
function unghost() { v.u.ghost.hidden = true; paintMasks(); }
const hover = (node, on) => {
  node.addEventListener('pointerenter', on); node.addEventListener('focusin', on);
  node.addEventListener('pointerleave', unghost); node.addEventListener('focusout', unghost);
};

/* ---- marking --------------------------------------------------------------------------------- */
function clearPending() {
  v.pending = null; v.u.pend.hidden = true; v.u.markTag.hidden = true; v.u.say.placeholder = SAY;
}
function setPending(box, stay) {   // stay: the keyboard is still moving the oval, so the focus stays on the photo
  v.pending = box; placeOval(v.u.pend, box); v.u.pend.hidden = false; v.u.markTag.hidden = false;
  v.u.say.placeholder = SAY_OVAL; if (!stay) v.u.say.focus();
}
function setMarking(on) {
  if (v.tools && (on || v.tools.mode === 'mark')) v.tools.setMode(on ? 'mark' : 'none');
  v.marking = on; v.u.mark.setAttribute('aria-pressed', String(on));
  if (on && v.picking) setPicking(false);
  v.u.frame.classList.toggle('marking', v.marking || v.picking);
  if (!on) clearPending();
}
/* ---- the grey point (F34) -------------------------------------------------------------------- */
function paintNeutral() {
  const p = getPhoto(v.file.path).neutral, u = v.u;
  u.greyChip.hidden = !p; u.greyDot.hidden = !p || !v.unframed || (v.tools && !v.tools.ready);
  if (p) { u.greyDot.style.left = p[0] * 100 + '%'; u.greyDot.style.top = p[1] * 100 + '%'; }
}
function setNeutral(p) {
  const base = getPhoto(v.file.path).base;
  updatePhoto(v.file.path, { neutral: p || undefined, ...(p && base ? { base: { ...base, wb: null, wb_bias: [0, 0] } } : {}) });
}
function setPicking(on) {
  if (v.tools && (on || v.tools.mode === 'neutral')) v.tools.setMode(on ? 'neutral' : 'none');
  v.picking = on; v.u.grey.setAttribute('aria-pressed', String(on));
  if (on && v.marking) setMarking(false);
  v.u.frame.classList.toggle('marking', v.marking || v.picking);
}
function wireFrame() {   // drag draws an oval while marking; otherwise holding the photo shows the original
  const f = v.u.frame, handle = v.u.splitControl; let a = null, pan = null, splitPointer = null;
  const pt = e => {
    const r = f.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return null;
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const boxOf = (p, q) => [Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.max(p[0], q[0]), Math.max(p[1], q[1])];
  const moveSplit = e => { const p = pt(e); if (p) { v.splitAt = Math.round(p[0] * 100); showing(); } };
  handle.addEventListener('keydown', e => {
    const step = e.shiftKey ? 10 : 1;
    const value = { ArrowLeft: v.splitAt - step, ArrowRight: v.splitAt + step,
      ArrowDown: v.splitAt - step, ArrowUp: v.splitAt + step, PageDown: v.splitAt - 10,
      PageUp: v.splitAt + 10, Home: 0, End: 100 }[e.key];
    if (value === undefined) return;
    e.preventDefault(); e.stopPropagation(); v.splitAt = Math.max(0, Math.min(100, value)); showing();
  });
  f.addEventListener('pointerdown', e => {
    if (e.button !== 0 || e.isPrimary === false) return;
    if (!handle.hidden && (!v.zoom || handle.contains(e.target))) {
      splitPointer = e.pointerId; f.setPointerCapture(e.pointerId); handle.focus({ preventScroll: true });
      e.preventDefault(); moveSplit(e); return;
    }
    if ((v.marking || v.picking) && v.tools && !v.tools.ready) { e.preventDefault(); say('Opening the uncropped photo. Place your mark when it is ready.'); return; }
    if (v.picking) {
      const p = pt(e); if (p) { e.preventDefault(); setNeutral(p.map(n => Math.round(n * 1e4) / 1e4)); setPicking(false); }
      return;
    }
    if (!v.marking) {
      if (v.zoom) {
        pan = { x: e.clientX, y: e.clientY, left: v.u.canvas.scrollLeft, top: v.u.canvas.scrollTop };
        f.setPointerCapture(e.pointerId); e.preventDefault();
      } else if (!v.split) setHold(true);
      return;
    }
    const p = pt(e); if (!p) return;
    e.preventDefault(); clearPending(); a = p;
    try { f.setPointerCapture(e.pointerId); } catch (_) { /* a synthetic pointer */ }
  });
  f.addEventListener('pointermove', e => {
    if (splitPointer === e.pointerId) { moveSplit(e); return; }
    if (pan) { v.u.canvas.scrollLeft = pan.left + pan.x - e.clientX; v.u.canvas.scrollTop = pan.top + pan.y - e.clientY; return; }
    if (!a) return; const q = pt(e); if (!q) return;
    placeOval(v.u.pend, boxOf(a, q)); v.u.pend.hidden = false;
  });
  f.addEventListener('pointerup', e => {
    if (splitPointer === e.pointerId) { moveSplit(e); splitPointer = null; return; }
    pan = null; setHold(false); if (!a) return;
    const q = pt(e) || a, box = boxOf(a, q).map(n => Math.round(n * 1e4) / 1e4); a = null;
    if (box[2] - box[0] < MIN_SIDE || box[3] - box[1] < MIN_SIDE) clearPending(); else setPending(box);
  });
  f.addEventListener('lostpointercapture', e => { if (splitPointer === e.pointerId) splitPointer = null; });
  f.addEventListener('pointercancel', () => { splitPointer = null; pan = null; setHold(false); if (a) { a = null; clearPending(); } });
  f.addEventListener('pointerleave', () => { if (!a) setHold(false); });
  f.addEventListener('keydown', e => {   // with Mark on: Enter places an oval, arrows move it, Shift + arrows resize it, Enter confirms
    if (!v.marking || e.metaKey || e.ctrlKey || e.altKey) return;
    if (v.tools && !v.tools.ready) { e.preventDefault(); say('Opening the uncropped photo…'); return; }
    const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (e.key !== 'Enter' && !(d && v.pending)) return;
    e.preventDefault(); e.stopPropagation();   // not the Photo's own arrow keys, which change photo
    if (e.key === 'Enter') {
      if (v.pending) v.u.say.focus();
      else { setPending([0.3, 0.3, 0.7, 0.7], true); say('Oval placed. Arrow keys move it, Shift and arrow keys resize it, Enter to say what changes.'); }
      return;
    }
    const [x0, y0, x1, y1] = v.pending, s = 0.02, r = n => Math.round(n * 1e4) / 1e4;
    if (e.shiftKey) setPending([x0, y0, r(Math.min(1, Math.max(x0 + MIN_SIDE, x1 + d[0] * s))), r(Math.min(1, Math.max(y0 + MIN_SIDE, y1 + d[1] * s)))], true);
    else {
      const dx = Math.max(-x0, Math.min(1 - x1, d[0] * s)), dy = Math.max(-y0, Math.min(1 - y1, d[1] * s));
      setPending([r(x0 + dx), r(y0 + dy), r(x1 + dx), r(y1 + dy)], true);
    }
  });
}
function submitSay(ev) {
  ev.preventDefault();
  const t = v.u.say.value.trim(); if (!t) return;
  const l = v.list;
  if (v.pending) {
    if (l.filter(isMark).length >= MAX_MARKS) return say('This photo has 20 marks; remove one first.', 'bad');
    if (t.length > 300) return say('Keep what the oval should change under 300 characters.', 'bad');
    l.push({ box: v.pending, kind: 'auto', edit: t }); setMarking(false);
  } else {
    if (textOf([...l, t]).length > MAX_TEXT) return say('Too many changes on this photo; remove one first.', 'bad');
    l.push(t);
  }
  v.u.say.value = ''; saveEntries(); drawChips(); refresh();
}

/* ---- chips ----------------------------------------------------------------------------------- */
function markState(c, info, finder) {   // the one quiet line under a mark chip (F37)
  if (!info) return {};
  const pr = info.problems || [];
  if (pr.length) { const m = /^not understood \((.*?)\): "/.exec(pr[0]); return { text: m ? m[1] : 'not understood', bad: true }; }
  const w = info.word || 'thing', held = { clamped: ' · held back in part', blocked: ' · held back' }[info.status] || '';
  if (info.kind === 'thing') {
    if (info.found) return { text: 'the ' + w + held };
    return { text: info.why === 'bigger' ? 'That is part of something bigger' : info.why === 'small' ? 'The ' + w + ' is too small here' : 'No ' + w + ' found in the oval', useOval: true };
  }
  if (info.kind === 'area') return { text: 'the oval' + held + (finder === 'sam3' && c.kind === 'auto' ? '. Name the thing to follow its outline: this jacket' : '') };
  return {};
}
function drawChips() {
  const ul = v.u.chips, probs = v.u.probs; ul.replaceChildren(); probs.replaceChildren();
  v.u.changes.hidden = !v.list.length;
  const l = v.list, a = curAnswer() || {}, pl = a.instruction_problems || [];
  const finder = (((a.result || {}).audit || {}).run_config || {}).backend;
  let k = 0;
  const done = () => { saveEntries(); drawChips(); refresh(); };
  l.forEach((e, i) => {
    const li = el('li', { class: 'pchip' }), row = el('div', { class: 'row' });
    const x = button('×', () => { l.splice(i, 1); done(); }, { class: 'x', 'aria-label': 'Remove: ' + (isMark(e) ? e.edit : e) });
    if (!isMark(e)) {
      const t = norm(e), bad = pl.find(line => { const s = line.indexOf('"'), z = line.lastIndexOf('"'); return s >= 0 && z > s && t.includes(norm(line.slice(s + 1, z))); });
      row.append(el('span', { class: 'txt' }, e));
      if (bad) { li.classList.add('flag'); li.title = bad; }
      if ((a.result && a.result.adjustments || []).some(j => j.via === 'local' && j.source && t.includes(j.source))) row.append(el('small', null, 'local model'));
      row.append(x); li.append(row);
    } else {
      const info = (a.marks || [])[k++], st = markState(e, info, finder);
      row.append(el('span', { class: 'glyph', 'aria-hidden': 'true' }), el('span', { class: 'txt' }, e.edit), x); li.append(row);
      if (st.bad) li.classList.add('flag');
      if (st.text) {
        li.append(el('div', { class: 'st' + (st.bad ? ' warn' : '') }, st.text,
          st.useOval ? button('Use the oval', () => { e.kind = 'area'; done(); }, { class: 'link' }) : null));
      }
      hover(li, () => {
        ghost(e.box);
        tint((a.mark_overlays || []).filter(o => info && (info.targets || []).includes(o.target)), 'var(--mark)');
      });
    }
    ul.append(li);
  });
  pl.forEach(line => probs.append(el('p', { class: 'warn' }, line)));
}

/* ---- views ----------------------------------------------------------------------------------- */
function setView(name) {
  if (name !== 'edit' && v.tools) v.tools.setMode('none');
  const prev = v.view; v.view = name; setHold(false);
  if (v.primary && name !== 'try' && prev === 'try') { v.sliders = {}; uResetPractice(); }
  const u = v.u;
  u.which.hidden = name !== 'try' || !(v.tried && v.tried.compare);
  if (!v.primary) u.panels.edit.hidden = name !== 'edit'; u.panels.how.hidden = name !== 'how'; u.panels.try.hidden = name !== 'try';
  if (name === 'edit') paintEdit(); else if (name === 'how') paintHow(); else paintTry();
  if (!v.primary) (name === 'edit' ? u.navs[prev] || u.say : u.panels[name].querySelector('h2')).focus();
  showing(); refresh();
}
function paintEdit() {
  const j = v.answer, u = v.u;
  if (!j) setThumbs(); else { setImg(u.before, j.before); setImg(u.after, j.after); metaLine(j); }
  u.before.alt = 'Original'; u.after.alt = 'Edited photo'; u.engine.removeAttribute('src');
  paintMasks(); drawChips(); paintCheck(); showing(); if (j && v.tools) { v.tools.acceptPreview(j); paintNeutral(); }
}

/* ---- before you share: what the check found, and Hide (F21, F22) ------------------------------ */
const TRACE_NOTE = 'Mosaic and blur keep a trace of what was there: a plate or text can sometimes be read back. Cover copies nothing from under the region.';
function setHide(kind, on) {
  const o = getPhoto(v.file.path), l = new Set(o.hide || []);
  if (on) l.add(kind); else l.delete(kind);
  updatePhoto(v.file.path, { hide: [...l] });
}
function paintCheck() {
  const r = (v.answer || {}).reveal, box = v.u.check; box.replaceChildren(); box.hidden = !r;
  if (!r) return;
  box.append(el('h3', null, 'Before you share'));
  if (r.picture !== 'checked') {
    box.append(el('p', { class: 'fine' }, 'The picture was not checked: it needs SAM 3. File information was.'));
    if ((r.hide_note || '').length) box.append(el('p', { class: 'warn' }, r.hide_note));
    return;
  }
  const o = getPhoto(v.file.path), fromSettings = new Set(settingKinds(runOptions())), mine = new Set(o.hide || []);
  const kinds = [...new Set(r.findings.map(f => f.kind))];
  if (!kinds.length) box.append(el('p', { class: 'fine' }, 'Nothing on the list was found. That is not a statement that the photo is safe to share.'));
  const ul = el('ul', { class: 'pchips' });
  kinds.forEach(k => {
    const fs = r.findings.filter(f => f.kind === k), label = fs[0].label || k, on = fromSettings.has(k) || mine.has(k);
    const b = button(on ? 'Show again' : 'Hide', () => setHide(k, !on), { class: 'line', 'aria-pressed': String(on), disabled: fromSettings.has(k),
      title: fromSettings.has(k) ? 'Hidden by your Settings' : '' });
    ul.append(el('li', { class: 'pchip' }, el('div', { class: 'row' },
      el('span', { class: 'txt' }, label + (fs.length > 1 ? ' (' + fs.length + ')' : '') + (fs[0].weak ? ' · may miss' : '')), b)));
  });
  if (kinds.length) box.append(ul);
  if (r.more) box.append(el('p', { class: 'fine' }, 'and ' + r.more + ' more not listed; Hide covers them too.'));
  (r.hidden || []).forEach(h => box.append(el('p', { class: 'fine' }, 'Hidden in the saved picture: ' + (h.label || h.kind) + ' · ' + h.method + (h.trace ? ' · keeps a trace' : ''))));
  if (r.hide_note) box.append(el('p', { class: 'warn' }, r.hide_note));
  if (kinds.length) {
    const sel = el('select', { id: 'hidemethod', on: { change: e => { updatePhoto(v.file.path, { hideMethod: e.target.value }); } } },
      ...['cover', 'mosaic', 'blur'].map(m => el('option', { value: m }, nice(m))));
    sel.value = o.hideMethod || state.settings.hide_method || 'cover';
    box.append(el('div', { class: 'field' }, el('label', { htmlFor: 'hidemethod' }, 'How to hide'), sel), el('p', { class: 'fine' }, TRACE_NOTE));
  }
}

/* ---- how it was done ------------------------------------------------------------------------- */
function lines(box, o) {
  [['does', ''], ['when', 'When: '], ['watch', 'Watch: ']].forEach(([k, pre]) => { if (o && o[k]) box.append(el('p', null, pre + o[k])); });
}
function paintHow() {
  const j = v.how, ch = v.u.hChanges, st = v.u.hSteps; ch.replaceChildren(); st.replaceChildren(); v.u.hDetail.replaceChildren();
  if (!j) { setThumbs(); drawChips(); showing(); return; }
  const walk = j.walkthrough || [];
  if (!walk.length) ch.append(el('li', null, "No change of your own on this photo: the steps below are AutoEdit's edit."));
  walk.forEach(w => {
    const li = el('li', { tabIndex: 0 }), lab = el('p', { class: 'lab' }, w.label || niceName(w.target)), m = w.mask || {};
    (w.sources || []).forEach(s => lab.append(' ', el('q', null, s)));
    const word = m.found === false ? 'not found' : STATUS_WORD[w.status] || w.status || '';
    if (word) lab.append(' · ' + word);
    li.append(lab);
    if (m.line) li.append(el('p', null, m.line));
    if ((w.parts || []).length) li.append(el('p', null, w.parts.map(q => q.label + ' ' + pct(q.share)).join(' · ')));
    (w.tools || []).forEach(t => (t.applied || []).forEach(a => li.append(el('p', null,
      (t.name || t.op) + ' asked ' + fmtVal(t.op, t.asked) + ': ' + a.part + ' ' + fmtVal(t.op, a.value) + (a.state === 'held' || a.state === 'blocked' ? ' (' + a.state + ')' : '')))));
    (w.held || []).forEach(h => li.append(el('p', null, h)));
    hover(li, () => {
      ghost(m.box); const marks = (j.mark_overlays || []).filter(o => o.target === w.target);
      if (marks.length) tint(marks, 'var(--mark)'); else tint((j.mask_overlays || []).filter(o => m.name && o.name === m.name), 'var(--accent)');
    });
    ch.append(li);
  });
  if (v.primary) {
    const p = getPhoto(v.file.path);
    if (!walk.length) ch.replaceChildren();
    for (const step of p.edit_steps || []) {
      ch.append(el('li', null, (step.origin === 'recipe' ? 'Recipe' : 'Gemini') + ' · ' + step.label +
        (step.on ? ' · ' + pct(step.amount) : ' · Off'), step.reason ? el('p',null, step.reason) : null));
    }
    for (const [target, values] of [['Whole photo', p.sliders || {}], ...Object.entries(p.region_settings || {})]) {
      for (const [op, value] of Object.entries(values)) ch.append(el('li',null, 'Manual · ' + target + ' · ' + nice(op) + ' ' + fmtVal(op, value)));
    }
    if (p.manual) {
      const row = el('li', null, 'Manual · LightCraft'); ch.append(row);
      api('/editor/manual?path=' + encodeURIComponent(v.file.path)).then(stage => {
        if (stage.manual?.sha256 === p.manual.sha256) row.textContent = 'Manual · ' + ((stage.changed || []).map(nice).join(', ') || 'LightCraft');
      }).catch(() => {});
    }
  }
  (j.steps || []).forEach((s, i) => st.append(el('li', null, button((v.primary ? 'Local · ' : '') + (i + 1) + '. ' + (s.title || s.kind), () => chooseStep(i)))));
  chooseStep(Math.min(v.step, Math.max(0, (j.steps || []).length - 1)));
}
function chooseStep(i) {
  const j = v.how, steps = (j && j.steps) || []; if (!steps.length) return;
  v.step = Math.max(0, Math.min(steps.length - 1, i)); const s = steps[v.step], u = v.u;
  setImg(u.after, s.after); setImg(u.before, steps[v.step - 1]?.after || j.before);
  u.after.alt = 'Step ' + (v.step + 1); u.before.alt = 'Before step ' + (v.step + 1);
  [...u.hSteps.querySelectorAll('button')].forEach((b, k) => { if (k === v.step) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current'); });
  say('Step ' + (v.step + 1) + ' of ' + steps.length + ' · ' + (s.title || s.kind));
  const d = u.hDetail; d.replaceChildren();
  if ((s.kind === 'tools' || s.kind === 'grain') && (s.tools || []).length) {
    s.tools.forEach(t => { const b = el('div', null, el('strong', null, toolText(t))); lines(b, t); d.append(b); });
  } else { const b = el('div'); lines(b, s); if (b.childNodes.length) d.append(b); }
  (s.where || []).forEach(w => {
    const r = el('p', { class: 'where', tabIndex: 0 }, w.target + ' ' + pct(w.coverage) + ((w.tools || []).length ? ' · ' + w.tools.map(toolText).join(', ') : ''));
    hover(r, () => tint((j.mask_overlays || []).filter(o => o.name === w.target || o.name === w.name), 'var(--accent)'));
    d.append(r);
  });
  if ((s.typed || []).length) {
    const labs = s.typed.map(k => { const w = (j.walkthrough || [])[k]; return w ? (w.label || niceName(w.target)) : ''; }).filter(Boolean);
    if (labs.length) d.append(el('p', null, 'Your changes here: ' + labs.join(', ')));
  }
  (s.notes || []).forEach(n => d.append(el('p', null, n)));
  if (v.primary) {
    d.append(button('Compare this step', () => { v.split = !v.split; showing(); }, {class:'line'}));
    const areas = (s.where || []).filter(w => w.tools?.length && s.kind === 'regions');
    for (const area of areas) d.append(button('Try it yourself · ' + nice(area.target),
      () => beginPractice(s, area.target, area.tools), {class:'line'}));
    if (!areas.length && (s.tools || []).length) d.append(button('Try it yourself', () => beginPractice(s, '', s.tools), {class:'line'}));
  }
  if (s.moved != null) d.append(el('p', null, 'Moved ' + Math.round(s.moved * 10) / 10 + ' levels of 255 on average'));
  showing();
}

/* ---- try it yourself ------------------------------------------------------------------------- */
function beginPractice(step, target, tools) {
  const body = previewBody(), defs = v.how?.editor?.sliders || [];
  const supported = tools.filter(t => defs.some(d => d.op === t.op));
  if (!supported.length) { say('This step uses a tool outside the practice sliders.'); return; }
  const offsets = target ? body.region_settings?.[target] || {} : body.photo_settings || {};
  v.practice = {target, offsets, tools:supported, signature:JSON.stringify(body)};
  v.sliders = Object.fromEntries(supported.map(t => [t.op,t.value]));
  v.u.practiceTitle.textContent = (target ? nice(target) + ' · ' : '') + (step.title || step.kind);
  v.u.tSliders.replaceChildren(); v.u.tLesson.replaceChildren(); v.tried = null;
  setView('try');
}
function practiceOffsets() {
  const {offsets, tools} = v.practice, values = {...offsets}, defs = v.how.editor.sliders;
  for (const {op,value} of tools) {
    const d = defs.find(x => x.op === op), offset = (offsets[op] || 0) + (v.sliders[op] ?? value) - value;
    values[op] = Number(Math.max(d.min, Math.min(d.max, offset)).toFixed(4));
    if (!values[op]) delete values[op];
  }
  return values;
}
function practiceRequest() {
  const body = previewBody(), {target} = v.practice, values = practiceOffsets();
  if (target) body.region_settings = {...body.region_settings, [target]:values};
  else body.photo_settings = values;
  return body;
}
function applyPractice() {
  const p = v.practice;
  if (!p || p.signature !== JSON.stringify(previewBody())) { say('Your photo changed. Reopen this step to practice on the current edit.'); return; }
  const values = practiceOffsets(), patch = p.target ? {region_settings:{...getPhoto(v.file.path).region_settings,[p.target]:values}} : {sliders:values};
  setView('how'); updatePhoto(v.file.path, patch);
}
function uResetPractice() {
  v.u.tSliders?.replaceChildren(); v.practice = null; v.tried = null;
  const flight = v.flights.try;
  if (flight) { flight.controller?.abort(); delete v.flights.try; }
}
function paintTry() {
  const j = v.tried, u = v.u;
  if (!j) { setThumbs(); drawChips(); showing(); return; }
  setImg(u.before, j.before); setImg(u.after, j.after); u.after.alt = 'Edited photo'; u.before.alt = 'Original'; tint([]);
  const box = u.tSliders;
  const sliders = v.practice ? (j.editor?.sliders || []).filter(d => v.practice.tools.some(t => t.op === d.op)) : (j.try || {}).sliders || [];
  if (!box.childNodes.length) sliders.forEach(s => {
    const id = 'sl_' + s.op, val = el('span', { class: 'num' }), r = el('input', { type: 'range', id, min: s.min, max: s.max, step: s.step });
    r.dataset.op = s.op;
    const set = x => { const t = fmtVal(s.op, +x); val.textContent = t; r.setAttribute('aria-valuetext', t); };
    r.value = v.sliders[s.op] || 0; set(r.value);
    const teach = () => { const L = u.tLesson; L.replaceChildren(el('strong', null, s.name || s.op)); lines(L, s); };
    r.addEventListener('input', () => set(r.value)); r.addEventListener('focus', teach); r.addEventListener('pointerdown', teach);
    r.addEventListener('change', () => { const n = +r.value; if (v.practice || n) v.sliders[s.op] = n; else delete v.sliders[s.op]; set(r.value); refresh(); });
    box.append(el('div', { class: 'sl' }, el('div', { class: 'row' }, el('label', { htmlFor: id }, s.name || s.op), val), r));
  });
  const c = j.compare;
  u.which.hidden = !c; if (!c) v.which = 'yours';
  if (c) setImg(u.engine, c.engine);
  paintCompare(c); syncWhich(); showing();
}
function syncWhich() {
  v.u.whichYours.setAttribute('aria-pressed', String(v.which === 'yours')); v.u.whichEngine.setAttribute('aria-pressed', String(v.which === 'engine'));
}
function paintCompare(c) {
  const u = v.u, f = u.tFacts, tb = u.tBody; f.replaceChildren(); tb.replaceChildren(); u.tSaid.textContent = '';
  u.tTable.hidden = !c; if (!c) return;
  [...(c.checks || [])].sort((a, b) => (b.flag ? 1 : 0) - (a.flag ? 1 : 0)).forEach(k => {
    f.append(el('li', { class: k.flag ? 'warn' : '' }, (k.name || k.key) + ': ' + k.line));
  });
  const groups = new Map();
  (c.tools || []).forEach(t => { if (!groups.has(t.where)) groups.set(t.where, []); groups.get(t.where).push(t); });
  [...groups.keys()].sort((a, b) => (a === 'global' ? 0 : 1) - (b === 'global' ? 0 : 1)).forEach(w => {
    tb.append(el('tr', { class: 'grp' }, el('th', { colSpan: 3, scope: 'colgroup' }, whereName(w))));
    groups.get(w).forEach(t => tb.append(el('tr', null, el('td', null, t.name || t.op),
      el('td', { class: 'n' }, fmtVal(t.op, t.yours) + (t.set != null && t.set !== t.yours ? ' (set ' + fmtVal(t.op, t.set) + ')' : '')),
      el('td', { class: 'n' }, fmtVal(t.op, t.engine)))));
  });
  const s = c.said || {}, q = x => x ? 'quality ' + x.quality + (x.needs_review ? ', worth a look' : '') : '';
  u.tSaid.textContent = 'Your version: ' + q(s.yours) + '. AutoEdit: ' + q(s.engine) + '.';
}

/* ---- save ------------------------------------------------------------------------------------ */
function linkBrowserDrop(me, drop) {
  openSheet('Link original photo', (body, close) => {
    const status = el('p', {role:'status'});
    const pick = button('Choose original', async () => {
      pick.disabled = true;
      try {
        const {path} = await api('/pick?kind=file', {});
        if (!path || v !== me) return;
        await api('/editor/import', {path, expected_drop:drop.sha256});
        if (v !== me) return;
        const edits = getPhoto(me.file.path);
        if (edits.manual) { status.textContent = 'Export this manual version in Fine-tune before opening another original.'; return; }
        const {history, future, manual, ...local} = edits;
        const opened = await loadPhotoPath(path);
        if (!opened) return;
        const prior = getPhoto(path);
        const hasPrior = Object.keys(prior).some(k => !['history','future'].includes(k));
        if (Object.keys(local).length && !hasPrior) updatePhoto(path, local);
        close();
        toast(hasPrior && Object.keys(local).length ? 'Opened the original with its saved edit. The browser copy keeps its own changes.' :
          'Original linked. Exports go beside this photo.');
      } catch (error) { status.textContent = error.message; }
      finally { pick.disabled = false; }
    }, {class:'primary'});
    body.append(el('p', null, 'Choose the same file on disk to keep the project and its exports beside the original.'), pick, status);
  });
}
async function openInEditor(edited) {
  const me = v, path = edited ? me.file.exported : me.file.path;
  if (!path || (edited && me.file.dirty)) return;
  try {
    if (await persistProject() === false || v !== me || (edited && me.file.dirty)) return;
    sessionStorage.setItem('autoedit.editor.open', path);
    if (me.primary && me.editing && !edited) sessionStorage.setItem('autoedit.editor.options', JSON.stringify(previewBody()));
    else sessionStorage.removeItem?.('autoedit.editor.options');
    location.assign('/editor/fine-tune/');
  } catch (error) { toast('Could not open the photo editor. ' + error.message); }
}
async function save() {
  if (!v?.editing) return;
  if (v.draftSliders) { const sliders = Object.fromEntries(Object.entries(v.draftSliders).filter(([, value]) => value)); v.draftSliders = null; updatePhoto(v.file.path, { sliders }); }
  if (v.primary && getPhoto(v.file.path).manual) { await openInEditor(false); return; }
  const me = v, b = previewBody(), u = me.u, savedState = JSON.stringify({ photo: getPhoto(v.file.path), run: runOptions() }); if (u.save.disabled) return;
  u.save.disabled = true; u.saved.className = 'fine'; u.saved.textContent = 'Saving…'; u.saved.removeAttribute('title');
  try {
    const j = await api('/run-one', b), out = String(j.image_out || ''), name = out.split(/[\\/]/).pop() || 'the photo';
    toast('Saved ' + name + ' in AutoEdit Exports');
    me.file.exported = out; me.file.status = (j.result || {}).needs_review ? 'review' : 'finished';
    me.file.dirty = savedState !== JSON.stringify({ photo: getPhoto(me.file.path), run: runOptions() });
    await persistProject(); if (v === me) copyState();
    if (v === me) { u.saved.textContent = me.file.dirty ? 'Saved the earlier version. New changes still need saving.' : 'Saved to ' + (out || 'AutoEdit Exports'); u.saved.title = out;
      if (me.primary && out) u.saved.append(button('Show export', () => runTool('reveal', {path:out}).catch(e => toast(e.message)), {class:'link'})); }
  } catch (e) { if (v === me) { u.saved.className = 'bad'; u.saved.textContent = e.message; } else toast(e.message); }
  if (v === me) u.save.disabled = false;
}

/* ---- copy to similar ------------------------------------------------------------------------- */
// The sync tool reads the exported file's sidecar, so it needs this photo saved first. It holds the server's slow-job lock,
// so nothing runs until the button is pressed: the first press counts the similar photos, the Copy it offers does the copy.
function copyState() {
  const ready = !!v.file.exported && !v.file.dirty;
  for (const button of [v.u.copy, v.u.openEdited]) {
    if (!button) continue;
    button.disabled = !ready; button.title = ready ? '' : 'Save this photo’s current changes first';
  }
}
async function copyAsk() {
  const me = v, u = me.u, target = me.file.exported; if (!target || me.file.dirty) return;
  u.copy.disabled = true; u.copyRes.textContent = 'Looking for similar photos…';
  try {
    const report = await runTool('sync', { target, dry_run: true }), candidates = report.targets || [];
    if (v !== me) return;
    u.copyRes.textContent = candidates.length ? '' : 'No similar photos found.';
    if (candidates.length) openSheet('Copy to similar photos', (body, close) => {
      const selected = new Set(candidates.map(t => t.sidecar)), categories = new Set(['tone', 'color', 'white_balance', 'detail']);
      const list = el('div', { class: 'editor-copy-list' }), count = el('p', { class: 'fine', role: 'status' });
      const match = el('input', { type: 'checkbox', checked: true });
      const apply = button('Copy changes', () => {
        const opts = { targets: [...selected], categories: [...categories], match_exposure: match.checked };
        close(); copyNow(target, opts);
      }, { class: 'primary' });
      const paint = () => { count.textContent = selected.size + ' of ' + candidates.length + ' photos selected'; apply.disabled = !selected.size || (!categories.size && !match.checked); };
      const checks = [];
      for (const t of candidates) {
        const input = el('input', { type: 'checkbox', checked: true, on: { change: e => { if (e.target.checked) selected.add(t.sidecar); else selected.delete(t.sidecar); paint(); } } });
        checks.push([input, t.sidecar]); list.append(el('label', null, input, ' ' + (t.image || t.sidecar).split(/[\\/]/).pop()));
      }
      const types = el('fieldset', { class: 'editor-copy-types' }, el('legend', null, 'What to copy'));
      for (const [key, label] of [['tone', 'Light and tone'], ['color', 'Color'], ['white_balance', 'White balance'], ['detail', 'Detail']]) {
        types.append(el('label', null, el('input', { type: 'checkbox', checked: true, on: { change: e => { if (e.target.checked) categories.add(key); else categories.delete(key); paint(); } } }), ' ' + label));
      }
      match.addEventListener('change', paint);
      body.append(el('p', { class: 'fine' }, 'Copy selected adjustments from the saved photo. Each photo keeps its own automatic edit. Marks stay on their original photo.'),
        types, el('label', { class: 'editor-copy-match' }, match, ' Match exposure to this photo'),
        el('div', { class: 'editor-history' }, button('Select all', () => { checks.forEach(([c, p]) => { c.checked = true; selected.add(p); }); paint(); }, { class: 'link' }),
          button('Clear selection', () => { selected.clear(); checks.forEach(([c]) => { c.checked = false; }); paint(); }, { class: 'link' })),
        count, list, apply); paint();
    });
  } catch (e) { if (v === me) u.copyRes.textContent = e.message; }
  if (v === me) copyState();
}
const photoValues = value => Object.fromEntries(Object.entries(value || {}).filter(([key]) => !['history', 'future'].includes(key)));
const photoSignature = value => JSON.stringify(photoValues(value));
const exportSidecar = path => String(path || '').replace(/\.[^./\\]+$/, '.autoedit.json');
function copiedPatch(result, previous) {
  return { base: { adjustments: (result.adjustments || []).filter(a => a.scope === 'photo' && !String(a.target).startsWith('mark:')),
    selected_regions: result.selected_regions || [], exposure_bias: result.exposure_bias || 0, wb: result.wb || null, wb_bias: result.wb_bias || [0, 0] }, sliders: {}, region_settings: {}, edit_steps: undefined,
    entries: (previous.entries || []).filter(okMark), neutral: undefined };
}
const copiedFile = result => ({ dirty: false, status: result.needs_review ? 'review' : 'finished', ...(result.image ? { out: result.image } : {}) });
async function rememberCopied(results, dir, recipients) {
  const changes = [], summary = { kept: 0, changed: 0, outside: 0 };
  for (const result of results || []) {
    if (result.status !== 'synced') continue;
    const known = recipients.find(({ file }) => file.exported && (file.exported === result.image || exportSidecar(file.exported) === result.sidecar));
    if (!known) { summary.outside++; continue; }
    changes.push({ ...known, result, patch: copiedPatch(result, known.photo) });
  }
  if (!changes.length) return summary;
  const applyOpen = async () => {
    for (const item of changes) {
      const file = state.files.find(f => f.path === item.file.path); if (!file) { summary.outside++; continue; }
      if (photoSignature(getPhoto(file.path)) !== photoSignature(item.photo)) { file.dirty = true; summary.changed++; continue; }
      updatePhoto(file.path, item.patch);
      const saved = copiedFile(item.result); file.dirty = false; file.status = saved.status; if (saved.out) file.exported = saved.out;
      summary.kept++;
    }
    await persistProject();
  };
  if (state.dir === dir) { await applyOpen(); return summary; }
  // A copy can outlive its screen. Update the original saved project without touching a
  // different folder's in-memory work, then reflect it if that folder reopened meanwhile.
  const saved = (await api('/project?dir=' + encodeURIComponent(dir))).project;
  if (state.dir === dir) { await applyOpen(); return summary; }
  if (!saved) throw new Error('Copied files were saved, but their project could not be reopened.');
  saved.photos ||= {}; saved.files ||= {};
  for (const item of changes) {
    const previous = saved.photos[item.file.path] || {};
    if (photoSignature(previous) !== photoSignature(item.photo)) { summary.changed++; continue; }
    const after = { ...photoValues(previous), ...item.patch };
    for (const key of Object.keys(after)) if (after[key] === undefined) delete after[key];
    saved.photos[item.file.path] = { ...after, history: [...(previous.history || []), photoValues(previous)].slice(-30), future: [] };
    saved.files[item.file.path] = { ...(saved.files[item.file.path] || {}), ...copiedFile(item.result) };
    summary.kept++;
  }
  await api('/project', { dir, project: saved });
  if (state.dir === dir) { summary.kept = 0; summary.changed = 0; await applyOpen(); }
  return summary;
}
async function copyNow(target, selection = {}) {
  const me = v, u = me.u, dir = state.dir;
  const recipients = state.files.map(file => { loadEntries(file.path); return { file: { path: file.path, exported: file.exported }, photo: getPhoto(file.path) }; });
  u.copy.disabled = true; u.copyRes.textContent = 'Working…';
  try {
    await persistProject();
    const r = await runTool('sync', { target, dry_run: false, ...selection }, m => { if (v === me) u.copyRes.textContent = m; });
    const imported = await rememberCopied(r.targets, dir, recipients), lines = r.lines || ['Done.'];
    if (imported.kept) lines.push('Copied adjustments kept in the project for ' + imported.kept + ' photos.');
    if (imported.changed) lines.push(imported.changed + ' newer photo drafts were kept. Their copied exports are saved separately.');
    if (imported.outside) lines.push(imported.outside + ' copied files are outside this open project.');
    if (v === me) u.copyRes.replaceChildren(...lines.map(l => el('div', null, String(l))));
    else toast(lines[lines.length - 1]);
  } catch (e) { if (v === me) u.copyRes.textContent = e.message; else toast(e.message); }
  if (v === me) copyState();
}

/* ---- editable controls and project history ------------------------------------------------- */
function syncEditControls() {
  if (!v) return;
  const p = getPhoto(v.file.path), u = v.u;
  if (u.style && p.style && [...u.style.options].some(o => o.value === p.style)) u.style.value = p.style;
  if (u.look) u.look.value = p.look === undefined ? state.look : p.look;
  if (u.strength) { u.strength.value = p.intensity === undefined ? (state.strength === null ? u.defaultStrength : state.strength) : p.intensity; u.paintStrength(); }
  for (const [op, c] of Object.entries(u.manualInputs || {})) { c.input.value = (p.sliders === undefined ? state.folderSliders || {} : p.sliders)[op] || 0; c.paint(); }
  if (u.copiedChip) u.copiedChip.hidden = !p.base;
  if (u.undo) u.undo.disabled = !canUndo(v.file.path);
  if (u.redo) u.redo.disabled = !canRedo(v.file.path);
  const ref = options().reference;
  if (u.referenceCaption) u.referenceCaption.textContent = ref ? String(ref).split(/[\\/]/).pop() : 'No reference selected';
  if (u.referenceThumb) { u.referenceThumb.hidden = !ref; if (ref) u.referenceThumb.src = thumbUrl(ref, 320); }
  if (u.referenceRemove) u.referenceRemove.disabled = !ref;
  if (u.referenceStrength) { u.referenceStrength.hidden = !ref; u.referenceStrength.value = p.reference_strength === undefined ? (state.settings.reference_strength === undefined ? u.defaultReferenceStrength : state.settings.reference_strength) : p.reference_strength; }
}
function manualControls(defs) {
  if (!v || !Array.isArray(defs) || !defs.length || v.u.manualInputs) return;
  const me = v, u = me.u; u.manualInputs = {}; u.manual.replaceChildren();
  for (const s of defs) {
    const id = 'edit_' + s.op, val = el('output', { class: 'num', htmlFor: id });
    const input = el('input', { type: 'range', id, min: s.min, max: s.max, step: s.step, title: s.does || '' });
    const paint = () => { const t = +input.value ? fmtVal(s.op, +input.value) : getPhoto(v.file.path).base ? 'No added change' : 'Auto'; val.textContent = t; input.setAttribute('aria-valuetext', t); };
    input.addEventListener('input', () => {
      paint(); v.draftSliders = { ...(getPhoto(v.file.path).sliders === undefined ? state.folderSliders || {} : getPhoto(v.file.path).sliders), [s.op]: +input.value }; schedulePreview();
    });
    input.addEventListener('change', () => {
      const sliders = { ...(getPhoto(v.file.path).sliders === undefined ? state.folderSliders || {} : getPhoto(v.file.path).sliders) }, amount = +input.value;
      if (amount) sliders[s.op] = amount; else delete sliders[s.op];
      v.draftSliders = null; updatePhoto(v.file.path, { sliders });
    });
    u.manualInputs[s.op] = { input, paint };
    u.manual.append(el('div', { class: 'sl' }, el('div', { class: 'row' },
      el('label', { htmlFor: id }, s.op === 'saturation' ? 'Color' : s.name || nice(s.op)), val), input));
  }
  syncEditControls();
}
function historyMove(redo = false) { if (redo ? redoPhoto(v.file.path) : undoPhoto(v.file.path)) v.u.saved.textContent = 'Changes ready. Save to update the exported photo.'; }
function resetPhoto() {
  updatePhoto(v.file.path, { look: undefined, intensity: undefined, neutral: undefined, reference: undefined,
    reference_strength: undefined, base: undefined, sliders: undefined, entries: [], hide: undefined, hideMethod: undefined,
    edit_steps: undefined, region_settings: undefined, mask_refinements: undefined, photo_geometry: undefined, local_only: undefined });
}
function chooseLooks() {
  const me = v, names = [...me.u.look.options].map(o => o.value).filter(Boolean); if (!names.length) return;
  let page = 0, controller;
  openSheet('Choose a look', (body, close) => {
    const note = el('p', { class: 'fine', role: 'status' }), grid = el('div', { class: 'editor-looks' });
    const prev = button('Previous looks', () => { page--; render(); }, { class: 'line' });
    const next = button('More looks', () => { page++; render(); }, { class: 'line' });
    const render = async () => {
      if (controller) controller.abort(); controller = new AbortController(); me.lookController = controller;
      const current = controller, picks = names.slice(page * 6, page * 6 + 6);
      prev.disabled = page === 0; next.disabled = (page + 1) * 6 >= names.length;
      note.textContent = 'Rendering these looks on your photo…'; grid.replaceChildren();
      const tiles = new Map();
      for (const name of picks) {
        const img = el('img', { alt: '', src: thumbUrl(me.file.thumb, 320) });
        const tile = button('', () => { controller.abort(); updatePhoto(me.file.path, { look: name }); close(); },
          { 'aria-label': 'Use ' + nice(name), 'aria-pressed': String((getPhoto(me.file.path).look === undefined ? state.look : getPhoto(me.file.path).look) === name) });
        tile.append(img, el('span', null, nice(name))); grid.append(tile); tiles.set(name, img);
      }
      try {
        const request = previewBody(); delete request.export;
        const result = await api('/variants', { ...request, variants: picks.map(name => ({ name, look: name })) }, { signal: current.signal });
        if (v !== me || current !== controller || !grid.isConnected) return;
        for (const item of result.variants || []) { const img = tiles.get(item.name); if (img && pic(item.after)) img.src = item.after; }
        note.textContent = 'Looks on this photo with your adjustments. Choose one to see the full preview.';
      } catch (e) { if (e.name !== 'AbortError' && v === me && current === controller) note.textContent = e.message; }
    };
    body.append(note, grid, el('div', { class: 'editor-history' }, prev, next));
    document.getElementById('sheet').addEventListener('close', () => { if (controller) controller.abort(); }, { once: true }); render();
  });
}
function referencePanel() {
  const me = v, u = me.u;
  u.referenceCaption = el('span', { class: 'fine' }); u.referenceThumb = el('img', { class: 'editor-reference', alt: 'Reference photo', hidden: true });
  const choose = button('Choose reference…', async () => {
    choose.disabled = true;
    try { const r = await api('/pick?kind=file', {}); if (r.path && v === me) updatePhoto(me.file.path, { reference: r.path }); }
    catch (e) { toast(e.message); }
    finally { choose.disabled = false; }
  }, { class: 'line' });
  u.referenceRemove = button('Remove', () => updatePhoto(v.file.path, { reference: '' }), { class: 'link' });
  u.defaultReferenceStrength = 0.5;
  u.referenceStrength = el('input', { type: 'range', min: 0, max: 1, step: 0.05, 'aria-label': 'Reference strength', hidden: true,
    on: { change: e => updatePhoto(v.file.path, { reference_strength: +e.target.value }) } });
  getSchema().then(sc => { if (v !== me) return; const s = (sc.settings || []).find(s => s.key === 'reference_strength');
    if (s) { u.defaultReferenceStrength = s.default; u.referenceStrength.min = s.min; u.referenceStrength.max = s.max; } syncEditControls(); }).catch(() => {});
  return el('details', { class: 'field editor-reference-box' }, el('summary', null, 'Match a reference'),
    u.referenceThumb, u.referenceCaption, el('div', { class: 'editor-history' }, choose, u.referenceRemove), u.referenceStrength);
}

/* ---- the panel ------------------------------------------------------------------------------- */
function editPanel() {
  const path = v.file.path, u = v.u;
  const lookSel = u.look = el('select', { id: 'look', on: { change: e => updatePhoto(path, { look: e.target.value }) } }, el('option', { value: '' }, 'Default'));
  const range = u.strength = el('input', { type: 'range', id: 'strength', min: 0, max: 2, step: 0.05 });
  const out = el('span', { class: 'value num' }), reset = button('Use folder strength', () => updatePhoto(path, { intensity: undefined }), { class: 'link', hidden: true });
  u.defaultStrength = 0.8;
  u.paintStrength = () => { out.textContent = Number(range.value).toFixed(2); reset.hidden = getPhoto(path).intensity === undefined; };
  range.addEventListener('input', u.paintStrength);
  range.addEventListener('change', () => updatePhoto(path, { intensity: +range.value }));
  getSchema().then(sc => {
    if (v !== u.owner) return;
    const by = Object.fromEntries((sc.settings || []).map(s => [s.key, s]));
    (by.look && by.look.choices || []).forEach(n => lookSel.add(new Option(nice(n), n)));
    const want = getPhoto(path).look === undefined ? state.look : getPhoto(path).look;
    if (want && ![...lookSel.options].some(o => o.value === want)) lookSel.add(new Option(nice(want), want));
    if (by.intensity) { range.min = by.intensity.min; range.max = by.intensity.max; u.defaultStrength = by.intensity.default; }
    syncEditControls();
  }).catch(e => toast('Settings could not be read: ' + e.message));
  u.copiedChip = el('div', { class: 'pchip', hidden: true }, el('div', { class: 'row' }, el('span', { class: 'txt' }, 'Copied adjustments'),
    button('Reset copied', () => updatePhoto(path, { base: undefined }), { class: 'link', title: 'Remove the copied adjustments. Undo restores them.' })));
  u.manual = el('div', { class: 'sliders' }, el('p', { class: 'fine' }, 'Adjustments appear when the preview is ready.'));
  u.undo = button('Undo', () => historyMove(), { class: 'line', disabled: true, title: 'Undo photo change (Ctrl/Cmd + Z)' });
  u.redo = button('Redo', () => historyMove(true), { class: 'line', disabled: true, title: 'Redo photo change (Ctrl/Cmd + Shift + Z)' });
  const history = el('div', { class: 'editor-history' }, u.undo, u.redo, button('Reset photo', resetPhoto, { class: 'link', title: 'Use folder settings and remove this photo’s adjustments. Undo restores them.' }));

  u.chips = el('ul', { class: 'pchips' }); u.probs = el('div', { class: 'probs' });
  u.changes = el('div', { class: 'field' }, el('h3', null, 'Your changes'), u.chips);
  u.check = el('div', { class: 'field', hidden: true });
  u.masksBox = el('input', { type: 'checkbox', id: 'masks', on: { change: e => {
    v.masks = e.target.checked; paintMasks(); if (v.masks) once('how'); } } });
  u.save = el('button', { type: 'button', class: 'primary', on: { click: save } }, 'Save this photo');
  u.saved = el('p', { class: 'fine', role: 'status' });
  u.copy = el('button', { type: 'button', class: 'line', disabled: true, title: 'Save this photo first', on: { click: copyAsk } }, 'Copy to similar');
  u.copyRes = el('div', { class: 'fine', role: 'status' });
  u.navs = {};
  const nav = (text, name) => u.navs[name] = el('button', { type: 'button', class: 'nav', on: { click: () => setView(name) } }, el('span', null, text), el('span', { 'aria-hidden': 'true' }, '›'));
  u.grey = button('Pick something grey', () => setPicking(!v.picking), { class: 'line', 'aria-pressed': 'false', title: 'Then click a spot in the photo that should be grey or white' });
  u.greyChip = el('div', { class: 'pchip', hidden: true }, el('div', { class: 'row' }, el('span', { class: 'txt' }, 'Grey point'),
    button('×', () => setNeutral(null), { class: 'x', 'aria-label': 'Remove the grey point' })));
  const rate = (text, decision) => {
    const b = button(text, async () => {
      try {
        await api('/review/labels', { out_dir: exportsDir(), labels: [{ image: path, decision, source: 'edit' }] });
        both.forEach(x => x.setAttribute('aria-pressed', String(x === b))); toast('Noted');
      } catch (e) { toast(e.message); }
    }, { class: 'line', 'aria-pressed': 'false' });
    return b;
  };
  const both = [rate('Good', 'keep'), rate('Not good', 'reject')];
  u.review = el('div', { class: 'field', hidden: !store.get('labels', false) }, el('h3', null, 'Is this edit good?'), el('div', { class: 'btnrow' }, ...both));
  const recipes = button('Recipes', () => openPhotoPresets({
      file: v.file,
      getOptions: () => ({ look: options().look || '', intensity: options().intensity ?? u.defaultStrength }),
      getOffsets: () => {
        const own = getPhoto(path);
        return { photo_settings: own.sliders ?? state.folderSliders ?? {}, region_settings: own.region_settings || {},
          edit_steps: own.edit_steps || [] };
      },
    }), { class: 'line' });
  if (v.primary) {
    const [changes, objects, framing, assist] = v.tools.panels;
    objects.open = assist.open = true;
    u.sidebar.panel('adjust').replaceChildren(u.tune, framing);
    u.sidebar.panel('isolate').replaceChildren(objects, el('label', {class:'check'}, u.masksBox, 'Show all masks'));
    u.sidebar.panel('gemini').replaceChildren(assist);
    return el('div', {class:'panel'}, history, changes,
      el('div', {class:'field'}, el('div', {class:'row'}, el('label', {htmlFor:'strength'}, 'Strength'), out, reset), range),
      el('details', {class:'editor-tool'}, el('summary', null, 'Whole photo adjustments'), u.copiedChip, u.manual, u.grey, u.greyChip),
      el('details', {class:'editor-tool'}, el('summary', null, 'Looks & references'), lookSel, button('Compare looks…', chooseLooks, {class:'line'}), referencePanel(), recipes),
      u.changes, u.probs, u.check, u.copy, u.copyRes, u.review, u.saved);
  }
  return el('div', { class: 'panel' }, history, recipes,
    el('div', { class: 'field' }, el('label', { htmlFor: 'look' }, 'Look'), lookSel, button('Compare looks…', chooseLooks, { class: 'line' })),
    el('div', { class: 'field' }, el('div', { class: 'row' }, el('label', { htmlFor: 'strength' }, 'Strength'), out, reset), range),
    el('details', { class: 'editor-tool' }, el('summary', null, 'Whole photo adjustments'), u.copiedChip, u.manual),
    el('div', { class: 'field' }, u.grey, u.greyChip), referencePanel(),
    ...v.tools.panels,
    u.changes, u.probs, u.check,
    u.copy, u.copyRes,
    el('div', { class: 'field' }, nav('How it was done', 'how'), nav('Try it yourself', 'try'),
      el('label', { class: 'check' }, u.masksBox, ' Masks')),
    u.review,
    el('div', { class: 'field' }, u.save, u.saved,
      u.openEdited = button('Open saved copy in Photo editor', () => openInEditor(true), { class: 'line', disabled: true })));
}
function howPanel() {
  const u = v.u;
  u.hChanges = el('ol', { class: 'plist' }); u.hSteps = el('ol', { class: 'plist' }); u.hDetail = el('div', { class: 'detail' });
  if (v.primary) return el('div', {class:'panel', hidden:true},
    el('h3', null, 'What changed'), u.hSteps, u.hDetail, u.hChanges);
  return el('div', { class: 'panel', hidden: true },
    button('← Back', () => setView('edit'), { class: 'link back' }),
    el('h2', { tabIndex: -1 }, 'How it was done'),
    el('h3', null, 'Your changes'), u.hChanges, el('h3', null, 'Every step'), u.hSteps, u.hDetail);
}
function tryPanel() {
  const u = v.u;
  u.tSliders = el('div', { class: 'sliders' }); u.tLesson = el('div', { class: 'detail' });
  u.tFacts = el('ul', { class: 'plist' }); u.tBody = el('tbody'); u.tSaid = el('p');
  u.tTable = el('table', { hidden: true }, el('thead', null, el('tr', null, el('th', { scope: 'col' }, 'Tool'),
    el('th', { scope: 'col', class: 'n' }, 'Yours'), el('th', { scope: 'col', class: 'n' }, 'AutoEdit'))), u.tBody);
  if (v.primary) return el('div', {class:'panel', hidden:true}, el('h3',null,'Try it yourself'),
    u.practiceTitle = el('p'), u.tSliders, u.tLesson,
    button('Apply practice edit', applyPractice, {class:'primary'}), button('Discard practice', () => setView('how'), {class:'line'}));
  const cmp = el('button', { type: 'button', 'aria-pressed': 'false', on: { click: () => {
    v.compare = !v.compare; cmp.setAttribute('aria-pressed', String(v.compare)); refresh(); } } }, 'Compare with AutoEdit');
  return el('div', { class: 'panel', hidden: true },
    button('← Back', () => setView('edit'), { class: 'link back' }),
    el('h2', { tabIndex: -1 }, 'Try it yourself'),
    u.tSliders, u.tLesson,
    el('div', { class: 'btnrow' }, cmp, button('Reset', () => {
      v.sliders = {}; u.tSliders.querySelectorAll('input').forEach(r => { r.value = 0; r.dispatchEvent(new Event('input')); }); refresh(); })),
    u.tFacts, u.tTable, u.tSaid,
    el('p', { class: 'fine' }, "Saving keeps AutoEdit's edit with your sentences and marks, not these sliders."));
}

/* ---- browsing and comparison ---------------------------------------------------------------- */
function filmstrip() {
  const strip = el('nav', { class: 'editor-filmstrip', 'aria-label': 'Photos in this folder' });
  const start = Math.max(0, v.index - 12), end = Math.min(state.files.length, v.index + 13);
  for (let i = start; i < end; i++) {
    const f = state.files[i], active = i === v.index;
    strip.append(button('', () => { state.reviewQueue = false; go('photo', { index: i }); }, { title: f.name + (f.recipeReview ? ' · ' + f.recipeReview : f.status === 'review' ? ' · needs review' : ''),
      'aria-label': 'Open ' + f.name, 'aria-current': active ? 'true' : undefined, data: { status: f.status || '' } }));
    strip.lastChild.append(el('img', { src: thumbUrl(f.thumb, 160), alt: '', loading: 'lazy' }), el('span', null, f.name));
  }
  return strip;
}
function compareTools(begin) {
  const u = v.u;
  u.begin = button(v.file.dirty || v.file.exported || canUndo(v.file.path) ? 'Resume guided edit' : 'Preview AutoEdit', begin, { class: 'primary' });
  u.split = button(v.primary ? 'Before / After' : 'Before / after', () => {
    if (!v.split && v.tools.mode !== 'none') v.tools.setMode('none');
    setHold(false); v.split = !v.split; showing();
    if (v.split) u.splitControl.focus({ preventScroll: true });
  }, { class: 'line', disabled: true, 'aria-pressed': 'false' });
  u.zoom = button('100% preview', () => {
    v.zoom = !v.zoom; u.zoom.textContent = v.zoom ? 'Fit photo' : '100% preview'; u.zoom.setAttribute('aria-pressed', String(v.zoom)); fit();
    if (v.zoom) { u.canvas.scrollLeft = (u.canvas.scrollWidth - u.canvas.clientWidth) / 2; u.canvas.scrollTop = (u.canvas.scrollHeight - u.canvas.clientHeight) / 2; }
  }, { class: 'line', 'aria-pressed': 'false', title: 'Inspect preview pixels. Drag to pan, or use the scrollbars.' });
  if (v.primary) {
    u.editActions = el('div', {class: 'editor-action-group'}, u.split);
    return el('div', {class: 'editor-compare'}, u.editActions, u.zoom, u.sidebar.drawer);
  }
  u.editActions = el('div', { class: 'editor-action-group', hidden: true }, u.split,
    button('Targeted edits', () => { if (v.view !== 'edit') setView('edit'); v.tools.openObjects(); }, { class: 'line' }));
  return el('div', { class: 'editor-compare' }, u.begin, u.editActions, u.zoom,
    button('Open original in Photo editor', () => openInEditor(false), { class: 'line' }));
}

/* ---- the screen ------------------------------------------------------------------------------ */
const neighbourIndex = d => {
  for (let i = v.index + d; i >= 0 && i < state.files.length; i += d) if (!state.reviewQueue || state.files[i].recipeReview || ['review', 'failed'].includes(state.files[i].status)) return i;
  return -1;
};
const neighbour = d => { const i = neighbourIndex(d); if (i >= 0) go('photo', { index: i }); };

export const screen = {
  mount(root, { index, editor = false }) {
    const file = state.files[index];
    if (!file) {
      setBar(backButton('Folder', 'folder'), el('span', { class: 'grow' }), gearButton());
      root.append(el('p', { class: 'fine pv-gone' }, 'That photo is not in this folder.')); return;
    }
    const u = {}, me = v = { index, file, u, primary: editor, editing: false, view: 'edit', list: [], flights: {}, answer: null, how: null, tried: null,
      step: 0, sliders: {}, compare: false, which: 'yours', hold: false, marking: false, picking: false, pending: null, masks: false,
      busy: 0, scene: '', busyTimer: 0, busyFrom: 0, previewTimer: 0, draftSliders: null, unframed: false, split: false, splitAt: 50, zoom: false };
    u.owner = me;
    u.title = el('span', { class: 'title' });
    u.orig = el('button', { type: 'button', hidden: true, 'aria-pressed': 'false', title: 'Hold B to see the original', on: { click: () => setHold(!v.hold) } }, 'Original');
    if (editor) setBar(backButton('Library', 'folder'), u.title, el('span', {class:'grow'}),
      u.exportButton = button('Export', () => {
        if (getPhoto(file.path).manual) openInEditor(false);
        else if (!me.editing) toast('Nothing to export yet. Choose Run AI Edit, or Adjust \u2192 Fine-tune in LightCraft to develop the photo by hand.');
        else save();
      }, {class:'primary'}));
    else setBar(backButton('Folder', 'folder'), u.title, el('span', { class: 'grow' }),
      el('button', { type: 'button', 'aria-label': state.reviewQueue ? 'Previous photo to review' : 'Previous photo', disabled: neighbourIndex(-1) < 0, on: { click: () => neighbour(-1) } }, '‹'),
      el('button', { type: 'button', 'aria-label': state.reviewQueue ? 'Next photo to review' : 'Next photo', disabled: neighbourIndex(1) < 0, on: { click: () => neighbour(1) } }, '›'),
      state.reviewQueue ? button('Review queue', () => { state.reviewQueue = false; go('photo', { index }); }, { class: 'line', title: 'Leave the review queue and browse every photo' }) : null, u.orig, gearButton());
    title();

    u.opening = el('div', {class:'editor-opening',role:'status',hidden:!editor}, file.isRaw ? 'Opening RAW…' : 'Opening photo…');
    u.busy = el('div', { class: 'pv-busy', hidden: true });
    u.before = el('img', { alt: 'Original' }); u.after = el('img', { alt: 'Edited photo' }); u.engine = el('img', { alt: "AutoEdit's edit" });
    u.tint = el('div', { class: 'pv-tint' });
    u.ghost = el('div', { class: 'oval', hidden: true }); u.pend = el('div', { class: 'oval pend', hidden: true });
    u.greyDot = el('div', { class: 'pv-grey', hidden: true }); u.secs = el('span', { 'aria-hidden': 'true' });
    u.tag = el('div', { class: 'pv-tag', hidden: true });
    u.splitControl = el('div', { class: 'editor-split-line', role: 'slider', tabIndex: 0, hidden: true,
      'aria-label': 'Before and after divider', 'aria-orientation': 'horizontal', 'aria-valuemin': 0, 'aria-valuemax': 100,
      title: 'Drag to compare, or use the arrow keys' }, el('span', { 'aria-hidden': 'true' }, '‹ ›'));
    u.frame = el('div', { class: 'pv-frame', role: 'group', tabIndex: 0, 'aria-label': 'Original photo', data: { showing: 'before' } },
      u.before, u.after, u.engine, u.tint, el('div', { class: 'pv-ovals' }, u.ghost, u.pend, u.greyDot), u.tag,
      u.splitControl, el('div', { class: 'editor-split-labels', 'aria-hidden': 'true' }, u.splitBefore = el('span', null, editor ? 'Original' : 'Before'), u.splitAfter = el('span', null, editor ? 'Current edit' : 'After')));
    u.canvas = el('div', { class: 'pv-canvas' }, u.frame);
    u.status = el('div', { class: 'pv-status', role: 'status' });
    u.mark = el('button', { type: 'button', class: 'line', 'aria-pressed': 'false', title: 'Draw an oval, then say what changes in it (M)',
      on: { click: () => setMarking(!v.marking) } }, 'Mark');
    u.markTag = el('small', { class: 'mark-tag', hidden: true }, 'for the oval');
    u.say = el('input', { type: 'text', id: 'say', placeholder: SAY, autocomplete: 'off', 'aria-label': 'Say what to change' });
    u.which = el('span', { class: 'pv-which', hidden: true },
      u.whichYours = button('Yours', () => { v.which = 'yours'; syncWhich(); showing(); }, { 'aria-pressed': 'true' }),
      u.whichEngine = button("AutoEdit's", () => { v.which = 'engine'; syncWhich(); showing(); }, { 'aria-pressed': 'false' }));
    const form = el('form', { class: 'pv-say', on: { submit: submitSay } }, u.mark, u.markTag, u.say);
    const under = el('div', { class: 'pv-under', hidden: true }, el('div', { class: 'pv-line' }, u.status, u.which), form);
    if (editor) {
      u.sidebar = createEditorSidebar(el, selectSection);
      u.style = el('select', {id: 'photo-local-style', 'aria-label': 'Local style'}, el('option', {value:'standard'}, 'Standard'));
      api('/local-styles').then(({styles}) => {
        if (v !== me) return;
        u.style.replaceChildren(...styles.map(s => el('option', {value:s.id}, s.label)));
        u.style.value = getPhoto(file.path).style || 'standard';
      }).catch(() => {});
      u.run = button('Run AI Edit', () => {
        updatePhoto(file.path, {style:u.style.value || 'standard', ai_enabled:true});
        if (!me.editing) startEditing(); else refresh();
      }, {class:'primary'});
      u.aiActions = el('div', {class:'field'}, el('label',{htmlFor:'photo-local-style'},'Local style'), u.style, u.run);
      const drops = store.get('browserDrops', []), drop = Array.isArray(drops) && drops.find(x => x.path === file.path);
      if (drop) u.aiActions.prepend(el('div',{class:'drop-source'}, el('span',null,'Browser copy'),
        button('Link original', () => linkBrowserDrop(me, drop), {class:'link'})));
      u.sidebar.panel('ai').append(u.aiActions);
      if (canUndo(file.path) || canRedo(file.path)) {
        u.originalHistory = el('div', {class:'editor-history'},
          button('Undo', () => { if (undoPhoto(file.path)) go('photo',{index}); }, {class:'line',disabled:!canUndo(file.path)}),
          button('Redo', () => { if (redoPhoto(file.path)) go('photo',{index}); }, {class:'line',disabled:!canRedo(file.path)}));
        u.aiActions.append(u.originalHistory);
      }
      u.tune = button(getPhoto(file.path).manual ? 'Resume fine-tuning in LightCraft' : 'Fine-tune in LightCraft', () => openInEditor(false), {class:'line'});
      u.sidebar.panel('adjust').append(u.tune);
      if (getPhoto(file.path).manual) u.aiActions.prepend(el('div',{class:'field'}, el('span',null,'Viewing the local stage'),
        button('View finished manual edit', () => openInEditor(false), {class:'line'})));
      for (const [id,label] of [['isolate','Find objects'],['learn','Show local editing steps'],['gemini','Prepare photo']]) {
        u.sidebar.panel(id).append(button(label, () => { startEditing(); selectSection(id); }, {class:'line'}));
      }
    }
    const side = editor ? u.sidebar.aside : el('aside', { class: 'side pv-side', hidden: true, 'aria-label': 'Edit this photo' });
    const workspace = el('div', { class: editor ? 'pv unified-editor' : 'pv is-viewing' },
      el('div', { class: 'pv-main' }, el('div', { class: 'pv-stage' }, u.busy, u.canvas, u.opening), compareTools(startEditing), under, u.strip = filmstrip()), side);
    root.append(workspace);
    function selectSection(id) {
      if (!me.editing) return;
      if (id !== 'isolate' && me.tools.mode !== 'none') me.tools.setMode('none');
      const view = id === 'learn' ? 'how' : 'edit';
      if (me.view !== view) setView(view);
      if (id === 'isolate') me.tools.openObjects();
      if (id === 'learn') { u.panels.how.hidden = false; once('how'); }
    }
    function startEditing() {
      if (v !== me || me.editing) return;
      me.editing = true; me.list = loadEntries(file.path);
      me.tools = createPhotoTools({ path: file.path, frame: u.frame, displayImage: u.after, request: previewBody, alive: () => v === me, refresh: schedulePreview,
        onMode: (mode, unframed) => {
          me.marking = mode === 'mark'; me.picking = mode === 'neutral';
          if (mode !== 'none' && me.split) { me.split = false; showing(); }
          u.mark.setAttribute('aria-pressed', String(me.marking)); if (u.grey) u.grey.setAttribute('aria-pressed', String(me.picking));
          u.frame.classList.toggle('marking', me.marking || me.picking);
          if (!me.marking) clearPending();
          if (me.unframed !== unframed) { me.unframed = unframed; schedulePreview(); }
        },
      });
      u.panels = { edit: editPanel(), how: howPanel(), try: tryPanel() };
      if (editor) {
        if (u.originalHistory) u.originalHistory.hidden = true;
        u.sidebar.panel('learn').replaceChildren(u.panels.how, u.panels.try);
        u.sidebar.panel('ai').append(u.panels.edit);
        selectSection(u.sidebar.active());
      } else side.append(u.panels.edit, u.panels.how, u.panels.try); under.prepend(me.tools.sourceNote);
      u.editActions.append(me.tools.detailButton());
      u.begin.hidden = true; u.orig.hidden = u.editActions.hidden = under.hidden = side.hidden = false;
      workspace.classList.remove('is-viewing');
      u.frame.setAttribute('aria-label', 'Photo preview. Hold B for the original. With Mark on, Enter places an oval.');
      drawChips(); showing(); paintNeutral(); copyState(); syncEditControls(); me.tools.sync();
      if (!editor) u.frame.focus({ preventScroll: true }); refresh();
    }
    u.before.addEventListener('load', () => { u.opening.hidden = true; });
    u.before.addEventListener('error', () => {
      if (v !== me || !editor) return;
      u.opening.hidden = false; u.opening.textContent = 'Could not open this preview.';
      api('/editor/import', {path:file.path}).catch(error => { if (v === me) u.opening.textContent = error.message; });
    });
    wireFrame(); setThumbs(); showing();
    const active = u.strip.querySelector('[aria-current=true]'); if (active) u.strip.scrollLeft = active.offsetLeft - u.strip.offsetLeft - u.strip.clientWidth / 2 + active.clientWidth / 2;
    me.unsubscribe = subscribeProject(event => {
      if (v !== me || !me.editing || !me.tools || !['change', 'undo', 'redo', 'load'].includes(event.type) || (event.path && event.path !== me.file.path)) return;
      if (editor && event.type === 'undo' && !getPhoto(file.path).ai_enabled && !canUndo(file.path)) {
        go('photo', {index}); return;
      }
      me.draftSliders = null; me.list = loadEntries(me.file.path); if (event.type !== 'load') me.file.dirty = true;
      syncEditControls(); drawChips(); paintNeutral(); copyState(); me.tools.sync();
      u.saved.textContent = 'Changes ready. Save to update the exported photo.'; schedulePreview();
    });
    u.after.addEventListener('load', () => { if (v === me) { fit(); if (me.tools) { me.tools.imageReady(); paintNeutral(); } } });
    me.ro = new ResizeObserver(fit); me.ro.observe(u.canvas);
    me.settingsKey = JSON.stringify(runOptions());
    me.sheetClose = () => {
      if (v !== me) return;
      if (!me.editing) { setThumbs(); return; }
      me.tools.sync(); const key = JSON.stringify(runOptions());
      if (key !== me.settingsKey) { me.settingsKey = key; syncEditControls(); paintNeutral(); copyState(); schedulePreview(); }
    };
    document.getElementById('sheet').addEventListener('close', me.sheetClose);
    me.up = e => { if (e.key.toLowerCase() === 'b') setHold(false); };
    me.blur = () => setHold(false);
    document.addEventListener('keyup', me.up); window.addEventListener('blur', me.blur);
    // A photo that waits for a look opens on its edit and the reason, as the Review queue steps through them.
    const existing = getPhoto(file.path);
    const hasStage = existing.ai_enabled || ['look','intensity','base','edit_steps','sliders','region_settings','mask_refinements','entries','photo_geometry','hide'].some(k =>
      existing[k] !== undefined && (typeof existing[k] !== 'object' || Object.keys(existing[k] || {}).length));
    if ((editor && hasStage) || state.reviewQueue || file.recipeReview || ['review', 'failed'].includes(file.status)) startEditing();
  },
  focus() { if (v) v.u.frame.focus({ preventScroll: true }); },
  unmount() {
    if (!v) return;
    clearTimeout(v.previewTimer); clearInterval(v.busyTimer);
    for (const f of Object.values(v.flights)) if (f.controller) f.controller.abort();
    v.originalAbort?.abort();
    if (v.lookController) v.lookController.abort();
    if (v.unsubscribe) v.unsubscribe();
    if (v.tools) v.tools.dispose();
    document.getElementById('sheet').removeEventListener('close', v.sheetClose);
    if (v.ro) v.ro.disconnect();
    document.removeEventListener('keyup', v.up); window.removeEventListener('blur', v.blur);
    v = null;
  },
  key(e) {
    if (!v || !v.u.say) return false;
    if (e.key === 'Escape' && v.primary && v.u.sidebar.closeDrawer()) return true;
    if (!v.editing) {
      if (e.metaKey || e.ctrlKey || e.altKey || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return false;
      neighbour(e.key === 'ArrowRight' ? 1 : -1); return true;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return true; }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && !(e.target.matches('textarea, input[type=text], input[type=search], [contenteditable]'))) { e.preventDefault(); historyMove(e.shiftKey); return true; }
    if (e.metaKey || e.ctrlKey || e.altKey) return false;
    if (e.key === 'Escape') {
      if (v.pending) { clearPending(); v.u.ghost.hidden = true; }
      else if (v.marking) setMarking(false);
      else if (v.picking) setPicking(false);
      else if (v.tools && v.tools.mode !== 'none') v.tools.setMode('none');
      else if (v.view !== 'edit') setView('edit');
      else return false;
      return true;
    }
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    // the sentence box has the focus on arrival: while it is empty, the arrows still change photo
    if (isTyping(e.target) && !(e.target === v.u.say && !e.target.value && (k === 'ArrowLeft' || k === 'ArrowRight'))) return false;
    if (k === 'ArrowLeft' || k === 'ArrowRight') { neighbour(k === 'ArrowRight' ? 1 : -1); return true; }
    if (k === 'b') { if (!e.repeat) setHold(true); return true; }
    if (k === 'm') { setMarking(!v.marking); return true; }
    return false;
  },
};
