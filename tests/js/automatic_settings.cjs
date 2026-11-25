// Automatic previews originals, uses the local catalog, and sends only local work.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
  const calls = [];
  let bar = [], failCatalog = false, nextJob;
  let referencePicker = async () => ({ path: null });
  function el(tag, attrs, ...kids) {
    const node = { tag, children: kids.flat().filter(Boolean),
      append(...next) { this.children.push(...next); }, replaceChildren(...next) { this.children = next; } };
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === 'on') Object.assign(node, value); else node[key] = value;
    }
    return node;
  }
  function find(node, matches) {
    if (!node || typeof node !== 'object') return null;
    if (typeof matches === 'string' ? node.tag === 'button' && node.children.includes(matches) : matches(node)) return node;
    for (const child of node.children || []) { const hit = find(child, matches); if (hit) return hit; }
    return null;
  }
  const reference = '/library/standard-grade.jpg', custom = '/library/blue-hour.jpg';
  const state = { finder: 'stub', settings: { out_format: 'png', metadata: 'none', reference, reference_strength: 0.7 },
    look: 'punchy', strength: 1.4, runText: 'darken the sky', exportPreset: 'web' };
  async function api(url, body) {
    calls.push([url, body]);
    if (url === '/local-styles') {
      if (failCatalog) throw new Error('offline');
      return { styles: [{ id: 'standard', label: 'Simple' }, { id: 'auto', label: 'Auto' }] };
    }
    if (url === '/sample') return { dir: '/photos' };
    if (url === '/pick?kind=folder') return { path: '/photos' };
    if (url === '/pick?kind=file') return referencePicker();
    if (url.startsWith('/groups')) return [{ pixel: 'a.jpg' }, { raw: 'b.cr2', pixel: 'b.jpg' }];
    if (url === '/jobs') return nextJob;
    throw new Error('Unexpected request ' + url);
  }
  const exports = { el, api, state, go() {}, toast() {}, setBar: (...nodes) => { bar = nodes; },
    backButton: () => 'back', gearButton: () => 'settings', baseName: p => p.split('/').pop(),
    joinPath: (a, b) => a.replace(/\/$/, '') + '/' + b, thumbUrl: p => p };
  const context = vm.createContext({ setTimeout: () => 0, clearTimeout() {},
    crypto: { getRandomValues: values => values }, sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} } });
  const source = fs.readFileSync(path.resolve(__dirname, '../../src/autoedit/web/static/app/modes.js'), 'utf8');
  const module = new vm.SourceTextModule(source, { context });
  await module.link(name => {
    const given = name === './main.js' ? exports : { runTool() {} };
    return new vm.SyntheticModule(Object.keys(given), function () {
      for (const [key, value] of Object.entries(given)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();

  const root = el('main');
  failCatalog = true;
  module.namespace.automatic.mount(root);
  await new Promise(resolve => setImmediate(resolve));
  assert(bar.includes('settings'), 'Settings must be reachable from Automatic');
  assert(find(root, 'Run AI Edit & export').disabled, 'AI must be visible and idle before choosing originals');
  failCatalog = false;
  await find(root, 'Reload styles').click();
  await find(root, 'Try a sample set').click();
  assert.equal(find(root, n => n.tag === 'select').value, 'standard');
  const contains = text => find(root, n => n.children.some(c => typeof c === 'string' && c.includes(text)));
  assert(!find(root, n => n.type === 'password'), 'Automatic has no cloud credential fields');
  assert(find(root, n => n.tag === 'img').alt.startsWith('Original '));
  assert(!calls.some(([url]) => url === '/jobs'), 'viewing originals does not start an edit');
  nextJob = { id: 'job1', status: 'done', processed: 2, done: 2, review: 0, total: 2, items: [] };
  await find(root, 'Run AI Edit & export').click();
  const [, job] = calls.find(([url]) => url === '/jobs');
  assert.equal(job.consistent, false, 'independent automatic exports match individual Photo editor saves');
  assert.equal(job.backend, 'stub', 'the region finder chosen in Settings applies');
  assert.equal(job.out_format, 'png');
  assert.equal(job.metadata, 'none');
  assert.equal(job.local_only, true);
  assert.equal(job.reference, reference, 'the existing Settings reference is inherited');
  assert.equal(job.reference_strength, 0.7);
  assert(!job.ai_edit && !JSON.stringify(job).includes('session-only'));
  assert.equal(job.translator, 'off');
  assert.deepEqual(Array.from(job.files), ['/photos/a.jpg', '/photos/b.cr2']);
  assert(job.out_dir.startsWith('/photos/AutoEdit Exports/Automatic '));
  for (const key of ['look', 'intensity', 'edit', 'export']) {
    assert(!(key in job), 'a Guided folder choice must not reach Automatic: ' + key);
  }
  await find(root, 'Choose more photos').click();
  await find(root, 'Try a sample set').click();
  await new Promise(resolve => setImmediate(resolve));
  find(root, n => n.id === 'automatic-style').change({ target: { value: 'auto' } });
  assert(contains('standard-grade.jpg'));
  let resolvePicker;
  referencePicker = () => new Promise(resolve => { resolvePicker = resolve; });
  const picking = find(root, 'Choose reference').click();
  assert(find(root, 'Run AI Edit & export').disabled);
  await find(root, 'Run AI Edit & export').click();
  assert.equal(calls.filter(([url]) => url === '/jobs').length, 1, 'exports wait for reference selection');
  resolvePicker({ path: custom }); await picking;
  assert(contains('blue-hour.jpg'));
  referencePicker = async () => ({ path: null });
  await find(root, 'Choose reference').click();
  referencePicker = async () => { throw new Error('Picker unavailable'); };
  await find(root, 'Choose reference').click();
  assert(contains('Picker unavailable') && contains('blue-hour.jpg'), 'failure and cancellation retain the reference');
  const ai = find(root, 'Run AI Edit & export');
  assert.equal(ai.disabled, false);
  nextJob = { ...nextJob, id: 'job2', items: [
    { name: 'a.jpg', out: '/out/a.jpg', ai_status: 'applied' },
    { name: 'b.cr2', out: '/out/b.jpg', ai_status: 'fallback', ai_reason: 'Session budget reached.' },
  ] };
  await Promise.all([ai.click(), ai.click()]);
  const jobs = calls.filter(([url]) => url === '/jobs');
  assert.equal(jobs.length, 2, 'a double click starts one AI export');
  assert.equal(jobs[1][1].style, 'auto');
  assert.equal(jobs[1][1].consistent, true, 'Auto groups neighbouring frames');
  assert.equal(jobs[1][1].local_only, true);
  assert.equal(jobs[1][1].decider, 'rules');
  assert.equal(jobs[1][1].allow_cloud, false);
  assert(!('ai_edit' in jobs[1][1]));
  assert.equal(jobs[1][1].reference, custom);
  assert.equal(jobs[1][1].reference_strength, 0.7);
  assert(find(root, n => n.tag === 'summary' && n.children.some(c => typeof c === 'string' && c.includes('Used local fallback'))));
  assert(find(root, n => n.tag === 'li' && n.children.some(c => typeof c === 'string' && c.includes('Session budget reached.'))));
  await find(root, 'Choose more photos').click();
  await find(root, 'Try a sample set').click();
  await new Promise(resolve => setImmediate(resolve));
  assert(contains('standard-grade.jpg') && !contains('blue-hour.jpg'), 'new selections clear their reference override');
  assert(!find(root, 'Run AI Edit & export').disabled);
  failCatalog = true;
  await find(root, 'Choose folder').click();
  await new Promise(resolve => setImmediate(resolve));
  assert(find(root, 'Reload styles') && !find(root, 'Run AI Edit & export').disabled);
  failCatalog = false;
  await find(root, 'Reload styles').click();
  assert(!find(root, 'Run AI Edit & export').disabled);
  await find(root, 'Remove reference').click();
  assert(!contains('standard-grade.jpg'));
  await find(root, 'Run AI Edit & export').click();
  assert(!('reference' in calls.filter(([url]) => url === '/jobs').at(-1)[1]));
  assert.equal(state.settings.reference, reference, 'removing a reference leaves the Settings default intact');
  console.log('Automatic local styles, original previews, references and old job receipts pass.');
})().catch(error => { console.error(error); process.exitCode = 1; });
