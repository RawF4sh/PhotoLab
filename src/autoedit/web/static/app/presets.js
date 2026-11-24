// Recipes store bounded control values. Every application takes a fresh local copy.
import { api, el, openSheet, toast, nice, state } from './main.js';
import { getPhoto, updatePhoto, persistProject, scheduleProjectSave } from './project.js';
import { runTool } from './tools.js';
import { runOptions } from './settings.js';
import { recipePatch } from './recipe_application.js';

function button(label, fn, style = 'line') {
  return el('button', { type: 'button', class: style, on: { click: fn } }, label);
}

async function applyRecipes(assignments, planned = false) {
  if (!assignments.length) throw new Error('Select at least one matching photo.');
  // Validate capacity for the whole selection before changing the first photo.
  const patches = assignments.map(({ file, recipe, missing = [] }) => ({
    file, patch: recipePatch(getPhoto(file.path), recipe, planned, missing),
  }));
  for (const { file, patch } of patches) {
    if (patch) updatePhoto(file.path, patch);
  }
  if (!await persistProject()) throw new Error('Your changes are open, but saving failed. Keep this folder open and retry.');
}

const select = (label, choices) => el('select', { 'aria-label': label },
  choices.map(([value, title]) => el('option', { value }, title)));

function conditionEditor(measures, current = {}) {
  const scene = select('Scene', [['', 'Any scene'], ['people', 'People'], ['landscape', 'Landscape'], ['other', 'Other']]);
  scene.value = current.scene || '';
  const rows = el('div', { class: 'recipe-tests' });
  const read = [];
  const add = button('Add a test', () => addTest());
  function addTest(test = {}) {
    if (read.length >= 3) return;
    const metric = select('Measurement', Object.entries(measures).map(([key, info]) => [key, info.label]));
    if (test.measure) metric.value = test.measure;
    const op = select('Comparison', [['above', 'is above'], ['below', 'is below']]);
    op.value = test.op || 'above';
    const value = el('input', { type: 'number', step: 'any', required: true, 'aria-label': 'Threshold', value: test.value ?? '' });
    const limits = () => {
      const info = measures[metric.value];
      value.min = info.min; value.max = info.max; value.placeholder = info.min + ' to ' + info.max;
    };
    metric.addEventListener('change', limits); limits();
    const get = () => {
      if (!value.checkValidity()) { value.reportValidity(); throw new Error('Enter a threshold within the measurement range.'); }
      return { measure: metric.value, op: op.value, value: value.valueAsNumber };
    };
    const row = el('div', { class: 'recipe-test' }, metric, op, value, button('Remove test', () => {
      row.remove(); read.splice(read.indexOf(get), 1); add.disabled = false;
    }, 'link'));
    read.push(get); rows.append(row); add.disabled = read.length >= 3;
  }
  for (const test of current.tests || []) addTest(test);
  return { node: el('fieldset', { class: 'recipe-condition' }, el('legend', null, 'Match photos'), scene, rows, add),
    get() {
      const condition = {};
      if (scene.value) condition.scene = scene.value;
      if (read.length) condition.tests = read.map(get => get());
      return condition;
    } };
}

