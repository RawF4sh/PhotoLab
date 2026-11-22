// The Tools screen (#/tools) and the helpers every tool form uses: runTool, reveal, showResult.
// Forms come from small tables below; Looks, Your style and Calibrate are the three that need their own code.
import { el, api, state, register, setBar, backButton, gearButton, toast, joinPath, nice } from './main.js';

const sleep = ms => new Promise(r => setTimeout(r, ms));
export const fileManager = /Win/i.test(navigator.platform || '') ? 'Explorer' : 'Finder';

/* ---- jobs ------------------------------------------------------------------------------------ */
// Poll a job route every 500 ms until it stops running. Resolves with its result, throws its error.
async function pollJob(path, onLog) {
  for (;;) {
    const j = await api(path);
    if (onLog && j.log && j.log.length) onLog(j.log[j.log.length - 1]);
    if (['error', 'failed', 'cancelled'].includes(j.status)) throw new Error(j.error || 'The job failed.');
    if (!['running', 'queued', 'pending'].includes(j.status)) return j.result === undefined || j.result === null ? j : j.result;
    await sleep(500);
  }
}

export async function runTool(name, body, onLog) {
  const { id } = await api('/tools/' + name, body || {});
  return pollJob('/tools/jobs/' + id, onLog);
}

export const reveal = path => runTool('reveal', { path });
const revealBtn = (path, label) => el('button', { type: 'button', class: 'line',
  on: { click: () => reveal(path).catch(e => toast(e.message)) } }, label || 'Show in ' + fileManager);

/* ---- results --------------------------------------------------------------------------------- */
const PATH_KEYS = ['out', 'out_dir', 'html', 'page', 'report', 'path', 'folder'];

// A few lines of counts and paths, a button to show the output, and the full answer under "Details".
export function showResult(out, result, path, btn) {
  const r = result || {}, lines = [];
  for (const [k, v] of Object.entries(r)) {
    if (v === null || v === '' || lines.length >= 6) continue;
    if (Array.isArray(v)) lines.push(nice(k) + ': ' + v.length);
    else if (typeof v !== 'object' && String(v).length < 140) lines.push(nice(k) + ': ' + v);
  }
  const p = path || PATH_KEYS.map(k => r[k]).find(v => typeof v === 'string' && v);
  out.replaceChildren(el('div', { class: 'tsum' }, ...lines.map(l => el('div', null, l))),
    p ? revealBtn(p, btn) : null,
    el('details', null, el('summary', null, 'Details'), el('pre', null, JSON.stringify(result, null, 2))));
}

/* ---- forms ----------------------------------------------------------------------------------- */
let seq = 0;
function control(f) {
  const id = 'tf' + (++seq), dflt = typeof f.default === 'function' ? f.default() : f.default;
  let input;
  if (f.type === 'bool') input = el('input', { type: 'checkbox', id, checked: !!dflt });
  else if (f.type === 'choice') {
    input = el('select', { id }, ...f.choices.map(c => el('option', { value: c }, nice(c))));
    input.value = dflt === undefined ? f.choices[0] : dflt;
  } else input = el('input', { type: f.type === 'number' ? 'number' : 'text', id, value: dflt === undefined ? '' : String(dflt) });
  const get = () => f.type === 'bool' ? input.checked : f.type === 'number' ? (input.value === '' ? undefined : Number(input.value)) : input.value.trim();
  const kind = f.type === 'folder' ? 'folder' : f.pick;   // POST /pick: folder, file (photos) or json (any file)
  const pick = kind ? el('button', { type: 'button', class: 'line', on: { click: async () => {
    try { const r = await api('/pick?kind=' + kind, {}); if (r.path) input.value = r.path; } catch (e) { toast(e.message); }
  } } }, 'Choose…') : null;
  const node = f.type === 'bool'
    ? el('div', { class: 'tfield check' }, input, el('label', { htmlFor: id }, f.label))
    : el('div', { class: 'tfield' }, el('label', { htmlFor: id }, f.label), el('span', { class: 'tin' }, input, pick),
      f.hint ? el('small', null, f.hint) : null);
  return { node, get, key: f.key };
}

const dirField = (key = 'folder', text = 'Folder') => ({ key, label: text, type: 'folder', default: () => state.dir });

// spec: {title, tool | run(vals, onLog), fields, primary, quiet?, body?(vals), path?(vals, result), then?(vals, result, out)}
// One filled button per tool: forms beyond the first on a screen set quiet.
function form(spec) {
  const cs = spec.fields.map(control), status = el('small', { class: 'tstatus', role: 'status' });
  const out = el('div', { class: 'tout' });
  const go_ = el('button', { type: 'button', class: spec.quiet ? 'line' : 'primary' }, spec.primary);
  go_.addEventListener('click', async () => {
    const vals = {};
    cs.forEach(c => { const v = c.get(); if (v !== '' && v !== undefined && !(typeof v === 'number' && Number.isNaN(v))) vals[c.key] = v; });
    go_.disabled = true; status.textContent = 'Working…'; out.replaceChildren();
    try {
      const onLog = m => { status.textContent = m; };
      const r = spec.run ? await spec.run(vals, onLog) : await runTool(spec.tool, spec.body ? spec.body(vals) : vals, onLog);
      status.textContent = '';
      showResult(out, r, spec.path && spec.path(vals, r));
      if (spec.then) out.append(spec.then(vals, r));
    } catch (e) { status.textContent = e.message; status.classList.add('bad'); go_.disabled = false; return; }
    status.classList.remove('bad'); go_.disabled = false;
  });
  return el('section', { class: 'tform' }, spec.title ? el('h3', null, spec.title) : null, ...cs.map(c => c.node), go_, status, out);
}

