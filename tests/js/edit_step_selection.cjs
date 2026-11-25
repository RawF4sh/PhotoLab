// The same selections pass through the real project, history, persistence and reload modules.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = '/photos/example.png';
const stored = new Map();
async function launch() {
  const state = { dir: '', files: [{ path: source, checked: true }], settings: {} };
  const context = vm.createContext({
    location: { hash: '#/photo/0' }, window: { addEventListener() {} }, setTimeout, clearTimeout,
  });
  async function api(url, payload) {
    if (url === '/project' && payload) {
      stored.set(payload.dir, JSON.stringify(payload.project)); return { saved: true };
    }
    const dir = new URL(url, 'http://localhost').searchParams.get('dir');
    return { project: stored.has(dir) ? JSON.parse(stored.get(dir)) : null };
  }
  const main = { api, state, toast: assert.fail, el() {}, nice: x => x };
  const mods = new Map([['./main.js', new vm.SyntheticModule(Object.keys(main), function () {
    for (const [key, value] of Object.entries(main)) this.setExport(key, value);
  }, { context })]]);
  function load(name) {
    if (!mods.has(name)) mods.set(name, new vm.SourceTextModule(fs.readFileSync(
      path.join(__dirname, '../../src/autoedit/web/static/app', name), 'utf8'), { context, identifier: name }));
    return mods.get(name);
  }
  const changes = load('./edit_steps.js');
  await changes.link(load); await changes.evaluate();
  const project = load('./project.js').namespace;
  await project.loadProject('/photos', { initialize() { state.dir = '/photos'; } });
  // Run the real batch result receiver with a stub for its DOM shell.
  const shell = {
    './main.js': ['el','api','state','store','nice','thumbUrl','joinPath','baseName','getSchema','go','toast','openSheet','setBar','backButton','gearButton'],
    './settings.js': ['runOptions','openSettings'], './photo.js': ['loadEntries','textOf','marksOf','hideRequest'],
    './tools.js':['runTool'], './share.js':['openShare','exportsDir'], './presets.js':['openPhotoPresets'],
    './workflow.js':['workflowSteps','readinessCard'],
  };
  const folder = load('./folder.js');
  await folder.link(name => {
    if (name === './project.js') return load(name);
    const keys = shell[name];
    return new vm.SyntheticModule(keys, function () {
      for (const key of keys) this.setExport(key, main[key] || (() => {}));
    }, { context });
  });
  await folder.evaluate();
  return { project, changes: changes.namespace, file: state.files[0], applyBatchItem: folder.namespace.applyBatchItem };
}

