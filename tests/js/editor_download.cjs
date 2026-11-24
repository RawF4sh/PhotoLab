// The Photo editor page asks before it downloads the editor, as the SAM 3 weights do.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

(async () => {
  const requests = [], nodes = new Map();
  let installed = false;
  function node(id) {
    if (!nodes.has(id)) {
      const events = {};
      nodes.set(id, { id, hidden: false, disabled: false, textContent: '', src: '', events,
        addEventListener(name, fn) { (events[name] ||= []).push(fn); }, focus() {},
        async click() { for (const fn of events.click || []) await fn({ currentTarget: this }); } });
    }
    return nodes.get(id);
  }
  async function fetch(url, options = {}) {
    requests.push([url, options.method || 'GET']);
    if (url === '/api/v1/editor/install') installed = true;
    return { ok: true, statusText: 'OK',
      json: async () => ({ ready: installed, version: '0.21.1', native: false, download_mb: 75 }) };
  }
  const context = vm.createContext({ fetch, document: { getElementById: node, querySelector: node },
    window: { addEventListener() {} }, location: { origin: 'http://localhost' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, sessionStorage: { getItem: () => null },
    setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {} });
  const source = fs.readFileSync(path.resolve(__dirname, '../../src/autoedit/web/static/app/editor_host.js'), 'utf8');
  const module = new vm.SourceTextModule(source, { context });
  await module.link(name => {
    const names = name === './editor_assist.js' ? ['setupEditorAssist'] : ['comparePhoto', 'thumbnail'];
    return new vm.SyntheticModule(names, function () { for (const key of names) this.setExport(key, () => {}); }, { context });
  });
  await module.evaluate();
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();

  assert.deepEqual(requests, [['/api/v1/editor/status', 'GET']], 'opening the page must not start the download');
  assert.equal(node('install').hidden, false);
  assert.equal(node('retry').hidden, true);
  assert.equal(node('editor').src, '');
  assert.match(node('boot-message').textContent, /LightCraft 0\.21\.1/);
  assert.match(node('boot-message').textContent, /75 MB/);

  await node('install').click();
  await settle();
  assert(requests.some(([url, method]) => url === '/api/v1/editor/install' && method === 'POST'));
  assert.equal(node('install').hidden, true);
  assert.equal(node('editor').src, '/editor/release/index.html?host=parent');
  console.log('The editor download waits for a click and names its size.');
})().catch(error => { console.error(error); process.exitCode = 1; });
