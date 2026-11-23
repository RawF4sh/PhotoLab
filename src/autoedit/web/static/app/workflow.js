import { el } from './main.js';
import { runTool } from './tools.js';

export function workflowSteps(active = 'open') {
  const steps = [['open', 'Open', 'Choose a photo or a folder'], ['improve', 'Improve', 'Start with an automatic edit'],
    ['adjust', 'Adjust', 'Refine the whole photo or one object'], ['export', 'Export', 'Save a new copy; keep your original']];
  return el('ol', { class: 'workflow-steps', 'aria-label': 'Editing workflow' }, ...steps.map(([key, title, text], index) =>
    el('li', { class: key === active ? 'current' : '', 'aria-current': key === active ? 'step' : undefined },
      el('span', { class: 'step-number', 'aria-hidden': 'true' }, String(index + 1)),
      el('div', null, el('strong', null, title), el('small', null, text)))));
}

export function readinessCard(onSetup) {
  const detail = el('small', { role: 'status' }, 'Checking local object selection…');
  const card = el('div', { class: 'readiness-card' }, el('div', null,
    el('strong', null, 'Local editing is ready'),
    el('small', null, 'Automatic adjustments, sliders, crop and export work without a cloud account.'), detail),
    el('button', { type: 'button', class: 'link', on: { click: onSetup } }, 'Selection setup'));
  runTool('models_status', {}).then(model => {
    if (!card.isConnected) return;
    const ready = model.cached && model.torch && model.transformers && model.hub;
    detail.textContent = ready ? 'Object selection model is installed and runs on this computer.'
      : 'Precise object selection needs the local model. You can start editing before setting it up.';
  }).catch(() => { if (card.isConnected) detail.textContent = 'Object selection setup can be checked in Settings.'; });
  return card;
}