(async () => {
  const { project, changes } = await launch();
  const answer = { id: 'request', model: 'model', proposal: { changes: [
    { label: 'Lift', why: 'Brighten the foreground', target: 'photo', ops: { exposure: .4 } },
    { label: 'Warm', why: 'Warm the colors', target: 'photo', ops: { temp: 2 } },
    { label: 'Protect sky', why: 'Retain sky details', target: 'sky', ops: { highlights: -8 } },
  ], explanation: 'Review before applying.' } };
  const selected = new Set([0, 2]);
  project.updatePhoto(source, { sliders: { exposure: -.1 } });
  const initial = project.getPhoto(source);
  const entries = changes.selectedChanges(initial, answer, selected);
  project.updatePhoto(source, { edit_steps: entries, local_only: true });
  const applied = project.getPhoto(source);
  assert.deepEqual(Array.from(applied.edit_steps, x => x.id), ['request-0', 'request-2']);
  assert.equal(applied.edit_steps[1].regions.sky.highlights, -8);
  assert.equal(applied.sliders.exposure, -.1);
  assert.equal(JSON.stringify(changes.selectedChanges(applied, answer, selected)), JSON.stringify(entries));
  const revised = applied.edit_steps.map((entry, i) => i ? { ...entry, on: false } : { ...entry, amount: .5 });
  project.updatePhoto(source, { edit_steps: revised });
  await project.persistProject();
  const { project: reopened } = await launch();
  assert.equal(JSON.stringify(reopened.getPhoto(source).edit_steps), JSON.stringify(revised));
  assert(reopened.undoPhoto(source)); assert.equal(reopened.getPhoto(source).edit_steps[1].on, true);
  assert(reopened.undoPhoto(source)); assert.equal(reopened.getPhoto(source).edit_steps, undefined);
  assert.equal(reopened.getPhoto(source).sliders.exposure, -.1);
  assert(reopened.redoPhoto(source)); assert.equal(reopened.getPhoto(source).edit_steps.length, 2);
  await reopened.persistProject();
  const full = { edit_steps: Array.from({length:24}, (_, i) => ({ ...entries[0], id: 'full-'+i })) };
  assert.throws(() => changes.selectedChanges(full, answer, selected), /24 changes/);
  assert.equal(full.edit_steps.length, 24);
  const regions = { region_settings: Object.fromEntries(Array.from({length:32}, (_,i) => ['thing'+i, {}])) };
  assert.throws(() => changes.selectedChanges(regions, answer, selected), /32 objects/);
  const review = await launch();
  const entry = { id: 'recipe-a', origin: 'recipe', label: 'Coat', amount: 1, on: true, global: {}, regions: { coat: { temp: 2 } } };
  review.project.updatePhoto(source, { edit_steps: [entry] });
  const batch = { status: 'review', out: 'photo.jpg', quality_warn: 'Highlight clipping',
    object_checks: { coat: false }, warn: 'Highlight clipping; Object not found: coat' };
  review.applyBatchItem(review.file, batch, {signature: 'same'}, 'same');
  assert.match(review.file.recipeReview, /coat/); assert.equal(review.file.status, 'review');
  await review.project.persistProject();
  const reopenedReview = await launch();
  assert.match(reopenedReview.file.recipeReview, /coat/);
  assert.equal(reopenedReview.file.error, 'Highlight clipping');
  reopenedReview.project.updatePhoto(source, { edit_steps: [{ ...entry, on: false }] });
  assert.equal(reopenedReview.file.recipeReview, ''); assert.equal(reopenedReview.file.status, 'review');
  assert.equal(reopenedReview.file.error, 'Highlight clipping');
  assert(reopenedReview.project.undoPhoto(source)); assert.match(reopenedReview.file.recipeReview, /coat/);
  reopenedReview.applyBatchItem(reopenedReview.file, {...batch, quality_warn: ''}, {signature:'a'}, 'a');
  assert.equal(reopenedReview.file.status, 'finished'); assert.match(reopenedReview.file.recipeReview, /coat/);
  reopenedReview.project.updatePhoto(source, {edit_steps: []});
  assert.equal(reopenedReview.file.recipeReview, ''); assert.equal(reopenedReview.file.status, 'finished');
  const old = { signature: 'a', status: 'finished', error: '' };
  reopenedReview.applyBatchItem(reopenedReview.file, {status:'editing'}, old, 'a');
  reopenedReview.applyBatchItem(reopenedReview.file, batch, old, 'b');
  assert.equal(reopenedReview.file.status, 'finished'); assert.equal(reopenedReview.file.error, '');
  assert.equal(reopenedReview.file.recipeReview, ''); assert(reopenedReview.file.dirty);
  const receipt = {scene:'mist', median:{global:{exposure:.4}}, intensity:1, scene_override:'mist'};
  const path = reopenedReview.file.path;
  reopenedReview.project.updatePhoto(path, {style:'auto',scene:'mist',base:{wb:[1,2]}});
  reopenedReview.applyBatchItem(reopenedReview.file, {...batch,auto_shoot:receipt}, {signature:'x'}, 'x');
  assert.equal(reopenedReview.project.getPhoto(path).base.auto_shoot.scene, 'mist');
  assert.equal(reopenedReview.file.dirty, false);
  reopenedReview.project.updatePhoto(path, {scene:undefined});
  assert(!reopenedReview.project.getPhoto(path).base.auto_shoot);
  assert.deepEqual([...reopenedReview.project.getPhoto(path).base.wb], [1,2]);
  reopenedReview.project.undoPhoto(path);
  assert.equal(reopenedReview.project.getPhoto(path).base.auto_shoot.scene, 'mist');
  console.log('Named suggestions, selective acceptance, limits, persistence and undo passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