/* ---- the tables ------------------------------------------------------------------------------ */
const pickForm = () => form({ title: '', tool: 'pick', primary: 'Pick keepers',
  fields: [dirField(), { key: 'copy', label: 'Copy the keepers into AutoEdit Picks', type: 'bool', default: true }],
  path: v => joinPath(v.folder, 'AutoEdit Picks') });

const mergeForm = () => form({ tool: 'merge', primary: 'Merge frames',
  fields: [dirField(), { key: 'mode', label: 'What to merge', type: 'choice', choices: ['auto', 'average', 'brackets', 'focus', 'trails'] },
    { key: 'dry_run', label: 'Only show what would be merged', type: 'bool' }],
  path: v => v.dry_run ? '' : joinPath(v.folder, 'AutoEdit Merged') });

const subsetForm = () => form({ primary: 'Choose photos',
  fields: [dirField(), { key: 'count', label: 'How many', type: 'number', default: 50 },
    { key: 'mode', label: 'Choose', type: 'choice', choices: ['diverse', 'best'] }],
  run: async (v, onLog) => {
    const j = await api('/curate/jobs', { input_dir: v.folder, count: v.count, mode: v.mode });
    return pollJob('/curate/jobs/' + j.id, onLog);
  } });

function looksNode() {
  const list = el('div', { class: 'tlist' }), note = el('div', { class: 'tout' });
  const act = async (tool, body, onDone) => {
    note.textContent = 'Working…';
    try { const r = await runTool(tool, body, m => { note.textContent = m; }); showResult(note, r); if (onDone) onDone(); }
    catch (e) { note.textContent = e.message; }
  };
  const load = async () => {
    try {
      const { looks } = await runTool('looks_list', {});
      list.replaceChildren(...looks.map(l => el('div', { class: 'trow2' },
        el('span', { class: 'grow' }, nice(l.name), ' ', el('small', null, l.origin)),
        ...[['Export', () => act('looks_export', { name: l.name, out: joinPath(state.dir, l.name + '.aelook') })],
          ['LUT', () => act('looks_lut', { name: l.name, out: joinPath(state.dir, l.name + '.cube') })],
          ['Card', () => act('looks_card', { name: l.name, out: joinPath(state.dir, 'AutoEdit Looks'),
            photos: state.files.filter(f => f.checked).slice(0, 4).map(f => f.path) })],
          ...(l.origin === 'profile' ? [['Delete', () => { if (confirm('Delete the look "' + l.name + '"?')) act('looks_delete', { name: l.name }, load); }]] : [])]
          .map(([t, fn]) => el('button', { type: 'button', class: 'link', on: { click: fn } }, t)))));
    } catch (e) { list.textContent = e.message; }
  };
  load();
  return el('div', null, list, note,
    form({ title: 'Import a look', tool: 'looks_import', primary: 'Import', quiet: true,
      fields: [{ key: 'file', label: 'Look file (.aelook)', type: 'text', pick: 'json' }], then: () => { load(); return el('span'); } }),
    form({ title: 'Save a new look', tool: 'looks_save', primary: 'Save look',
      fields: [{ key: 'base', label: 'Start from', type: 'text', default: 'natural' },
        { key: 'name', label: 'Name (lowercase letters, digits, underscores)', type: 'text' }], then: () => { load(); return el('span'); } }));
}

function styleNode() {
  const root = el('div', null, el('small', null, 'Reading…'));
  (async () => {
    try {
      const [ov, hist] = await Promise.all([api('/style/suggestions'), runTool('history_status', {})]);
      const name = el('input', { type: 'text', placeholder: 'name_for_the_look', 'aria-label': 'Name for the new look' });
      const note = el('small');
      const save = el('button', { type: 'button', class: 'primary', on: { click: async () => {
        try { const r = await api('/style/accept', { name: name.value.trim() }); note.textContent = 'Saved the look ' + (r.name || name.value.trim()) + '.'; }
        catch (e) { note.textContent = e.message; }
      } } }, 'Save as a look');
      const sugg = ov.suggestions || [];
      root.replaceChildren(
        el('p', null, ov.on ? 'Learned from ' + ov.photos + ' photos over ' + ov.days + ' days.' : 'History is off. Turn on "Keep a history of your edits" in Settings › Output to learn your style.'),
        ...sugg.map(s => el('div', { class: 'trow2' }, el('span', null, s.ask || s.fact || s.id))),
        sugg.length ? el('div', { class: 'tin' }, name, save) : null, note,
        el('div', { class: 'trow2' }, el('small', { class: 'grow' }, 'History file: ' + hist.photos + ' photos'),
          el('button', { type: 'button', class: 'line', on: { click: async () => {
            if (!confirm('Clear the history of your edits? This cannot be undone.')) return;
            try { await runTool('history_clear', {}); toast('History cleared.'); } catch (e) { toast(e.message); }
          } } }, 'Clear history')));
    } catch (e) { root.textContent = e.message; }
  })();
  return root;
}

