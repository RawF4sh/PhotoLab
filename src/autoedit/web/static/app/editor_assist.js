// Accepted suggestions become normal, undoable LightCraft slider values on this photo.
function element(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function withLabel(text, input) {
  const label = element('label', text);
  label.append(input);
  return label;
}

function encodePreview(source) {
  const scale = Math.min(1, 512 / Math.max(source.width, source.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(source.width * scale));
  canvas.height = Math.max(1, Math.round(source.height * scale));
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.82).split(',')[1];
}

function sliderName(control) {
  const [, band, axis] = control.id.split('.');
  if (control.id.startsWith('mixer.')) {
    const channels = {hue: 'hue', sat: 'saturation', lum: 'luminance'};
    return band[0].toUpperCase() + band.slice(1) + ' ' + channels[axis];
  }
  return control.label;
}

// LightCraft's rendered-file panel displays temperature on a relative scale.
// Its command API stores it as Kelvin. Camera RAWs can use a different white
// from the stored settings (as-shot, legacy calibration or a named preset).
function photoControls(photo, native) {
  const controls = native.filter(c => /^(light|color|effects|mixer)\./.test(c.id) && Number.isFinite(c.value));
  if (photo.kind !== 'image' || photo.source?.type !== 'file') return controls;
  const wb = photo.develop?.wb;
  if (!wb || !(wb.temp > 0) || !Number.isFinite(wb.tint)) return controls;
  const relative = (1e6 / 6500 - 1e6 / wb.temp) / 0.8;
  const asShot = wb.mode === 'asShot';
  const panel = {min: -100, max: 100, decimals: 0};
  controls.push(
    {...panel, id: 'wb.tempRel', label: 'Temperature', value: asShot ? 0 : Math.max(-100, Math.min(100, relative))},
    {...panel, id: 'wb.tint', label: 'Tint', value: asShot ? 0 : Math.max(-100, Math.min(100, wb.tint))});
  return controls;
}

function selectedValues({choices, controls}) {
  const deltas = new Map();
  for (const {toggle, change} of choices) if (toggle.checked) {
    for (const [name, delta] of Object.entries(change.ops)) deltas.set(name, (deltas.get(name) || 0) + delta);
  }
  const values = {};
  for (const [name, delta] of deltas) {
    const c = controls.get(name);
    const clamped = Math.min(c.max, Math.max(c.min, c.value + delta));
    const value = Number(clamped.toFixed(c.decimals));
    if (value !== Number(c.value.toFixed(c.decimals))) values[name] = value;
  }
  return values;
}

export function setupEditorAssist({command, renderThumbnail, onApplied}) {
  const dialog = element('dialog', '', {id: 'native-assist', 'aria-labelledby': 'assist-heading'});
  const close = element('button', 'Back to photo');
  const heading = element('h2', 'Ask Gemini', {id: 'assist-heading'});
  const bar = element('div', '', {class: 'assist-heading'});
  bar.append(heading, close);
  const style = element('select');
  style.append(element('option', 'Standard', {value: 'standard'}));
  const prompt = element('textarea', '', {maxlength: '800', rows: '2', placeholder: 'Keep the foreground cool'});
  const key = element('input', '', {type: 'password', autocomplete: 'off'});
  const keyField = element('details');
  keyField.append(element('summary', 'Gemini API key'), withLabel('Key for this session', key));
  const consent = element('input', '', {type: 'checkbox'});
  const privacy = element('label');
  privacy.append(consent, ' Send a small preview, my editing request and slider values to Gemini.');
  const ask = element('button', 'Suggest edits', {class: 'ai-primary'});
  const setup = element('div', '', {class: 'assist-setup'});
  setup.append(withLabel('Edit style', style), withLabel('Additional direction (optional)', prompt),
    keyField, privacy, ask);
  const revise = element('button', 'Change request');
  revise.hidden = true;
  const status = element('p', '', {role: 'status'});
  const local = element('button', 'Use local auto instead');
  local.hidden = true;
  local.onclick = () => { dialog.close(); document.querySelector('#auto').click(); };
  const list = element('div', '', {class: 'assist-choices'});
  const totals = element('dl', '', {class: 'assist-values', 'aria-label': 'Selected slider changes'});
  const apply = element('button', 'Apply selected edit', {class: 'ai-primary'});
  apply.hidden = true;
  dialog.append(bar, setup, status, local, list, totals, apply, revise);
  document.body.append(dialog);

  let request = null, review = null, generation = 0, catalog = null;
  const photoId = async () => (await command('library.state')).selection?.active;
  const currentSignature = async id => JSON.stringify(await command('develop.get', {id}));
  function forget() {
    review = null; list.replaceChildren(); totals.replaceChildren(); apply.hidden = true;
    revise.hidden = true; setup.hidden = false; heading.textContent = 'Ask Gemini';
  }
  function invalidate() {
    generation++; request?.abort(); request = null; forget(); ask.disabled = false;
    local.hidden = true; status.textContent = '';
  }
  async function loadStyles() {
    const response = await fetch('/api/v1/ai-edits');
    if (!response.ok) throw new Error('Styles unavailable');
    const {styles, has_server_key} = await response.json();
    style.replaceChildren(...styles.map(s => element('option', s.label, {value: s.id})));
    style.value = 'standard';
    if (has_server_key === false && !key.value) keyField.open = true;
  }
  close.onclick = () => dialog.close();
  revise.onclick = () => { invalidate(); style.focus(); };
  dialog.addEventListener('close', invalidate);
  prompt.addEventListener('input', invalidate);
  key.addEventListener('input', invalidate);
  style.addEventListener('change', invalidate);
  consent.addEventListener('change', invalidate);
  document.querySelector('#ask-gemini').onclick = () => {
    invalidate(); dialog.showModal(); style.focus();
    if (!catalog) catalog = loadStyles().catch(() => {
      catalog = null;
      if (dialog.open && !request && !review) status.textContent = 'Only Standard is available. Reopen Ask Gemini to retry loading styles.';
    });
  };

  function refreshTotals() {
    if (!review) return;
    const values = selectedValues(review);
    totals.replaceChildren();
    for (const [name, next] of Object.entries(values)) {
      const c = review.controls.get(name);
      totals.append(element('dt', sliderName(c)), element('dd', Number(c.value.toFixed(c.decimals)) + ' → ' + next));
    }
    apply.disabled = !Object.keys(values).length;
  }

  ask.onclick = async () => {
    invalidate();
    if (!consent.checked) { consent.focus(); status.textContent = 'Select the preview-sharing option to continue.'; return; }
    const turn = generation, controller = request = new AbortController();
    try {
      ask.disabled = true; status.textContent = 'Preparing the current edit…';
      const id = await photoId();
      if (id == null) throw new Error('Select a photo first.');
      const signature = await currentSignature(id);
      const photo = await command('photo.inspect', {id});
      const sliders = photoControls(photo, await command('develop.controls'));
      const controls = new Map(sliders.map(c => [c.id, c]));
      const thumbnail = encodePreview(await renderThumbnail(photo));
      if (id !== await photoId() || signature !== await currentSignature(id)) throw new Error('The photo changed. Generate a new edit.');
      if (turn !== generation) return;
      status.textContent = 'Asking Gemini…';
      const response = await fetch('/api/v1/editor/assist', {method: 'POST', headers: {'Content-Type': 'application/json'},
        signal: controller.signal, body: JSON.stringify({thumbnail, style: style.value, prompt: prompt.value.trim(),
          allow_cloud: true, gemini_api_key: key.value, controls: Object.fromEntries([...controls].map(([name, c]) => [name, c.value]))})});
      const result = await response.json();
      if (turn !== generation) return;
      if (!response.ok || result.status !== 'ok') {
        local.hidden = result.status !== 'unavailable';
        if (result.reason === 'missing_key') keyField.open = true;
        throw new Error(result.message || result.error || 'Gemini is unavailable.');
      }
      status.textContent = result.proposal.explanation;
      const choices = result.proposal.changes.map(change => {
        if (Object.keys(change.ops).some(op => !controls.has(op))) throw new Error('This suggestion requires an unavailable slider.');
        const toggle = element('input', '', {type: 'checkbox'}); toggle.checked = true;
        const label = element('label'); label.append(toggle, element('strong', change.label));
        const row = element('div'); row.append(label, element('p', change.why)); list.append(row);
        toggle.onchange = refreshTotals;
        return {toggle, change};
      });
      review = {id, signature, controls, choices}; apply.hidden = !choices.length;
      setup.hidden = true; revise.hidden = false;
      heading.textContent = style.selectedOptions[0].textContent + ' edit';
      refreshTotals();
      if (choices.length && apply.disabled) status.textContent += ' Your sliders are already at these values.';
      (apply.disabled || apply.hidden ? revise : apply).focus();
    } catch (error) { if (turn === generation) { forget(); status.textContent = error.message || String(error); } }
    finally { if (turn === generation) { request = null; ask.disabled = false; } }
  };

  apply.onclick = async () => {
    const selected = review;
    if (!selected || apply.disabled) return;
    const values = selectedValues(selected);
    apply.disabled = true;
    try {
      const {id, signature} = selected;
      if (id !== await photoId() || signature !== await currentSignature(id)) throw new Error('The edit changed. Generate a fresh AI edit before applying.');
      if (review !== selected) return;
      if ('wb.tempRel' in values) {
        values['wb.temp'] = 1e6 / (1e6 / 6500 - values['wb.tempRel'] * 0.8);
        delete values['wb.tempRel'];
      }
      await command('develop.set', {ids: [id], values});
      if (review !== selected) return;
      dialog.close();
      onApplied();
    } catch (error) { if (review === selected) { forget(); status.textContent = error.message || String(error); } }
  };
}
