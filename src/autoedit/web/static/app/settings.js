// Settings sheet, drawn from GET /settings/schema. A new key in the schema gets a control with no change here.
import { el, api, store, state, getSchema, openSheet, toast } from './main.js';
import { runTool } from './tools.js';
import { folderSettingsChanged } from './project.js';

// Set on the Folder panel (or in a fixed group below), so the sheet has no row for them.
const ELSEWHERE = new Set(['look', 'genre', 'intensity', 'edit', 'export', 'backend', 'neutral', 'wb']);
// Settings that name a file on this computer, with the picker kind that fits (POST /pick: folder, file = photos, json = any file).
const PICK = { reference: 'file', watermark_logo: 'file', lut: 'json', recipe_model: 'json' };
const GROUP_NOTE = { llm: 'Off by default. When on, only a small preview and measurements leave this computer, never the full photo.' };

// Cloud suggestions are requested explicitly, and their credentials die with this page.
const assistantSession = { api_key: '', allow_cloud: false };
export const assistantOptions = () => ({ ...assistantSession });
export const setAssistantConsent = allowed => { assistantSession.allow_cloud = allowed === true; };
export const setAssistantKey = key => { assistantSession.api_key = key.trim(); };

function assistantSection() {
  const enabled = el('input', { type: 'checkbox', id: 'assistant-enabled', checked: assistantSession.allow_cloud,
    on: { change: e => { assistantSession.allow_cloud = e.target.checked; } } });
  const key = el('input', { type: 'password', id: 'assistant-key', value: assistantSession.api_key, maxLength: 256,
    autocomplete: 'off', placeholder: 'Use a server key, or paste your key for this session',
    on: { input: e => setAssistantKey(e.target.value) } });
  return el('details', { class: 'settings-section' }, el('summary', null, 'Gemini suggestions'),
    el('label', { htmlFor: 'assistant-enabled', class: 'assistant-consent' }, enabled, ' Allow a small preview and editing measurements to be sent to Google when I request a suggestion'),
    el('label', { htmlFor: 'assistant-key' }, 'Gemini API key'), key,
    el('small', null, 'This key lasts until the page closes. Google may bill your API usage.'),
    el('button', { type: 'button', class: 'link', on: { click: () => { assistantSession.api_key = ''; assistantSession.allow_cloud = false; key.value = ''; enabled.checked = false; } } }, 'Clear key and turn off'));
}

export function applySurround() { document.documentElement.classList.toggle('dark-surround', !!store.get('dark', false)); }

// Everything a job or preview needs: changed settings, the finder, then the Folder panel's choices.
export function runOptions() {
  const o = { ...state.settings, backend: state.finder };
  if (state.look) o.look = state.look;
  if (state.genre) o.genre = state.genre;
  if (state.strength !== null) o.intensity = state.strength;
  if (state.runText.trim()) o.edit = state.runText.trim();
  if (state.exportPreset) o.export = state.exportPreset;
  return o;
}

function keep(s, v) {   // hold a value only while it differs from the engine's default
  const same = v === '' || v === null || (Number.isNaN(v)) || v === s.default || (s.type === 'bool' && !v && !s.default);
  if (same) delete state.settings[s.key]; else state.settings[s.key] = v;
  store.set('settings', state.settings); folderSettingsChanged();
}

function control(s) {
  const cur = state.settings[s.key];
  const val = cur === undefined ? s.default : cur;
  const id = 'set_' + s.key;
  if (s.type === 'bool') {
    return el('input', { type: 'checkbox', id, checked: !!val, on: { change: e => keep(s, e.target.checked) } });
  }
  if (s.type === 'choice') {
    const sel = el('select', { id, on: { change: e => keep(s, e.target.value) } },
      el('option', { value: '' }, 'Default'), ...(s.choices || []).map(c => el('option', { value: c }, c.replaceAll('_', ' '))));
    sel.value = cur === undefined ? '' : cur;
    return sel;
  }
  if (s.type === 'float' || s.type === 'int') {
    const num = x => (s.type === 'int' ? parseInt(x, 10) : parseFloat(x));
    const ranged = s.min != null && s.max != null && s.default != null;
    const input = el('input', { type: ranged ? 'range' : 'number', id });
    if (s.min != null) input.min = s.min;
    if (s.max != null) input.max = s.max;
    input.step = s.type === 'int' ? 1 : ranged ? (s.max - s.min) / 100 : 'any';
    input.value = val === null || val === undefined ? '' : val;
    const out = el('small', { class: 'num' }, ranged ? String(input.value) : '');
    input.addEventListener('input', () => { out.textContent = ranged ? input.value : ''; keep(s, num(input.value)); });
    return el('span', { class: 'ctl' }, input, out);
  }
  if (s.type === 'list') {   // text, comma separated
    return el('input', { type: 'text', id, value: Array.isArray(val) ? val.join(', ') : (val || ''), on: { change: e => keep(s, e.target.value.trim()) } });
  }
  const input = el('input', { type: 'text', id, value: val || '', on: { change: e => keep(s, e.target.value.trim()) } });
  if (!PICK[s.key]) return input;
  const choose = el('button', { type: 'button', class: 'line', on: { click: async () => {
    try { const r = await api('/pick?kind=' + PICK[s.key], {}); if (r.path) { input.value = r.path; keep(s, r.path); } } catch (e) { toast(e.message); }
  } } }, 'Choose…');
  return el('span', { class: 'ctl' }, input, choose);
}

function row(s) {
  const field = control(s);
  const helpId = 'help_' + s.key;
  if (s.help) (field.id ? field : field.querySelector('input')).setAttribute('aria-describedby', helpId);
  return el('div', { class: 'srow' }, el('label', { htmlFor: 'set_' + s.key }, s.label), field,
    s.help ? el('span', { id: helpId, class: 'help', hidden: s.key !== 'hide_method' }, s.help) : null);
}

