// Run the shipping recipe patcher without DOM rendering. Exercise only user-observable edits.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
  const context = vm.createContext({});
  function load(name) {
    return new vm.SourceTextModule(fs.readFileSync(path.join(__dirname, '../../src/autoedit/web/static/app', name), 'utf8'), { context });
  }
  const controls = load('edit_steps.js');
  await controls.link(spec => {
    const keys = spec === './main.js' ? ['el', 'nice', 'toast'] : ['getPhoto', 'updatePhoto'];
    return new vm.SyntheticModule(keys, function () { keys.forEach(k => this.setExport(k, () => {})); }, { context });
  });
  const suite = load('recipe_application.js');
  await suite.link(() => controls); await suite.evaluate();
  const apply = (photo, recipe, plan, missing) =>
    JSON.parse(JSON.stringify({ ...photo, ...suite.namespace.recipePatch(photo, recipe, plan, missing) }));
  const a = {id:'a'.repeat(32), name:'Soft', settings:{look:'natural', intensity:.6, sliders:{temp:4}}};
  const b = {id:'b'.repeat(32), name:'Deep', settings:{look:'punchy', intensity:1.4, sliders:{contrast:8}}};
  const manual = {look:'original-choice', sliders:{exposure:-.25}};
  let photo = apply(manual, a, true, ['sky']);
  assert.equal(photo.edit_steps.length, 1); assert.deepEqual(photo.edit_steps[0].plan.missing, ['sky']);
  photo.intensity = .7;
  photo = apply(photo, b, true);
  assert.equal(photo.edit_steps.length, 1); assert.equal(photo.edit_steps[0].ref, b.id);
  assert.equal(photo.intensity, .7); assert.equal(photo.look, 'punchy');
  assert.deepEqual(apply(photo, b, true), photo);
  assert.deepEqual(apply(photo, null, true), {...manual, intensity:.7});
  const other = apply(manual, a, false);
  other.edit_steps[0].amount = .35;
  assert.deepEqual(apply(apply(other, a, true), null, true), other);
  assert.deepEqual(apply(apply(other, b, true), null, true), other);
  assert.deepEqual(apply(manual, null, true), manual);
  console.log('Recipe reassignment, fallback, repeat application and manual overrides verified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
