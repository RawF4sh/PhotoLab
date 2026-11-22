// Photo screen: preview, hold to compare, marks, chips, Masks, How it was done, Try it yourself, Save this photo.
// Ported from the Lab (lab.html, F38); request bodies are the Lab's. Text is never parsed as markup.
import { el, api, state, store, nice, thumbUrl, go, toast, getSchema, setBar, backButton, gearButton } from './main.js';
import { runOptions } from './settings.js';
import { runTool } from './tools.js';
import { exportsDir } from './share.js';

const MAX_TEXT = 2000, MAX_MARKS = 20, MIN_SIDE = 0.01;
const DATA = /^data:image\/(webp|jpeg|png);base64,[A-Za-z0-9+/=]+$/, PNG = /^data:image\/png;base64,[A-Za-z0-9+/=]+$/;
const SAY = 'Say what to change, e.g. warm the grass a little';
const SAY_OVAL = 'What should change in the oval? e.g. brighten this a little';
const STATUS_WORD = { applied: 'applied', clamped: 'held back in part', blocked: 'held back' };

const over = Object.create(null);   // per photo path: {look, strength}; kept while the page is open
let v = null;                       // the open screen, null when away

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
  if (op === 'exposure' || op === 'shadows_ev') return (x < 0 ? '-' : '+') + Math.abs(x).toFixed(2) + ' EV';
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
  const l = store.get('entries', {})[path]; if (!Array.isArray(l)) return [];
  const ok = []; let marks = 0;
  for (const e of l) {
    const s = typeof e === 'string' && e.trim() !== '' && e.length <= MAX_TEXT;
    if (!s && !okMark(e)) continue;
    const c = s ? e : { box: e.box, kind: e.kind, edit: e.edit };
    if (s ? textOf([...ok, c]).length > MAX_TEXT : ++marks > MAX_MARKS) continue;
    ok.push(c);
  }
  return ok;
}
function saveEntries() {
  const all = { ...store.get('entries', {}) };
  if (v.list.length) all[v.file.path] = v.list; else delete all[v.file.path];
  store.set('entries', all);
}