const calibrateForm = () => form({ tool: 'calibrate_start', primary: 'Start calibrating',
  fields: [dirField('photos', 'Photos'), { key: 'out', label: 'Work folder', type: 'text', default: () => joinPath(state.dir, 'AutoEdit Calibration') },
    { key: 'name', label: 'Name of your look', type: 'text', default: 'calibrated' }],
  path: () => '',
  then: (v, r) => {
    const out = el('div', { class: 'tout' }), step = (finish) => async () => {
      try { showResult(out, await runTool('calibrate_advance', { out: v.out, photos: v.photos, finish }), finish ? '' : r.folder); }
      catch (e) { out.textContent = e.message; }
    };
    return el('div', null, el('p', null, 'Open the vote page, choose the version you prefer in each pair, save the page, then come back and press Next round. Finish writes your look from the votes so far.'),
      el('div', { class: 'trow2' }, revealBtn(r.folder, 'Open the vote page'),
        el('button', { type: 'button', class: 'line', on: { click: step(false) } }, 'Next round'),
        el('button', { type: 'button', class: 'line', on: { click: step(true) } }, 'Finish')), out);
  } });

const trainingNode = () => el('div', null,
  form({ title: 'Rank variants', tool: 'rank_variants', primary: 'Rank', quiet: true, fields: [{ key: 'input', label: 'Photo or folder', type: 'folder', default: () => state.dir }] }),
  form({ title: 'Train a recipe model', tool: 'train_recipe', primary: 'Train', quiet: true,
    fields: [{ key: 'dataset', label: 'Dataset folder', type: 'folder' }, { key: 'out_model', label: 'Save model as (.json)', type: 'text' }] }),
  form({ title: 'Check a dataset', primary: 'Check', quiet: true, run: v => api('/datasets/validate', v),
    fields: [{ key: 'originals_dir', label: 'Originals', type: 'folder' }, { key: 'targets_dir', label: 'Edited versions', type: 'folder' },
      { key: 'recursive', label: 'Include subfolders', type: 'bool' }] }),
  form({ title: 'Build a dataset', primary: 'Build', quiet: true, run: v => api('/datasets/build', v),
    fields: [{ key: 'originals_dir', label: 'Originals', type: 'folder' }, { key: 'targets_dir', label: 'Edited versions', type: 'folder' },
      { key: 'out_dir', label: 'Put it in (optional)', type: 'folder' }] }),
  form({ title: 'Train a scorer', primary: 'Train', quiet: true,
    run: async (v, onLog) => { const j = await api('/aesthetic/train', v); return pollJob('/aesthetic/jobs/' + j.id, onLog); },
    fields: [{ key: 'images_dir', label: 'Finished photos', type: 'folder' }, { key: 'labels_csv', label: 'Labels file (.csv, optional)', type: 'text', pick: 'json' },
      { key: 'out_model', label: 'Save scorer as (.json, optional)', type: 'text' }] }),
  form({ title: 'Score a folder', primary: 'Score', quiet: true, run: v => api('/aesthetic/score', v),
    fields: [dirField(), { key: 'model', label: 'Scorer file (.json)', type: 'text', pick: 'json' }] }));

const TOOLS = [
  ['Pick keepers', 'the sharpest well-exposed frame of each burst', pickForm],
  ['Merge frames', 'brackets, repeats, focus series, trails', mergeForm],
  ['Choose a subset', 'copy a varied or best-scored set for review', subsetForm],
  ['Looks', 'save, import, export, card, LUT', looksNode],
  ['Your style', 'changes you keep making', styleNode],
  ['Calibrate', 'find your look from pairs you vote on', calibrateForm],
  ['Training (advanced)', 'datasets, scorers, recipe model', trainingNode],
];

export const screen = {
  mount(root) {
    setBar(backButton('Folder', 'folder'), el('span', { class: 'title grow' }, 'Tools'), gearButton());
    root.append(el('div', { class: 'toolscreen' }, ...TOOLS.map(([title, sub, build]) => {
      const body = el('div', { class: 'tbody', hidden: true });
      const head = el('button', { type: 'button', class: 'trow', 'aria-expanded': 'false', on: { click: () => {
        if (!body.childNodes.length) body.append(build());
        body.hidden = !body.hidden; head.setAttribute('aria-expanded', String(!body.hidden));
      } } }, el('span', null, title), el('small', { class: 'grow' }, sub), el('span', { 'aria-hidden': 'true' }, '›'));
      return el('div', { class: 'titem' }, head, body);
    })));
  },
};

// Registered after main.js has run (it owns the screen table); a #/tools reload lands on Start anyway, as no folder is open.
queueMicrotask(() => register('tools', screen));
