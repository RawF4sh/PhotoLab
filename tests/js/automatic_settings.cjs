// Automatic sends what the Settings sheet shows, and nothing of a Guided folder's own choices.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
  const calls = [];
  let bar = [];
  function el(tag, attrs, ...kids) {
    const node = { tag, children: kids.flat().filter(Boolean),
      append(...next) { this.children.push(...next); }, replaceChildren(...next) { this.children = next; } };
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === 'on') node.click = value.click; else node[key] = value;
    }
    return node;
  }
  function find(node, label) {
    if (!node || typeof node !== 'object') return null;
    if (node.tag === 'button' && node.children.includes(label)) return node;
    for (const child of node.children || []) { const hit = find(child, label); if (hit) return hit; }
    return null;
  }
  const state = { finder: 'stub', settings: { out_format: 'png', metadata: 'none' },
    look: 'punchy', strength: 1.4, runText: 'darken the sky', exportPreset: 'web' };
  async function api(url, body) {
    calls.push([url, body]);
    if (url === '/sample') return { dir: '/photos' };
    if (url.startsWith('/groups')) return [{ pixel: 'a.jpg' }, { raw: 'b.cr2', pixel: 'b.jpg' }];
    return { id: 'job1', status: 'running', processed: 0, total: 2, items: [] };
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
  module.namespace.automatic.mount(root);
  assert(bar.includes('settings'), 'Settings must be reachable from Automatic');
  await find(root, 'Try a sample set').click();
  await find(root, 'Edit and export').click();
  const [, job] = calls.find(([url]) => url === '/jobs');
  assert.equal(job.backend, 'stub', 'the region finder chosen in Settings applies');
  assert.equal(job.out_format, 'png');
  assert.equal(job.metadata, 'none');
  assert.equal(job.local_only, true);
  assert.equal(job.translator, 'off');
  assert.deepEqual(Array.from(job.files), ['/photos/a.jpg', '/photos/b.cr2']);
  assert(job.out_dir.startsWith('/photos/AutoEdit Exports/Automatic '));
  for (const key of ['look', 'intensity', 'edit', 'export']) {
    assert(!(key in job), 'a Guided folder choice must not reach Automatic: ' + key);
  }
  console.log('Automatic uses the Settings sheet and keeps Guided folder choices out.');
})().catch(error => { console.error(error); process.exitCode = 1; });