/* ---- the request ----------------------------------------------------------------------------- */
function options() {   // the Folder's merge, then this photo's own look and strength
  const o = runOptions(), p = over[v.file.path] || {};
  if (p.look !== undefined) { if (p.look) o.look = p.look; else delete o.look; }
  if (p.strength !== undefined) o.intensity = p.strength;
  if (p.neutral) o.neutral = p.neutral;   // F34: X,Y as shares of the frame
  return o;
}
function previewBody() {
  const b = { file: v.file.path, ...options() }, t = textOf(v.list), m = marksOf(v.list);
  if (t) b.photo_edit = t;
  if (m.length) b.photo_marks = m;
  return b;
}
const BODIES = {
  edit: () => previewBody(),
  how: () => ({ ...previewBody(), steps: true }),
  try: () => ({ ...previewBody(), try: { sliders: { ...v.sliders }, compare: v.compare } }),
};
const SHOW = {
  edit: j => { v.answer = j; if (v.view === 'edit') paintEdit(); },
  how: j => { v.how = j; v.step = 0; if (v.view === 'how') paintHow(); paintMasks(); },
  try: j => { v.tried = j; if (v.view === 'try') paintTry(); },
};
async function once(kind) {   // per view: one request in flight, the latest state wins
  const me = v, f = me.flights[kind] ||= { busy: false, again: false, shown: '' }, key = JSON.stringify(BODIES[kind]());
  if (key === f.shown) return;
  if (f.busy) { f.again = true; return; }
  f.busy = true; setBusy(1);
  try {
    const j = await api('/preview', JSON.parse(key));
    if (v === me && JSON.stringify(BODIES[kind]()) === key) { f.shown = key; SHOW[kind](j); }
  } catch (e) { if (v === me && JSON.stringify(BODIES[kind]()) === key) say(e.message, 'bad'); }
  if (v !== me) return;
  f.busy = false; setBusy(-1);
  if (f.again) { f.again = false; once(kind); }
}
function refresh() { once(v.view); if (v.masks && v.view !== 'how') once('how'); }
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
  const s = Math.min((canvas.clientWidth - 32) / w, (canvas.clientHeight - 32) / h);
  frame.style.width = Math.max(1, Math.floor(w * s)) + 'px'; frame.style.height = Math.max(1, Math.floor(h * s)) + 'px';
}
function setImg(img, s) { const p = pic(s); if (p) img.src = p; }
function setThumbs() { const t = thumbUrl(v.file.thumb, 1600); v.u.before.src = t; v.u.after.src = t; }
function showing() {
  const engine = v.view === 'try' && v.compare && v.which === 'engine' && v.tried && v.tried.compare;
  const s = v.hold ? 'before' : engine ? 'engine' : 'after', u = v.u;
  u.frame.dataset.showing = s;
  u.tag.hidden = s === 'after';
  u.tag.textContent = s === 'before' ? (v.view === 'how' ? 'Before this step' : 'Original') : "AutoEdit's edit";
  u.orig.setAttribute('aria-pressed', String(v.hold));
}
function setHold(on) { if (!v || v.hold === on) return; v.hold = on; showing(); }
function tint(list, color) {
  const t = v.u.tint; t.replaceChildren();
  (list || []).forEach(o => {
    const a = o && o.alpha; if (typeof a !== 'string' || !PNG.test(a)) return;
    const d = el('div', { class: 'tl' }), u = 'url("' + a + '")';
    d.style.backgroundColor = color; d.style.maskImage = u; d.style.webkitMaskImage = u; t.append(d);
  });
}
function paintMasks() { if (v.masks && v.how) tint(v.how.mask_overlays, 'var(--accent)'); else tint([]); }
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
  v.marking = on; v.u.mark.setAttribute('aria-pressed', String(on));
  if (on && v.picking) setPicking(false);
  v.u.frame.classList.toggle('marking', v.marking || v.picking);
  if (!on) clearPending();
}
/* ---- the grey point (F34) -------------------------------------------------------------------- */
function paintNeutral() {
  const p = (over[v.file.path] || {}).neutral, u = v.u;
  u.greyChip.hidden = u.greyDot.hidden = !p;
  if (p) { u.greyDot.style.left = p[0] * 100 + '%'; u.greyDot.style.top = p[1] * 100 + '%'; }
}
function setNeutral(p) {
  const o = over[v.file.path] ||= {};
  if (p) o.neutral = p; else delete o.neutral;
  paintNeutral(); refresh();
}
function setPicking(on) {
  v.picking = on; v.u.grey.setAttribute('aria-pressed', String(on));
  if (on && v.marking) setMarking(false);
  v.u.frame.classList.toggle('marking', v.marking || v.picking);
}
function wireFrame() {   // drag draws an oval while marking; otherwise holding the photo shows the original
  const f = v.u.frame; let a = null;
  const pt = e => {
    const r = f.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return null;
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const boxOf = (p, q) => [Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.max(p[0], q[0]), Math.max(p[1], q[1])];
  f.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    if (v.picking) {
      const p = pt(e); if (p) { e.preventDefault(); setNeutral(p.map(n => Math.round(n * 1e4) / 1e4)); setPicking(false); }
      return;
    }
    if (!v.marking) { setHold(true); return; }
    const p = pt(e); if (!p) return;
    e.preventDefault(); clearPending(); a = p;
    try { f.setPointerCapture(e.pointerId); } catch (_) { /* a synthetic pointer */ }
  });
  f.addEventListener('pointermove', e => {
    if (!a) return; const q = pt(e); if (!q) return;
    placeOval(v.u.pend, boxOf(a, q)); v.u.pend.hidden = false;
  });
  f.addEventListener('pointerup', e => {
    setHold(false); if (!a) return;
    const q = pt(e) || a, box = boxOf(a, q).map(n => Math.round(n * 1e4) / 1e4); a = null;
    if (box[2] - box[0] < MIN_SIDE || box[3] - box[1] < MIN_SIDE) clearPending(); else setPending(box);
  });
  f.addEventListener('pointercancel', () => { setHold(false); if (a) { a = null; clearPending(); } });
  f.addEventListener('pointerleave', () => { if (!a) setHold(false); });
  f.addEventListener('keydown', e => {   // with Mark on: Enter places an oval, arrows move it, Shift + arrows resize it, Enter confirms
    if (!v.marking || e.metaKey || e.ctrlKey || e.altKey) return;
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
  const prev = v.view; v.view = name; setHold(false);
  const u = v.u;
  u.which.hidden = name !== 'try' || !(v.tried && v.tried.compare);
  u.panels.edit.hidden = name !== 'edit'; u.panels.how.hidden = name !== 'how'; u.panels.try.hidden = name !== 'try';
  if (name === 'edit') paintEdit(); else if (name === 'how') paintHow(); else paintTry();
  (name === 'edit' ? u.navs[prev] || u.say : u.panels[name].querySelector('h2')).focus(); showing(); refresh();
}
function paintEdit() {
  const j = v.answer, u = v.u;
  if (!j) setThumbs(); else { setImg(u.before, j.before); setImg(u.after, j.after); metaLine(j); }
  u.before.alt = 'Original'; u.after.alt = 'Edited photo'; u.engine.removeAttribute('src');
  paintMasks(); drawChips(); showing();
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
  (j.steps || []).forEach((s, i) => st.append(el('li', null, button((i + 1) + '. ' + (s.title || s.kind), () => chooseStep(i)))));
  chooseStep(Math.min(v.step, Math.max(0, (j.steps || []).length - 1)));
}
function chooseStep(i) {
  const j = v.how, steps = (j && j.steps) || []; if (!steps.length) return;
  v.step = Math.max(0, Math.min(steps.length - 1, i)); const s = steps[v.step], u = v.u;
  setImg(u.after, s.after); setImg(u.before, (steps[v.step - 1] || s).after);
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
  if (s.moved != null) d.append(el('p', null, 'Moved ' + Math.round(s.moved * 10) / 10 + ' levels of 255 on average'));
  showing();
}

/* ---- try it yourself ------------------------------------------------------------------------- */
function paintTry() {
  const j = v.tried, u = v.u;
  if (!j) { setThumbs(); drawChips(); showing(); return; }
  setImg(u.before, j.before); setImg(u.after, j.after); u.after.alt = 'Edited photo'; u.before.alt = 'Original'; tint([]);
  const box = u.tSliders;
  if (!box.childNodes.length) ((j.try || {}).sliders || []).forEach(s => {
    const id = 'sl_' + s.op, val = el('span', { class: 'num' }), r = el('input', { type: 'range', id, min: s.min, max: s.max, step: s.step });
    r.dataset.op = s.op;
    const set = x => { const t = fmtVal(s.op, +x); val.textContent = t; r.setAttribute('aria-valuetext', t); };
    r.value = v.sliders[s.op] || 0; set(r.value);
    const teach = () => { const L = u.tLesson; L.replaceChildren(el('strong', null, s.name || s.op)); lines(L, s); };
    r.addEventListener('input', () => set(r.value)); r.addEventListener('focus', teach); r.addEventListener('pointerdown', teach);
    r.addEventListener('change', () => { const n = +r.value; if (n) v.sliders[s.op] = n; else delete v.sliders[s.op]; set(r.value); refresh(); });
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
async function save() {
  const me = v, b = previewBody(), u = me.u; if (u.save.disabled) return;
  u.save.disabled = true; u.saved.className = 'fine'; u.saved.textContent = 'Saving…'; u.saved.removeAttribute('title');
  try {
    const j = await api('/run-one', b), out = String(j.image_out || ''), name = out.split(/[\\/]/).pop() || 'the photo';
    toast('Saved ' + name + ' in AutoEdit Exports');
    me.file.exported = out; if (v === me) copyState();
    if (v === me) { u.saved.textContent = 'Saved to ' + (out || 'AutoEdit Exports'); u.saved.title = out; }
  } catch (e) { if (v === me) { u.saved.className = 'bad'; u.saved.textContent = e.message; } else toast(e.message); }
  if (v === me) u.save.disabled = false;
}

/* ---- copy to similar ------------------------------------------------------------------------- */
// The sync tool reads the exported file's sidecar, so it needs this photo saved first. It holds the server's slow-job lock,
// so nothing runs until the button is pressed: the first press counts the similar photos, the Copy it offers does the copy.
function copyState() { const c = v.u.copy, ok = !!v.file.exported; c.disabled = !ok; c.title = ok ? '' : 'Save this photo first'; }
async function copyAsk() {
  const me = v, u = me.u, target = me.file.exported; if (!target) return;
  u.copy.disabled = true; u.copyRes.textContent = 'Looking for similar photos…';
  try {
    const n = ((await runTool('sync', { target, dry_run: true })).targets || []).length;
    if (v !== me) return;
    u.copyRes.replaceChildren(...(n ? [el('div', null, 'Copy to ' + n + ' similar ' + (n === 1 ? 'photo' : 'photos') + '?'),
      el('div', { class: 'btnrow' }, button('Copy', () => copyNow(target), { class: 'line' }), button('Not now', () => u.copyRes.replaceChildren(), { class: 'link' }))]
      : [el('div', null, 'No similar photos found.')]));
  } catch (e) { if (v === me) u.copyRes.textContent = e.message; }
  if (v === me) u.copy.disabled = false;
}
async function copyNow(target) {
  const me = v, u = me.u;
  u.copy.disabled = true; u.copyRes.textContent = 'Working…';
  try {
    const r = await runTool('sync', { target, dry_run: false }, m => { if (v === me) u.copyRes.textContent = m; });
    if (v === me) u.copyRes.replaceChildren(...(r.lines || ['Done.']).map(l => el('div', null, String(l))));
  } catch (e) { if (v === me) u.copyRes.textContent = e.message; }
  if (v === me) u.copy.disabled = false;
}

/* ---- the panel ------------------------------------------------------------------------------- */
function editPanel() {
  const path = v.file.path, u = v.u;
  const setOver = patch => { over[path] = { ...over[path], ...patch }; };
  const lookSel = el('select', { id: 'look', on: { change: e => { setOver({ look: e.target.value }); refresh(); } } }, el('option', { value: '' }, 'Default'));
  const range = el('input', { type: 'range', id: 'strength', min: 0, max: 2, step: 0.05 });
  const out = el('span', { class: 'value num' }), reset = button('Reset', () => {
    delete over[path].strength; range.value = state.strength === null ? dflt : state.strength; paint(); refresh();
  }, { class: 'link', hidden: true });
  let dflt = 0.8;
  const cur = () => (over[path] && over[path].strength !== undefined ? over[path].strength : state.strength === null ? dflt : state.strength);
  const paint = () => { out.textContent = Number(range.value).toFixed(2); reset.hidden = !(over[path] && over[path].strength !== undefined); };
  range.addEventListener('input', paint);
  range.addEventListener('change', () => { setOver({ strength: Number(range.value) }); paint(); refresh(); });
  range.value = cur(); paint();
  getSchema().then(sc => {
    if (v !== u.owner) return;
    const by = Object.fromEntries((sc.settings || []).map(s => [s.key, s]));
    (by.look && by.look.choices || []).forEach(n => lookSel.add(new Option(nice(n), n)));
    const want = over[path] && over[path].look !== undefined ? over[path].look : state.look;
    if (want && ![...lookSel.options].some(o => o.value === want)) lookSel.add(new Option(nice(want), want));
    lookSel.value = want;
    if (by.intensity) { range.min = by.intensity.min; range.max = by.intensity.max; dflt = by.intensity.default; }
    range.value = cur(); paint();
  }).catch(e => toast('Settings could not be read: ' + e.message));

  u.chips = el('ul', { class: 'pchips' }); u.probs = el('div', { class: 'probs' });
  u.changes = el('div', { class: 'field' }, el('h3', null, 'Your changes'), u.chips);
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
  return el('div', { class: 'panel' },
    el('div', { class: 'field' }, el('label', { htmlFor: 'look' }, 'Look'), lookSel),
    el('div', { class: 'field' }, el('div', { class: 'row' }, el('label', { htmlFor: 'strength' }, 'Strength'), out, reset), range),
    el('div', { class: 'field' }, u.grey, u.greyChip),
    u.changes, u.probs,
    u.copy, u.copyRes,
    el('div', { class: 'field' }, nav('How it was done', 'how'), nav('Try it yourself', 'try'),
      el('label', { class: 'check' }, u.masksBox, ' Masks')),
    u.review,
    el('div', { class: 'field' }, u.save, u.saved));
}
function howPanel() {
  const u = v.u;
  u.hChanges = el('ol', { class: 'plist' }); u.hSteps = el('ol', { class: 'plist' }); u.hDetail = el('div', { class: 'detail' });
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

/* ---- the screen ------------------------------------------------------------------------------ */
const neighbour = d => { const i = v.index + d; if (i >= 0 && i < state.files.length) go('photo', { index: i }); };

export const screen = {
  mount(root, { index }) {
    const file = state.files[index];
    if (!file) {
      setBar(backButton('Folder', 'folder'), el('span', { class: 'grow' }), gearButton());
      root.append(el('p', { class: 'fine pv-gone' }, 'That photo is not in this folder.')); return;
    }
    const u = {}, me = v = { index, file, u, view: 'edit', list: loadEntries(file.path), flights: {}, answer: null, how: null, tried: null,
      step: 0, sliders: {}, compare: false, which: 'yours', hold: false, marking: false, picking: false, pending: null, masks: false,
      busy: 0, scene: '', busyTimer: 0, busyFrom: 0, timer: 0 };
    u.owner = me;
    u.title = el('span', { class: 'title' });
    u.orig = el('button', { type: 'button', 'aria-pressed': 'false', title: 'Hold B to see the original', on: { click: () => setHold(!v.hold) } }, 'Original');
    const n = state.files.length;
    setBar(backButton('Folder', 'folder'), u.title, el('span', { class: 'grow' }),
      el('button', { type: 'button', 'aria-label': 'Previous photo', disabled: index === 0, on: { click: () => neighbour(-1) } }, '‹'),
      el('button', { type: 'button', 'aria-label': 'Next photo', disabled: index >= n - 1, on: { click: () => neighbour(1) } }, '›'),
      u.orig, gearButton());
    title();

    u.busy = el('div', { class: 'pv-busy', hidden: true });
    u.before = el('img', { alt: 'Original' }); u.after = el('img', { alt: 'Edited photo' }); u.engine = el('img', { alt: "AutoEdit's edit" });
    u.tint = el('div', { class: 'pv-tint' });
    u.ghost = el('div', { class: 'oval', hidden: true }); u.pend = el('div', { class: 'oval pend', hidden: true });
    u.greyDot = el('div', { class: 'pv-grey', hidden: true }); u.secs = el('span', { 'aria-hidden': 'true' });
    u.tag = el('div', { class: 'pv-tag', hidden: true });
    u.frame = el('div', { class: 'pv-frame', role: 'img', tabIndex: 0, 'aria-label': 'Photo. Hold B to see the original. With Mark on, Enter places an oval.', data: { showing: 'after' } },
      u.before, u.after, u.engine, u.tint, el('div', { class: 'pv-ovals' }, u.ghost, u.pend, u.greyDot), u.tag);
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
    u.panels = { edit: editPanel(), how: howPanel(), try: tryPanel() };
    root.append(el('div', { class: 'pv' },
      el('div', { class: 'pv-main' }, el('div', { class: 'pv-stage' }, u.busy, u.canvas),
        el('div', { class: 'pv-under' }, el('div', { class: 'pv-line' }, u.status, u.which), form)),
      el('aside', { class: 'side pv-side', 'aria-label': 'Edit this photo' }, u.panels.edit, u.panels.how, u.panels.try)));

    wireFrame(); setThumbs(); drawChips(); showing(); paintNeutral(); copyState();
    u.after.addEventListener('load', fit);
    me.ro = new ResizeObserver(fit); me.ro.observe(u.canvas);
    me.up = e => { if (e.key.toLowerCase() === 'b') setHold(false); };
    me.blur = () => setHold(false);
    document.addEventListener('keyup', me.up); window.addEventListener('blur', me.blur);
    me.timer = setTimeout(refresh, 250);
  },
  focus() { if (v) v.u.say.focus(); },
  unmount() {
    if (!v) return;
    clearTimeout(v.timer); clearInterval(v.busyTimer);
    if (v.ro) v.ro.disconnect();
    document.removeEventListener('keyup', v.up); window.removeEventListener('blur', v.blur);
    v = null;
  },
  key(e) {
    if (!v || !v.u.say) return false;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return true; }
    if (e.metaKey || e.ctrlKey || e.altKey) return false;
    if (e.key === 'Escape') {
      if (v.pending) { clearPending(); v.u.ghost.hidden = true; }
      else if (v.marking) setMarking(false);
      else if (v.picking) setPicking(false);
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
