// Source-coordinate object and framing tools; reviewed assistance and native detail.
import { el, api, state, nice, openSheet, toast } from './main.js';
import { getPhoto, updatePhoto } from './project.js';
import { assistantOptions, setAssistantConsent } from './settings.js';

const IMAGE = /^data:image\/(png|webp|jpeg);base64,[A-Za-z0-9+/=]+$/;
const round = n => Math.round(n * 10000) / 10000;
const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
const button = (label, click, attrs = {}) => el('button', { type: 'button', ...attrs, on: { click } }, label);
const valuesOf = photo => photo.sliders === undefined ? state.folderSliders || {} : photo.sliders;
const valueText = (op, value) => value === 0 ? 'No added change' : (value > 0 ? '+' : '') + Number(value.toFixed(2)) + (op === 'exposure' ? ' EV' : '');

export function createPhotoTools({ path, frame, displayImage, request, alive, onMode, refresh }) {
  let mode = 'none', selected = '', finding = '', answer = null, definitions = [], objects = [], decoded = new Map();
  let decodeGeneration = 0, sourceReady = false, stroke = null, cropStart = null, cropDraft = null, aspect = 0;
  let nativeController = null, assistController = null, proposal = null, proposalSignature = '', appliedSuggestion = null, disposed = false;
  const overlay = el('div', { class: 'editor-source-overlay', 'aria-hidden': 'true' });
  const selection = el('div', { class: 'editor-selected-mask' }), brush = el('canvas', { class: 'editor-brush' });
  const crop = el('div', { class: 'editor-crop-box', hidden: true });
  overlay.append(selection, brush, crop); frame.append(overlay);
  const ui = {};
  const active = () => !disposed && alive();
  const sourceMode = () => ['select', 'add', 'subtract', 'crop', 'mark', 'neutral'].includes(mode);
  const currentGeometry = () => getPhoto(path).photo_geometry || { angle: 0, crop: null };
  const photoSignature = () => JSON.stringify(request());
  const note = text => { ui.objectNote.textContent = text; };
  const displayed = () => !!answer && displayImage.complete && displayImage.naturalWidth > 0 && displayImage.src === answer.after;
  const sameView = () => active() && displayed() && (sourceMode() ? sourceReady : answer && (answer.coordinate_space !== 'source' || !answer.result?.geometry));

  function setMode(next) {
    mode = next;
    sourceReady = answer && answer.coordinate_space === 'source' && displayed();
    frame.classList.toggle('source-tool', !['none', 'mark', 'neutral'].includes(mode));
    frame.classList.toggle('brush-tool', ['add', 'subtract'].includes(mode));
    if (!['add', 'subtract'].includes(mode)) { stroke = null; clearBrush(); }
    cropDraft = null; crop.hidden = mode !== 'crop';
    onMode(next, sourceMode()); sync(); paintSelection();
    if (next !== 'none') frame.focus({ preventScroll: true });
    if (sourceMode()) ui.sourceNote.textContent = 'Uncropped source view. Changes use the original photo’s coordinates.';
    else ui.sourceNote.textContent = '';
  }
  function paintSelection() {
    selection.style.maskImage = selection.style.webkitMaskImage = '';
    selection.hidden = !['select', 'add', 'subtract'].includes(mode) || !sourceReady || !selected;
    const object = objects.find(o => o.name === selected);
    if (!selection.hidden && object && IMAGE.test(object.alpha || '')) {
      selection.style.maskImage = selection.style.webkitMaskImage = 'url("' + object.alpha + '")';
    } else selection.hidden = true;
    paintCrop();
  }
  function choose(name) {
    selected = name; if (!sourceMode() || mode === 'crop') setMode('select');
    sync(); paintSelection();
    const object = objects.find(o => o.name === name);
    note(object && object.found !== false ? nice(object.label || object.name) + ' selected. Adjust it or refine its outline.' : 'This object was not found. Choose another detected object.');
  }
  function objectPanel() {
    ui.objectNote = el('p', { class: 'fine', role: 'status' }, 'Objects appear after the first preview.');
    ui.objectModeNote = el('p', { class: 'fine', hidden: true });
    ui.objectName = el('input', { id: 'find_object', type: 'text', maxLength: 120, placeholder: 'e.g. jacket or bouquet', autocomplete: 'off' });
    const find = el('form', { class: 'editor-find-object', on: { submit: event => {
      event.preventDefault(); const raw = ui.objectName.value, name = raw.trim().toLowerCase().replace(/\s+/g, ' ');
      if (!name || name.length > 120 || /[\x00-\x1f\x7f]/.test(raw) || /^(global|__proto__|constructor|prototype)$/.test(name) || /^(mark:|place:)/.test(name)) {
        note('Use a short object name such as jacket or bouquet. Global, mark and place commands are not object names.'); ui.objectName.focus(); return;
      }
      const photo = getPhoto(path), regions = { ...(photo.region_settings || {}) };
      const names = new Set([...Object.keys(regions), ...(photo.base?.selected_regions || []), name]);
      if (names.size > 32) { note('This photo already requests 32 objects. Reset an unused object before finding another.'); return; }
      selected = name; finding = name; if (!Object.hasOwn(regions, name)) regions[name] = {};
      updatePhoto(path, { region_settings: regions }); setMode('select'); ui.objectName.value = name;
      if (objects.some(o => o.name === name)) { finding = ''; paintObjectList(); }
      else note('Finding ' + name + ' in this photo…');
      refresh();
    } } }, el('label', { htmlFor: 'find_object' }, 'Find an object'), el('div', { class: 'editor-find-row' }, ui.objectName, el('button', { type: 'submit', class: 'line' }, 'Find')));
    ui.objectList = el('div', { class: 'editor-object-list', role: 'group', 'aria-label': 'Detected objects' });
    ui.select = button('Choose in photo', () => setMode(mode === 'select' ? 'none' : 'select'), { class: 'line', 'aria-pressed': 'false' });
    ui.add = button('Add to selection', () => setMode('add'), { class: 'line', disabled: true, 'aria-pressed': 'false' });
    ui.subtract = button('Subtract', () => setMode('subtract'), { class: 'line', disabled: true, 'aria-pressed': 'false' });
    ui.radius = el('input', { type: 'range', min: .002, max: .2, step: .002, value: .035, 'aria-label': 'Brush size' });
    ui.radiusValue = el('output', { class: 'num' }, '3.5%');
    ui.radius.addEventListener('input', () => { ui.radiusValue.textContent = (+ui.radius.value * 100).toFixed(1) + '%'; });
    ui.regionSliders = el('div', { class: 'sliders' });
    ui.regionTitle = el('h3', null, 'Choose an object');
    ui.regionControls = {};
    const reset = button('Reset this object', () => {
      if (!selected) return; const p = getPhoto(path), regions = { ...(p.region_settings || {}) }, masks = { ...(p.mask_refinements || {}) };
      delete regions[selected]; delete masks[selected]; selected = finding = ''; updatePhoto(path, { region_settings: regions, mask_refinements: masks });
    }, { class: 'link' });
    ui.objectSection = el('details', { class: 'editor-tool' }, el('summary', null, 'Objects & selections'), ui.objectNote, ui.objectModeNote,
      find, ui.select, ui.objectList, ui.regionTitle, ui.regionSliders,
      el('div', { class: 'editor-history' }, ui.add, ui.subtract),
      el('div', { class: 'sl' }, el('div', { class: 'row' }, el('span', null, 'Brush size'), ui.radiusValue), ui.radius),
      el('p', { class: 'fine' }, 'Paint on the uncropped photo. Each stroke can be undone.'),
      el('div', { class: 'editor-history' }, reset, button('Done selecting', () => setMode('none'), { class: 'line' })));
    return ui.objectSection;
  }
  function regionControls() {
    if (!definitions.length || ui.regionSliders.childNodes.length) return;
    for (const def of definitions) {
      const id = 'region_' + def.op, value = el('output', { class: 'num' });
      const input = el('input', { type: 'range', id, min: def.min, max: def.max, step: def.step, disabled: true, value: 0 });
      input.addEventListener('input', () => { value.textContent = valueText(def.op, +input.value); });
      input.addEventListener('change', () => {
        if (!selected) return;
        const regions = { ...(getPhoto(path).region_settings || {}) }, ops = { ...(regions[selected] || {}) };
        if (+input.value) ops[def.op] = +input.value; else delete ops[def.op];
        regions[selected] = ops;  // Empty controls still keep a requested object available for selection.
        updatePhoto(path, { region_settings: regions });
      });
      ui.regionControls[def.op] = { input, value };
      ui.regionSliders.append(el('div', { class: 'sl' }, el('div', { class: 'row' }, el('label', { htmlFor: id }, def.name || nice(def.op)), value), input));
    }
  }
  function paintObjectList() {
    ui.objectList.replaceChildren();
    for (const object of objects) ui.objectList.append(button(nice(object.label || object.name), () => choose(object.name), {
      class: 'line', 'aria-pressed': String(selected === object.name), disabled: object.found === false,
      title: object.found === false ? 'Not found in this photo' : 'Select ' + nice(object.label || object.name) }));
    const chosen = objects.find(o => o.name === selected);
    if (selected && chosen?.found === false) note(nice(selected) + ' was not found. No selection was invented. Try a clearer name or use an oval mark.');
    else if (selected && chosen?.found !== false && chosen) note(nice(selected) + ' selected. Adjust it or refine its outline.');
    else if (finding) note('Finding ' + finding + ' in this photo…');
    else if (!objects.some(o => o.found !== false)) note('No objects were found. Automatic object selection needs the region finder; whole-photo controls and oval marks still work.');
    else note('Choose a detected object below, or select it on the uncropped photo.');
  }
  function sync() {
    if (!ui.objectList) return;
    if (ui.consent) ui.consent.checked = !!assistantOptions().allow_cloud;
    const p = getPhoto(path), found = objects.some(o => o.name === selected && o.found !== false);
    ui.select.setAttribute('aria-pressed', String(mode === 'select'));
    for (const [name, control] of [['add', ui.add], ['subtract', ui.subtract]]) { control.disabled = !found; control.setAttribute('aria-pressed', String(mode === name)); }
    ui.regionTitle.textContent = selected ? nice(selected) : 'Choose an object';
    for (const [op, controls] of Object.entries(ui.regionControls)) {
      controls.input.disabled = !found; controls.input.value = (p.region_settings || {})[selected]?.[op] || 0;
      controls.value.textContent = valueText(op, +controls.input.value); controls.input.setAttribute('aria-valuetext', controls.value.textContent);
    }
    for (const b of ui.objectList.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.textContent === nice(objects.find(o => o.name === selected)?.label || selected)));
    const geo = currentGeometry(); ui.angle.value = geo.angle || 0; ui.angleValue.textContent = Number(geo.angle || 0).toFixed(1) + '°';
    const box = geo.crop || [0, 0, 1, 1];
    ui.cropNumbers.forEach((input, i) => { input.value = Math.round(box[i] * 1000) / 10; });
    if (proposal && photoSignature() !== proposalSignature) { proposal = null; ui.proposal.replaceChildren(); ui.assistStatus.textContent = 'The photo changed. Ask again for a suggestion based on this edit.'; }
    if (appliedSuggestion && photoSignature() === appliedSuggestion.before) ui.assistStatus.textContent = 'Suggestion undone. Your previous edit is restored.';
    else if (appliedSuggestion && photoSignature() === appliedSuggestion.after) ui.assistStatus.textContent = 'Applied as editable adjustments. Undo restores your previous edit.';
    paintSelection();
  }
  async function decodeObjects(list, generation) {
    const masks = new Map();
    for (const object of list) {
      if (!active() || generation !== decodeGeneration) return;
      if (!IMAGE.test(object.alpha || '') || object.found === false) continue;
      const image = new Image();
      try {
        image.src = object.alpha; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        const context = canvas.getContext('2d', { willReadFrequently: true }); context.drawImage(image, 0, 0);
        masks.set(object.name, { width: canvas.width, height: canvas.height, pixels: context.getImageData(0, 0, canvas.width, canvas.height).data });
      } catch (_) { /* The keyboard list remains available when a mask image cannot be decoded. */ }
    }
    if (active() && generation === decodeGeneration) decoded = masks;
  }
  function acceptPreview(j) {
    if (!active()) return;
    answer = j; sourceReady = j.coordinate_space === 'source' && displayed();
    definitions = j.editor?.sliders || definitions;
    objects = Array.isArray(j.objects) ? j.objects : [];
    if (finding && Object.hasOwn(getPhoto(path).region_settings || {}, finding)) {
      if (!objects.some(o => o.name === finding)) objects.push({ name: finding, label: finding, found: false });
      finding = '';
    }
    const stub = (j.result?.audit?.run_config?.backend || state.finder) === 'stub';
    ui.objectModeNote.hidden = !stub; ui.objectModeNote.textContent = stub ? 'These are approximate demo regions. Precise object outlines need the local model.' : '';
    regionControls(); paintObjectList(); sync();
    decodeObjects(objects, ++decodeGeneration);
    if (sourceMode() && !sourceReady) note('Opening the uncropped photo…');
  }
  function point(event) {
    const rect = frame.getBoundingClientRect(); if (!rect.width || !rect.height) return null;
    return [round(clamp((event.clientX - rect.left) / rect.width, 0, 1)), round(clamp((event.clientY - rect.top) / rect.height, 0, 1))];
  }
  function hit(point) {
    for (const object of objects) {
      const mask = decoded.get(object.name); if (!mask) continue;
      const x = Math.min(mask.width - 1, Math.floor(point[0] * mask.width)), y = Math.min(mask.height - 1, Math.floor(point[1] * mask.height));
      if (mask.pixels[(y * mask.width + x) * 4 + 3] > 32) return object.name;
    }
    return '';
  }
  function clearBrush() { const ctx = brush.getContext('2d'); if (ctx) ctx.clearRect(0, 0, brush.width, brush.height); }
  function paintStroke() {
    if (!stroke) return;
    const rect = frame.getBoundingClientRect(); brush.width = Math.max(1, Math.round(rect.width)); brush.height = Math.max(1, Math.round(rect.height));
    const context = brush.getContext('2d'); context.lineWidth = stroke.radius * Math.min(brush.width, brush.height) * 2;
    context.strokeStyle = stroke.mode === 'add' ? '#54b47aaa' : '#db806faa'; context.lineCap = context.lineJoin = 'round';
    context.beginPath(); const first = stroke.points[0]; context.moveTo(first[0] * brush.width, first[1] * brush.height);
    for (const p of stroke.points) context.lineTo(p[0] * brush.width, p[1] * brush.height);
    if (stroke.points.length === 1) context.lineTo(first[0] * brush.width + .01, first[1] * brush.height); context.stroke();
  }
  function completeStroke() {
    if (!stroke) return;
    const refinements = { ...(getPhoto(path).mask_refinements || {}) }, current = refinements[selected] || [];
    const allStrokes = Object.values(refinements).flat(), total = allStrokes.reduce((n, s) => n + s.points.length, 0);
    if (current.length >= 100 || allStrokes.length >= 256 || new Set([...Object.keys(refinements), selected]).size > 32 || total + stroke.points.length > 8192) { note('This photo reached the brush limit. Undo or reset an object before adding more strokes.'); stroke = null; clearBrush(); return; }
    refinements[selected] = [...current, stroke]; stroke = null; clearBrush(); updatePhoto(path, { mask_refinements: refinements });
  }
  function paintCrop() {
    if (mode !== 'crop' || !sourceReady) { crop.hidden = true; return; }
    const box = cropDraft || currentGeometry().crop || [0, 0, 1, 1]; crop.hidden = false;
    Object.assign(crop.style, { left: box[0] * 100 + '%', top: box[1] * 100 + '%', width: (box[2] - box[0]) * 100 + '%', height: (box[3] - box[1]) * 100 + '%' });
  }
  function normalizedAspect() {
    const source = answer && answer.source, ratio = source ? source.width / source.height : frame.clientWidth / frame.clientHeight;
    return aspect ? aspect / ratio : 0;
  }
  function drawnCrop(start, end) {
    let width = Math.abs(end[0] - start[0]), height = Math.abs(end[1] - start[1]);
    const ratio = normalizedAspect(); if (ratio) { if (width / Math.max(height, .0001) > ratio) width = height * ratio; else height = width / ratio; }
    return [end[0] < start[0] ? start[0] - width : start[0], end[1] < start[1] ? start[1] - height : start[1],
      end[0] < start[0] ? start[0] : start[0] + width, end[1] < start[1] ? start[1] : start[1] + height].map(round);
  }
  function commitCrop(box) {
    if (!box || box[2] - box[0] < .02 || box[3] - box[1] < .02) { ui.cropNote.textContent = 'Keep both sides of the crop at least 2% of the photo.'; return; }
    updatePhoto(path, { photo_geometry: { ...currentGeometry(), crop: box.map(round) } });
    ui.cropNote.textContent = 'Crop saved in the project. Preview framing to see the final edges.';
  }
  function framingPanel() {
    ui.cropNote = el('p', { class: 'fine', role: 'status' }, 'Draw on the original frame, then preview the crop and angle.');
    ui.aspect = el('select', { id: 'crop_aspect', on: { change: e => {
      aspect = +e.target.value;
      if (aspect) {
        const old = currentGeometry().crop || [0, 0, 1, 1], ratio = normalizedAspect();
        let w = old[2] - old[0], h = old[3] - old[1]; if (w / h > ratio) w = h * ratio; else h = w / ratio;
        const cx = (old[0] + old[2]) / 2, cy = (old[1] + old[3]) / 2; commitCrop([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]);
      }
    } } }, ...[['0', 'Free'], ['1', 'Square'], [String(4 / 3), '4:3'], ['1.5', '3:2'], [String(16 / 9), '16:9'], ['.8', '4:5 portrait'], [String(9 / 16), '9:16 portrait']].map(([value, name]) => el('option', { value }, name)));
    ui.angle = el('input', { id: 'crop_angle', type: 'range', min: -15, max: 15, step: .1, value: 0 });
    ui.angleValue = el('output', { class: 'num' }, '0.0°');
    ui.angle.addEventListener('input', () => { ui.angleValue.textContent = Number(ui.angle.value).toFixed(1) + '°'; });
    ui.angle.addEventListener('change', () => updatePhoto(path, { photo_geometry: { ...currentGeometry(), angle: +ui.angle.value } }));
    ui.cropNumbers = ['Left', 'Top', 'Right', 'Bottom'].map((name, i) => el('input', { type: 'number', min: 0, max: 100, step: .1, value: i < 2 ? 0 : 100, 'aria-label': name + ' crop edge in percent',
      on: { change: () => { const values = ui.cropNumbers.map(n => +n.value / 100); if (values.every(Number.isFinite) && values.every(n => n >= 0 && n <= 1)) commitCrop(values); else sync(); } } }));
    return el('details', { class: 'editor-tool' }, el('summary', null, 'Crop & straighten'), ui.cropNote,
      button('Draw crop on photo', () => setMode('crop'), { class: 'line' }),
      el('label', { htmlFor: 'crop_aspect' }, 'Aspect'), ui.aspect,
      el('div', { class: 'editor-crop-edges' }, ...ui.cropNumbers.map((input, i) => el('label', null, ['Left %', 'Top %', 'Right %', 'Bottom %'][i], input))),
      el('div', { class: 'sl' }, el('div', { class: 'row' }, el('label', { htmlFor: 'crop_angle' }, 'Angle'), ui.angleValue), ui.angle),
      el('p', { class: 'fine' }, 'Rotation trims the edges to keep real image pixels. In crop mode, arrow keys move the rectangle; Shift + arrows resize it.'),
      el('div', { class: 'editor-history' }, button('Preview framing', () => setMode('none'), { class: 'line' }),
        button('Reset framing', () => { updatePhoto(path, { photo_geometry: undefined }); ui.aspect.value = '0'; aspect = 0; setMode('none'); }, { class: 'link' })));
  }
  function stop(event) { event.preventDefault(); event.stopImmediatePropagation(); }
  function down(event) {
    if (event.button !== 0 || ['none', 'mark', 'neutral'].includes(mode)) return;
    stop(event); if (!sameView()) { note('Wait for the current view before placing a selection.'); return; }
    const p = point(event); if (!p) return;
    if (mode === 'select') { const name = hit(p); if (name) choose(name); else note(decoded.size ? 'No detected object at this point. Choose from the list or try another area.' : 'The masks are still loading. You can select an object from the list.'); }
    else if (mode === 'detail') openDetail(p);
    else if (mode === 'crop') { cropStart = p; cropDraft = [p[0], p[1], p[0], p[1]]; frame.setPointerCapture(event.pointerId); }
    else if (selected && objects.some(o => o.name === selected && o.found !== false)) { stroke = { mode, radius: +ui.radius.value, points: [p] }; frame.setPointerCapture(event.pointerId); paintStroke(); }
  }
  function move(event) {
    if (!stroke && !cropStart) return; stop(event); const p = point(event); if (!p) return;
    if (cropStart) { cropDraft = drawnCrop(cropStart, p); paintCrop(); }
    else if (stroke.points.length < 512 && Math.hypot(p[0] - stroke.points.at(-1)[0], p[1] - stroke.points.at(-1)[1]) > .001) { stroke.points.push(p); paintStroke(); }
  }
  function up(event) {
    if (!stroke && !cropStart) return; stop(event);
    if (stroke) completeStroke();
    if (cropStart) { const box = cropDraft; cropStart = cropDraft = null; commitCrop(box); paintCrop(); }
  }
  function cancel() { stroke = cropStart = cropDraft = null; clearBrush(); paintCrop(); }
  function keys(event) {
    if (event.target !== frame || ['none', 'mark', 'neutral'].includes(mode)) return;
    if (event.key === 'Escape') { stop(event); cancel(); setMode('none'); return; }
    const delta = { ArrowLeft: [-.01, 0], ArrowRight: [.01, 0], ArrowUp: [0, -.01], ArrowDown: [0, .01] }[event.key];
    if (mode === 'crop' && delta) {
      stop(event); const box = [...(currentGeometry().crop || [.1, .1, .9, .9])];
      if (event.shiftKey) { box[2] = clamp(box[2] + delta[0], box[0] + .02, 1); box[3] = clamp(box[3] + delta[1], box[1] + .02, 1); }
      else { const dx = clamp(delta[0], -box[0], 1 - box[2]), dy = clamp(delta[1], -box[1], 1 - box[3]); box[0] += dx; box[2] += dx; box[1] += dy; box[3] += dy; }
      commitCrop(box); return;
    }
    if (mode === 'select' && delta && objects.length) {
      stop(event); const found = objects.filter(o => o.found !== false), direction = delta[0] + delta[1] > 0 ? 1 : -1;
      if (found.length) choose(found[(Math.max(0, found.findIndex(o => o.name === selected)) + direction + found.length) % found.length].name);
    }
    if (event.key === 'Enter' && mode === 'crop') { stop(event); setMode('none'); }
    else if (event.key === 'Enter' && mode === 'detail') { stop(event); openDetail([.5, .5]); }
  }
  function openDetail(center = [.5, .5]) {
    if (mode !== 'detail') setMode('detail');
    const bodyRequest = request();
    openSheet('Native detail · 100%', (body) => {
      const status = el('p', { class: 'fine', role: 'status' }, 'Rendering full-resolution detail locally…');
      const before = el('img', { alt: 'Original native pixels' }), after = el('img', { alt: 'Edited native pixels' });
      const inputs = center.map((n, i) => el('input', { type: 'number', min: 0, max: 100, step: 1, value: Math.round(n * 100), 'aria-label': (i ? 'Vertical' : 'Horizontal') + ' detail position in percent' }));
      const update = button('Inspect this area', () => render(), { class: 'line' });
      let controller;
      const render = async () => {
        if (controller) controller.abort(); controller = nativeController = new AbortController(); const current = controller;
        const [x, y] = inputs.map(n => +n.value / 100);
        if (![x, y].every(n => Number.isFinite(n) && n >= 0 && n <= 1)) { status.textContent = 'Choose positions between 0 and 100%.'; return; }
        status.textContent = 'Rendering full-resolution detail locally…'; update.disabled = true;
        try {
          const j = await api('/detail', { ...bodyRequest, decider: 'rules', allow_cloud: false, x, y, size: 512 }, { signal: current.signal });
          if (!active() || current !== controller || !body.isConnected) return;
          if (j.scale !== 1 || !IMAGE.test(j.before || '') || !IMAGE.test(j.after || '')) throw new Error('The server did not return native-resolution detail.');
          before.src = j.before; after.src = j.after;
          status.textContent = `${j.crop.width} × ${j.crop.height} native pixels from ${j.full.width} × ${j.full.height}. One image pixel equals one displayed pixel; scroll each pane to inspect its edges.`;
        } catch (e) { if (e.name !== 'AbortError' && active() && current === controller) status.textContent = e.message; }
        finally { if (current === controller) update.disabled = false; }
      };
      body.append(status, el('p', { class: 'fine' }, 'Close this window and click another point in the photo to inspect it. Detail uses the current local controls and final framing.'),
        el('div', { class: 'editor-detail-position' }, el('label', null, 'Across %', inputs[0]), el('label', null, 'Down %', inputs[1]), update),
        el('div', { class: 'editor-native-grid' }, el('section', null, el('h3', null, 'Original'), el('div', { class: 'editor-native-crop' }, before)),
          el('section', null, el('h3', null, 'Edited'), el('div', { class: 'editor-native-crop' }, after))));
      document.getElementById('sheet').addEventListener('close', () => controller?.abort(), { once: true }); render();
    });
  }
  function proposalRows(p) {
    const rows = [];
    const add = (where, ops) => { for (const [op, delta] of Object.entries(ops || {})) if (delta) rows.push(el('li', null, where + ' · ' + (definitions.find(d => d.op === op)?.name || nice(op)) + ' ' + valueText(op, delta))); };
    add('Whole photo', p.global); for (const [name, ops] of Object.entries(p.regions || {})) add(nice(name), ops); return rows;
  }
  function applyProposal() {
    if (!proposal) return;
    if (photoSignature() !== proposalSignature) { proposal = null; ui.proposal.replaceChildren(el('p', { class: 'fine' }, 'Your edit changed. Ask again before applying.')); return; }
    const p = getPhoto(path), global = { ...valuesOf(p) }, regions = JSON.parse(JSON.stringify(p.region_settings || {}));
    const add = (base, proposed) => {
      for (const [op, delta] of Object.entries(proposed || {})) {
        const bounds = definitions.find(d => d.op === op); if (!bounds || !Number.isFinite(delta)) continue;
        const value = round(clamp((base[op] || 0) + delta, bounds.min, bounds.max)); if (value) base[op] = value; else delete base[op];
      }
    };
    add(global, proposal.global);
    for (const [name, ops] of Object.entries(proposal.regions || {})) { const values = regions[name] || {}; add(values, ops); if (Object.keys(values).length) regions[name] = values; }
    const before = proposalSignature; proposal = null; appliedSuggestion = null;
    updatePhoto(path, { sliders: global, region_settings: regions, local_only: true });
    appliedSuggestion = { before, after: photoSignature() };
    ui.assistStatus.textContent = 'Applied as editable adjustments. Undo restores your previous edit.'; ui.proposal.replaceChildren();
  }
  function assistPanel() {
    ui.assistPrompt = el('textarea', { id: 'assist_prompt', rows: 3, maxLength: 1200, placeholder: 'e.g. Keep the snow natural and warm the foreground', 'aria-label': 'Describe the edit you want' });
    ui.consent = el('input', { type: 'checkbox', checked: !!assistantOptions().allow_cloud, on: { change: e => setAssistantConsent(e.target.checked) } });
    ui.assistStatus = el('p', { class: 'fine', role: 'status' }); ui.proposal = el('div', { class: 'editor-proposal' });
    ui.ask = button('Suggest changes', async () => {
      const prompt = ui.assistPrompt.value.trim(); if (!prompt) { ui.assistStatus.textContent = 'Describe what you want to change first.'; ui.assistPrompt.focus(); return; }
      if (!ui.consent.checked) { ui.assistStatus.textContent = 'Allow the small preview to be sent before asking Gemini. Local controls remain available.'; return; }
      if (assistController) assistController.abort(); const controller = assistController = new AbortController();
      const settings = assistantOptions(), start = photoSignature(); ui.ask.disabled = true; ui.assistStatus.textContent = 'Asking Gemini for proposed adjustments…'; proposal = null; appliedSuggestion = null; ui.proposal.replaceChildren();
      try {
        const j = await api('/assist', { ...request(), assist_prompt: prompt, allow_cloud: true,
          ...(settings.api_key ? { gemini_api_key: settings.api_key } : {}) }, { signal: controller.signal });
        if (!active() || controller !== assistController) return;
        if (j.status !== 'ok' || !j.proposal) { ui.assistStatus.textContent = j.message || 'Gemini could not suggest changes. Your local edit is unchanged.'; return; }
        if (photoSignature() !== start) { ui.assistStatus.textContent = 'Your edit changed while Gemini was working. Ask again for the latest version.'; return; }
        proposal = j.proposal; proposalSignature = start; ui.assistStatus.textContent = 'Review the suggestion. Nothing changes until you apply it.';
        const rows = proposalRows(proposal);
        ui.proposal.append(el('p', null, proposal.explanation || ''), el('ul', { class: 'editor-proposal-list' }, ...rows),
          el('div', { class: 'editor-history' }, button('Apply suggestion', applyProposal, { class: 'primary', disabled: !rows.length || !definitions.length }),
            button('Dismiss', () => { proposal = null; ui.proposal.replaceChildren(); ui.assistStatus.textContent = 'Suggestion dismissed. Your edit is unchanged.'; }, { class: 'line' })));
      } catch (e) { if (e.name !== 'AbortError' && active() && controller === assistController) ui.assistStatus.textContent = e.message; }
      finally { if (active() && controller === assistController) ui.ask.disabled = false; }
    }, { class: 'line' });
    return el('details', { class: 'editor-tool' }, el('summary', null, 'Ask Gemini'),
      el('p', { class: 'fine' }, 'Get a suggestion you can inspect and adjust. Your full-resolution photo stays on this computer. Set a Gemini key in Settings, or use the configured environment key.'),
      ui.assistPrompt, el('label', { class: 'check' }, ui.consent, ' Send a small preview and editing measurements to Gemini'), ui.ask, ui.assistStatus, ui.proposal);
  }

  const panels = [objectPanel(), framingPanel(), assistPanel()];
  ui.sourceNote = el('p', { class: 'fine editor-source-note', role: 'status' });
  for (const [name, fn] of [['pointerdown', down], ['pointermove', move], ['pointerup', up], ['pointercancel', cancel], ['keydown', keys]]) frame.addEventListener(name, fn, true);
  return {
    panels, sourceNote: ui.sourceNote, acceptPreview, sync, setMode, sourceMode,
    imageReady() { sourceReady = !!answer && answer.coordinate_space === 'source' && displayed(); paintSelection(); if (sourceReady) paintObjectList(); },
    get mode() { return mode; }, get ready() { return !sourceMode() || sourceReady; },
    detailButton: () => button('Native detail', () => openDetail(), { class: 'line', title: 'Inspect matched original and edited pixels at native resolution' }),
    dispose() {
      disposed = true; decodeGeneration++; nativeController?.abort(); assistController?.abort();
      for (const [name, fn] of [['pointerdown', down], ['pointermove', move], ['pointerup', up], ['pointercancel', cancel], ['keydown', keys]]) frame.removeEventListener(name, fn, true);
    },
  };
}