const FINDER = { auto: 'Auto (SAM 3 when it is installed)', sam3: 'SAM 3', stub: 'Stub (no model)' };

// The SAM 3 weights: what is on this computer, a download (never started by itself) and a remove.
function modelRow() {
  const text = el('span', null, 'Reading…'), box = el('div', { class: 'modelrow' }, el('strong', null, 'Model'), text);
  const mb = n => (n / 1e6 >= 1000 ? (n / 1e9).toFixed(1) + ' GB' : Math.round(n / 1e6) + ' MB');
  const paint = async () => {
    try {
      const m = await runTool('models_status', {});
      text.textContent = m.cached ? 'Installed, ' + mb(m.size_bytes) : 'Not installed';
      const bits = [];
      if (!m.cached) bits.push(el('button', { type: 'button', class: 'line', on: { click: async e => {
        e.target.disabled = true; text.textContent = 'Downloading…';
        try { await runTool('models_download', {}, l => { text.textContent = l; }); } catch (err) { toast(err.message); }
        paint();
      } } }, 'Download SAM 3 (needs the licence accepted on Hugging Face)'));
      else bits.push(el('button', { type: 'button', class: 'line', on: { click: async () => {
        if (!confirm('Remove the SAM 3 model from this computer?')) return;
        try { await runTool('models_remove', {}); } catch (err) { toast(err.message); }
        paint();
      } } }, 'Remove'));
      box.replaceChildren(el('strong', null, 'Model'), text, ...bits);
    } catch (e) { text.textContent = e.message; }
  };
  paint();
  return box;
}

function finderSection(by) {
  const b = by.backend || {};
  return el('section', null, el('fieldset', null, el('legend', null, 'Region finder'),
    ...(b.choices || ['auto', 'sam3', 'stub']).map(c => el('label', null,
      el('input', { type: 'radio', name: 'finder', value: c, checked: state.finder === c,
        on: { change: () => { state.finder = c; store.set('finder', c); folderSettingsChanged(); } } }), ' ' + (FINDER[c] || c)))),
    modelRow());
}

function appearanceSection(descriptions) {
  return el('section', null, el('h3', null, 'Appearance'),
    el('label', null, el('input', { type: 'checkbox', checked: !!store.get('dark', false),
      on: { change: e => { store.set('dark', e.target.checked); applySurround(); } } }), ' Dark surround'),
    el('label', null, el('input', { type: 'checkbox', checked: !!store.get('labels', false),
      on: { change: e => store.set('labels', e.target.checked) } }), ' Save photo ratings for training'),
    el('label', null, descriptions, ' Show setting descriptions'));
}

// The local instruction reader (a small model on this computer), in plain words.
function readerLine() {
  const line = el('small', null, 'Reading instructions on this computer: checking…');
  api('/translator/status').then(t => {
    line.textContent = 'Reading instructions on this computer: ' + (t.available ? 'ready (' + t.model + '), nothing leaves this computer' : 'not available, so only the built-in word list reads your sentences');
  }).catch(() => { line.textContent = ''; });
  return line;
}

function aboutSection() {
  const line = el('small', null, 'Reading…');
  api('/version').then(v => { line.textContent = 'Engine ' + v.engine + ' · API ' + v.api; }).catch(() => { line.textContent = ''; });
  const pre = el('pre', { class: 'about-text', hidden: true });
  const check = el('button', { type: 'button', class: 'line', on: { click: async () => {
    check.disabled = true; pre.hidden = false; pre.textContent = 'Checking…';
    try { pre.textContent = (await runTool('doctor', {})).text; } catch (e) { pre.textContent = e.message; }
    check.disabled = false;
  } } }, 'Check this computer');
  return el('section', null, el('h3', null, 'About'), line, check, pre);
}

export async function openSettings() {
  let sc;
  try { sc = await getSchema(); } catch (e) { toast('Settings could not be read: ' + e.message); return; }
  const by = Object.fromEntries((sc.settings || []).map(s => [s.key, s]));
  openSheet('Settings', (body, close) => {
    const descriptions = el('input', { type: 'checkbox' });
    body.append(appearanceSection(descriptions), assistantSection());
    const setup = el('details', { class: 'settings-section' }, el('summary', null, 'Object selection setup'), finderSection(by));
    body.append(setup);
    for (const g of sc.groups || []) {
      const rows = (sc.settings || []).filter(s => s.group === g.key && !ELSEWHERE.has(s.key));
      if (!rows.length) continue;
      const adv = rows.filter(s => s.advanced);
      body.append(el('details', { class: 'settings-section' }, el('summary', null, g.label),
        GROUP_NOTE[g.key] ? el('small', null, GROUP_NOTE[g.key]) : null,
        g.key === 'llm' ? readerLine() : null,
        ...rows.filter(s => !s.advanced).map(row),
        adv.length ? el('details', null, el('summary', null, 'More'), ...adv.map(row)) : null));
    }
    const optionalHelp = body.querySelectorAll('.srow .help[hidden]');
    descriptions.addEventListener('change', () => {
      for (const hint of optionalHelp) hint.hidden = !descriptions.checked;
    });
    body.append(el('section', null,
      el('button', { type: 'button', class: 'line', on: { click: () => {
        state.settings = {}; assistantSession.api_key = ''; assistantSession.allow_cloud = false;
        store.set('settings', {}); folderSettingsChanged(); close(); toast('Settings are back to the defaults.');
      } } }, 'Reset all settings')),
      aboutSection(),
      el('button', { type: 'button', class: 'primary', on: { click: close } }, 'Done'));
  });
}
