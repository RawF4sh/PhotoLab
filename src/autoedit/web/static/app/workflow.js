import { el } from './main.js';
import { runTool } from './tools.js';

export function workflowSteps(active = 'open') {
  const steps = [['open', 'Open'], ['improve', 'Improve'], ['adjust', 'Adjust'], ['export', 'Export']];
  return el('ol', { class: 'workflow-steps', 'aria-label': 'Editing workflow' }, ...steps.map(([key, title], index) =>
    el('li', { class: key === active ? 'current' : '', 'aria-current': key === active ? 'step' : undefined },
      el('span', { class: 'step-number', 'aria-hidden': 'true' }, String(index + 1)),
      el('strong', null, title))));
}

export function readinessCard(onSetup) {
  const detail = el('span', { role: 'status' }, 'Checking object selection…');
  const card = el('div', { class: 'readiness-card' },
    el('strong', null, 'Local editing ready'), detail,
    el('button', { type: 'button', class: 'link', on: { click: onSetup } }, 'Selection setup'));
  runTool('models_status', {}).then(model => {
    if (!card.isConnected) return;
    const ready = model.cached && model.torch && model.transformers && model.hub;
    detail.textContent = ready ? 'Object selection ready' : 'Precise selection needs setup';
  }).catch(() => { if (card.isConnected) detail.textContent = 'Selection status unavailable'; });
  return card;
}
