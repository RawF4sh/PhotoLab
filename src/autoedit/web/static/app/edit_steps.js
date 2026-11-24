// Bounded, named contributions to the normal manual controls.
import { el, nice, toast } from './main.js';
import { getPhoto, updatePhoto } from './project.js';

export const MAX_EDIT_STEPS = 24;

export function checkStepCapacity(photo, steps) {
  if (steps.length > MAX_EDIT_STEPS) throw Error('A photo can have 24 changes. Remove a change or select fewer.');
  const targets = new Set([...Object.keys(photo.region_settings || {}), ...(photo.base?.selected_regions || [])]);
  for (const step of steps) {
    if (step.on && step.amount > 0) Object.keys(step.regions).forEach(name => targets.add(name));
  }
  if (targets.size > 32) throw Error('A photo can use 32 objects. Remove an unused object or switch off a change first.');
}

// Selection uses the index in the reviewed response, including gaps left by unchecked rows.
export function selectedChanges(photo, response, selected) {
  const steps = [...(photo.edit_steps || [])];
  for (const [index, change] of response.proposal.changes.entries()) {
    const id = response.id + '-' + index;
    if (!selected.has(index) || steps.some(step => step.id === id)) continue;
    steps.push({ id, label: change.label, origin: 'suggested', on: true, amount: 1,
      global: change.target === 'photo' ? change.ops : {},
      regions: change.target === 'photo' ? {} : { [change.target]: change.ops },
      ...(change.why ? { note: change.why } : {}), ref: response.model });
  }
  checkStepCapacity(photo, steps);
  return steps;
}

const percent = value => Math.round(100 * value) + '%';

function adjustments(step) {
  const describe = (name, ops) => Object.entries(ops).map(([op, offset]) => {
    const value = Number((offset * step.amount).toFixed(4));
    return name + ' · ' + nice(op) + ' ' + (value > 0 ? '+' : '') + value + (op === 'exposure' ? ' EV' : '');
  });
  return [...describe('Whole photo', step.global),
    ...Object.entries(step.regions).flatMap(([name, ops]) => describe(nice(name), ops))].join(', ');
}

// Keep inputs mounted through preview refreshes, so a keyboard or pointer adjustment retains focus.
export function changesPanel(path) {
  const list = el('ul', { class: 'editor-step-list' });
  const limits = el('p', { class: 'fine', role: 'status', hidden: true });
  const panel = el('section', { class: 'field', hidden: true, 'aria-label': 'Changes' },
    el('h3', null, 'Changes'), list, limits);
  const rows = new Map();
  function modify(id, patch) {
    const photo = getPhoto(path), current = photo.edit_steps || [];
    const steps = patch ? current.map(step => step.id === id ? { ...step, ...patch } : step) : current.filter(step => step.id !== id);
    try { checkStepCapacity(photo, steps); }
    catch (error) { sync(); toast(error.message); return; }
    updatePhoto(path, { edit_steps: steps.length ? steps : undefined });
  }
  function makeRow(id) {
    const enabled = el('input', { type: 'checkbox', on: { change: e => modify(id, { on: e.target.checked }) } });
    const title = el('span'), origin = el('small'), summary = el('small'), reason = el('p', { class: 'fine' });
    const missing = el('p', { class: 'fine', hidden: true });
    const amount = el('input', { type: 'range', min: 0, max: 2, step: .05 });
    const value = el('output', { class: 'num' });
    const paint = () => { value.textContent = percent(+amount.value); amount.setAttribute('aria-valuetext', value.textContent); };
    amount.addEventListener('input', paint);
    amount.addEventListener('change', () => modify(id, { amount: +amount.value }));
    const remove = el('button', { type: 'button', class: 'link', on: { click: () => modify(id, null) } }, 'Remove');
    const element = el('li', { class: 'editor-step' },
      el('div', { class: 'row' }, el('label', { class: 'check' }, enabled, title), remove),
      origin, summary, reason, el('label', { class: 'editor-step-amount' }, 'Amount ', value, amount), missing);
    return { element, enabled, title, origin, summary, reason, missing, amount, remove, paint };
  }
  function sync(preview = null) {
    const steps = getPhoto(path).edit_steps || [];
    panel.hidden = steps.length === 0;
    const found = preview ? new Set((preview.objects || []).filter(o => o.found).map(o => o.name)) : null;
    const present = new Set(steps.map(s => s.id));
    for (const [id, row] of rows) if (!present.has(id)) { row.element.remove(); rows.delete(id); }
    for (const [i, step] of steps.entries()) {
      const row = rows.get(step.id) || makeRow(step.id);
      rows.set(step.id, row);
      if (list.children[i] !== row.element) list.insertBefore(row.element, list.children[i] || null);
      row.enabled.checked = step.on;
      row.title.textContent = step.label;
      row.origin.textContent = step.origin === 'recipe' ? 'Recipe' : 'Suggested';
      row.summary.textContent = adjustments(step);
      row.reason.textContent = step.note || ''; row.reason.hidden = !step.note;
      row.amount.setAttribute('aria-label', 'Amount for ' + step.label);
      row.amount.disabled = !step.on;
      if (!row.amount.matches(':active')) row.amount.value = step.amount;
      row.paint(); row.remove.setAttribute('aria-label', 'Remove ' + step.label);
      const missingNames = found && step.on && step.amount ? Object.keys(step.regions).filter(name => !found.has(name)) : [];
      row.missing.textContent = missingNames.length ? missingNames.map(nice).join(', ') + ' not found in this photo.' : '';
      row.missing.hidden = !missingNames.length;
    }
    const clamped = preview?.editor?.clamped || [];
    limits.hidden = !clamped.length;
    limits.textContent = clamped.map(item => (item.target === 'global' ? 'Whole photo' : nice(item.target)) +
      ' · ' + nice(item.op) + ' is at its limit (' + item.value + ').').join(' ');
  }
  return { panel, sync };
}
