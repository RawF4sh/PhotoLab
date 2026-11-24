// Accepted suggestions become normal, undoable LightCraft slider values on this photo.
const supported = new Set(['light.exposure', 'light.contrast', 'light.highlights', 'light.shadows', 'color.saturation']);

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

export function setupEditorAssist({command, renderThumbnail}) {
  const dialog = element('dialog', '', {id: 'native-assist', 'aria-labelledby': 'assist-heading'});
  const close = element('button', 'Close');
  const heading = element('h2', 'Ask Gemini', {id: 'assist-heading'});
  const bar = element('div', '', {class: 'assist-heading'});
  bar.append(heading, close);
  const prompt = element('textarea', '', {maxlength: '1200', rows: '3', placeholder: 'For example, lift the shadows and keep detail in the clouds'});
  const key = element('input', '', {type: 'password', autocomplete: 'off'});
  const keyField = element('details');
  keyField.append(element('summary', 'API key'), withLabel('Gemini key for this session', key));
  const consent = element('input', '', {type: 'checkbox'});
  const privacy = element('label');
  privacy.append(consent, ' Send a small preview, my request, and current slider values to Gemini');
  const ask = element('button', 'Get suggestions');
  const status = element('p', '', {role: 'status'});
  const list = element('div', '', {class: 'assist-choices'});
  const apply = element('button', 'Apply selected');
  apply.hidden = true;
  dialog.append(bar, withLabel('What would you like to change?', prompt), keyField, privacy, ask, status, list, apply);
  document.body.append(dialog);
  let request = null, review = null, generation = 0;
  const photoId = async () => (await command('library.state')).selection?.active;
  const currentSignature = async id => JSON.stringify(await command('develop.get', {id}));
  const forget = () => { review = null; list.replaceChildren(); apply.hidden = true; };
  function invalidate() {
    generation++; request?.abort(); request = null; forget(); ask.disabled = false;
    status.textContent = '';
  }
  close.onclick = () => dialog.close();
  dialog.addEventListener('close', invalidate);
  prompt.addEventListener('input', invalidate);
  consent.addEventListener('change', invalidate);
  document.querySelector('#ask-gemini').onclick = () => { invalidate(); dialog.showModal(); prompt.focus(); };

  ask.onclick = async () => {
    invalidate();
    if (!prompt.value.trim()) { prompt.focus(); status.textContent = 'Describe the change first.'; return; }
    if (!consent.checked) { consent.focus(); status.textContent = 'Select the preview-sharing option to continue.'; return; }
    const turn = generation, controller = request = new AbortController();
    try {
      ask.disabled = true; status.textContent = 'Preparing the current edit…';
      const id = await photoId();
      if (id == null) throw new Error('Select a photo first.');
      const signature = await currentSignature(id);
      const controls = new Map((await command('develop.controls')).filter(c => supported.has(c.id)).map(c => [c.id, c]));
      const thumbnail = encodePreview(await renderThumbnail(id));
      if (turn !== generation) return;
      if (id !== await photoId() || signature !== await currentSignature(id)) throw new Error('The photo changed. Ask again to see updated suggestions.');
      status.textContent = 'Asking Gemini…';
      const response = await fetch('/api/v1/editor/assist', {method: 'POST', headers: {'Content-Type': 'application/json'},
        signal: controller.signal, body: JSON.stringify({thumbnail, prompt: prompt.value.trim(), allow_cloud: true, gemini_api_key: key.value,
          controls: Object.fromEntries([...controls].map(([name, c]) => [name, c.value]))})});
      const result = await response.json();
      if (turn !== generation) return;
      if (!response.ok || result.status !== 'ok') throw new Error(result.message || result.error || 'Gemini is unavailable.');
      status.textContent = result.proposal.explanation;
      const choices = result.proposal.changes.map(change => {
        const toggle = element('input', '', {type: 'checkbox'}); toggle.checked = true;
        const title = element('strong', change.label), label = element('label'); label.append(toggle, title);
        const values = Object.entries(change.ops).map(([op, delta]) => {
          const control = controls.get(op);
          if (!control) throw new Error('This suggestion requires an unavailable slider.');
          return control.label + ' ' + (delta > 0 ? '+' : '') + delta;
        }).join(', ');
        const row = element('div'); row.append(label, element('p', values), element('p', change.why)); list.append(row);
        toggle.onchange = () => { apply.disabled = !choices.some(c => c.toggle.checked); };
        return {toggle, change};
      });
      review = {id, signature, controls, choices}; apply.hidden = !choices.length; apply.disabled = false;
    } catch (error) { if (turn === generation) { forget(); status.textContent = error.message || String(error); } }
    finally { if (turn === generation) { request = null; ask.disabled = false; } }
  };

  apply.onclick = async () => {
    const selected = review;
    if (!selected || apply.disabled) return;
    apply.disabled = true;
    try {
      const {id, signature, controls, choices} = selected;
      if (id !== await photoId() || signature !== await currentSignature(id)) throw new Error('The edit changed. Get fresh suggestions before applying.');
      if (review !== selected) return;
      const values = {};
      for (const {change, toggle} of choices) if (toggle.checked) {
        for (const [op, delta] of Object.entries(change.ops)) values[op] = (values[op] ?? controls.get(op).value) + delta;
      }
      if (!Object.keys(values).length) return;
      for (const op of Object.keys(values)) {
        const c = controls.get(op); values[op] = Math.min(c.max, Math.max(c.min, values[op]));
      }
      await command('develop.set', {ids: [id], values});
      forget(); status.textContent = 'Applied. You can adjust these sliders or undo the change in the editor.';
    } catch (error) { forget(); status.textContent = error.message || String(error); }
  };
}