function download(data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = el('a', { href: url, download: data.name.replace(/[^\p{L}\p{N}_ -]/gu, '_') + '.json' });
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function routingPanel(recipes, note, take, repaint) {
  const listing = new Map(recipes.map(r => [r.id, r]));
  const active = (state.recipes || []).filter(id => listing.has(id));
  const options = select('Recipe to add', recipes.map(r => [r.id, r.name]));
  const order = el('ol', { class: 'recipe-order' });
  const results = el('div', { role: 'status' });
  const dir = state.dir;
  let revision = 0;
  const change = () => {
    revision++; results.replaceChildren(); state.recipes = active.slice(); scheduleProjectSave(); draw();
  };
  function draw() {
    order.replaceChildren(...active.map((id, i) => {
      const move = button('Move up', () => { [active[i-1], active[i]] = [id, active[i-1]]; change(); });
      move.disabled = i === 0;
      return el('li', null, listing.get(id).name, move, button('Remove', () => { active.splice(i, 1); change(); }, 'link'));
    }));
    plan.disabled = !active.length && !state.files.some(f => f.checked && getPhoto(f.path).edit_steps?.some(step => step.plan));
  }
  const plan = button('Plan', () => take(async () => {
    const inputs = state.files.filter(f => f.checked);
    if (!inputs.length) throw new Error('Tick the photos to plan.');
    const snapshot = ++revision;
    results.replaceChildren();
    note.textContent = 'Checking ' + inputs.length + ' photos…';
    const settings = runOptions();
    const allowed = ['backend','device','genre','style','preset','concepts','look','raw_mode','raw_recover','fast_analysis','night_sky','lens_ca','lens_vignette','snap_edges'];
    const selectedOptions = Object.fromEntries(allowed.filter(k => k in settings).map(k => [k, settings[k]]));
    // An empty order only clears earlier assignments, and that needs no measuring.
    const data = active.length
      ? await runTool('plan-recipes', { folder: dir, photos: inputs.map(f => f.path), recipes: active.slice(), options: selectedOptions })
      : { recipes: [], counts: {}, unmatched: inputs.length, missing_objects: 0, failed: 0,
        items: inputs.map(f => ({ path: f.path, recipe_id: null, missing: [] })) };
    if (state.dir !== dir || revision !== snapshot || !results.isConnected) return;
    note.textContent = '';
    const snapshots = new Map(data.recipes.map(r => [r.id, r]));
    const matches = new Map(inputs.map(f => [f.path, f]));
    const assigned = data.items.filter(row => !row.error && (row.recipe_id || getPhoto(row.path).edit_steps?.some(step => step.plan))).map(row => ({
      file: matches.get(row.path), recipe: snapshots.get(row.recipe_id), missing: row.missing,
    }));
    const summary = el('ul', null, data.recipes.map(r => el('li', null, r.name + ' · ' + data.counts[r.id])));
    const problems = data.items.filter(row => row.error || row.missing?.length);
    const details = problems.length ? el('details', null, el('summary', null, 'Photos to review'), el('ul', null,
      problems.map(row => el('li', null, (matches.get(row.path)?.name || row.path) + ' · ' +
        (row.error || ('Missing ' + row.missing.join(', '))))))) : null;
    const apply = button('Apply plan', () => take(async () => {
      if (revision !== snapshot || state.dir !== dir) throw new Error('Run Plan again for this folder.');
      await applyRecipes(assigned, true); repaint(); results.replaceChildren(); note.textContent = 'Updated recipe assignments on ' + assigned.length + ' photos.';
    }));
    apply.disabled = !assigned.length;
    results.replaceChildren(summary, el('p', null, data.unmatched + ' unmatched (use prior settings) · ' + data.missing_objects + ' missing objects · ' + data.failed + ' failed'), ...(details ? [details] : []), apply);
  }));
  draw();
  return el('section', { class: 'recipe-plan' }, el('h3', null, 'Recipe order (first match wins)'),
    order, options, button('Add to order', () => {
      if (!listing.has(options.value)) return;
      if (active.includes(options.value)) { note.textContent = 'That recipe is already in the order.'; return; }
      if (active.length >= 20) { note.textContent = 'A plan supports up to 20 recipes.'; return; }
      active.push(options.value); change();
    }), plan, results);
}

export function openPhotoPresets({ getOptions, getOffsets, file, folder = false, repaint = () => {} }) {
  const dir = state.dir;
  return openSheet('Recipes', (body, close) => {
    const note = el('p', { class: 'fine recipe-status', role: 'status' }, 'Loading recipes…');
    const content = el('div'); body.append(el('div', null, note, content));
    let busy = false;
    const take = async fn => {
      if (busy || dir !== state.dir) return;
      busy = true;
      try { await fn(); } catch (error) { note.textContent = error.message; }
      finally { busy = false; }
    };
    const load = async () => {
      const { presets, measures } = await api('/edit-presets');
      if (!body.isConnected || dir !== state.dir) return;
      note.textContent = '';
      const list = el('div', { class: 'saved-presets' });
      const name = el('input', { id: 'preset-name', type: 'text', maxLength: 80, required: true });
      let conditions = conditionEditor(measures);
      const condArea = el('div', null, conditions.node);
      let edited = null;
      const submit = el('button', { type: 'submit', class: 'primary' }, 'Save current settings');
      const form = el('form', { class: 'preset-save', on: { submit: e => {
        e.preventDefault(); take(async () => {
          const settings = { ...(edited ? edited.settings : getOptions()) };
          const cond = conditions.get();
          if (Object.keys(cond).length) settings.condition = cond; else delete settings.condition;
          await api('/edit-presets', { ...(edited ? {id: edited.id} : {}), name: name.value.trim(), settings,
            ...(!edited && getOffsets ? {offsets: getOffsets()} : {}) });
          await load(); note.textContent = 'Recipe saved.';
        });
      } } }, el('label', { htmlFor: 'preset-name' }, 'Recipe name'), name, condArea, submit);
      const savePanel = el('details', { class: 'recipe-save-panel', open: !presets.length },
        el('summary', null, 'Save a recipe'), form);
      for (const recipe of presets) {
        const apply = button(folder ? 'Apply to ticked photos' : 'Apply to this photo', () => take(async () => {
          const files = folder ? state.files.filter(f => f.checked) : [file];
          await applyRecipes(files.map(file => ({file, recipe}))); repaint(); close(); toast('Applied ' + recipe.name + '.');
        }));
        const edit = button('Edit name or conditions', () => {
          edited = recipe; name.value = recipe.name; conditions = conditionEditor(measures, recipe.settings.condition);
          condArea.replaceChildren(conditions.node); submit.textContent = 'Save recipe changes'; savePanel.open = true; name.focus();
        }, 'link');
        const saveFile = button('Export', () => take(async () => download(await api('/edit-presets/' + recipe.id + '/export'))), 'link');
        const remove = button('Delete', e => {
          const control = e.currentTarget;
          if (!control.dataset.confirm) { control.dataset.confirm = '1'; control.textContent = 'Confirm delete'; return; }
          take(async () => {
            const response = await fetch('/api/v1/edit-presets/' + recipe.id, { method: 'DELETE' });
            if (!response.ok) throw new Error('Could not delete the recipe.');
            state.recipes = (state.recipes || []).filter(id => id !== recipe.id); scheduleProjectSave(); await load();
          });
        }, 'link');
        const targets = [recipe.settings.look ? nice(recipe.settings.look) : '', ...Object.keys(recipe.settings.region_settings || {})].filter(Boolean);
        list.append(el('div', { class: 'recipe-card' }, el('strong', null, recipe.name),
          targets.length ? el('small', null, targets.join(' · ')) : null,
          el('div', {class:'btnrow'}, apply, edit, saveFile, remove)));
      }
      const upload = el('input', {type:'file', accept:'.json,application/json', 'aria-label':'Import recipe JSON', on:{change:e => {
        const selected = e.target.files[0];
        if (selected) take(async () => {
          if (selected.size > 65536) throw new Error('Choose a recipe file smaller than 64 KB.');
          await api('/edit-presets/import', JSON.parse(await selected.text())); await load(); note.textContent = 'Recipe imported.';
        });
      }}});
      content.replaceChildren();
      if (folder && (presets.length || state.files.some(f => getPhoto(f.path).edit_steps?.some(step => step.plan)))) content.append(routingPanel(presets, note, take, repaint));
      content.append(list, savePanel, el('label', {class:'recipe-import'}, 'Import recipe', upload));
    };
    load().catch(error => { note.textContent = error.message; });
  });
}
