// Exercise the real shell, folder opener and saved-project code without a browser or packages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = path.resolve(__dirname, '../../src/autoedit/web/static/app');

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.events = {}; this.dataset = {};
    this.style = {}; this.classList = { add() {}, remove() {}, toggle() {} }; this.open = false;
  }
  append(...children) {
    for (const child of children) {
      if (typeof child === 'object') {
        if (child.parentNode) child.parentNode.children = child.parentNode.children.filter(x => x !== child);
        child.parentNode = this;
      }
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    for (const child of this.children) if (typeof child === 'object') child.parentNode = null;
    this.children = []; this.append(...children);
  }
  get textContent() { return this.children.map(c => typeof c === 'object' ? c.textContent : String(c)).join(''); }
  set textContent(value) { this.replaceChildren(value); }
  get isConnected() { return this.tagName === 'BODY' || !!this.parentNode?.isConnected; }
  addEventListener(event, fn) { this.events[event] = fn; }
  setAttribute(key, value) { this[key] = value; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  async click() { if (!this.disabled) return this.events.click?.({ currentTarget: this, target: this }); }
  find(fn) {
    if (fn(this)) return this;
    for (const child of this.children) {
      const hit = typeof child === 'object' && child.find(fn);
      if (hit) return hit;
    }
  }
}

async function fixture() {
  const events = {}, session = {};
  let lastJob;
  const folderPath = '/photos/saved-shoot', photoPath = folderPath + '/second.jpg';
  const saved = { version: 1, folder: { look: 'natural_clean' }, photos: {
    [photoPath]: { sliders: { exposure: .25 }, region_settings: { sky: { highlights: -8 } },
      history: [{ sliders: {} }], future: [] },
  }, files: {}, last_index: 1 };
  const before = JSON.stringify(saved), requests = [], timers = new Map();
  let timer = 0, approved = false, picked = null, failure = null;
  const body = new Element('body');
  for (const id of ['bar', 'view', 'sheet', 'toast']) { const element = new Element('div'); element.id = id; body.append(element); }
  const document = { body, createElement: tag => new Element(tag), addEventListener() {},
    querySelector: selector => body.find(element => element.id === selector.slice(1)) };
  const store = {};
  const answer = (status, json) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(json)) });
  const context = vm.createContext({ document, window: { addEventListener: (event, fn) => { events[event] = fn; } }, crypto: require('node:crypto').webcrypto,
    sessionStorage: { getItem: k => session[k], setItem: (k,v) => { session[k] = v; }, removeItem: k => { delete session[k]; } }, location: { hash: '#/start' },
    localStorage: { getItem: key => store[key] || null, setItem: (key, value) => { store[key] = value; } },
    setTimeout: (fn, ms) => { if (ms === 50) fn(); else timers.set(++timer, fn); return timer; },
    clearTimeout: id => timers.delete(id), console,
    fetch: async (url, opts = {}) => {
      const route = new URL(url, 'http://localhost'); requests.push({ route: route.pathname, opts });
      if (route.pathname.endsWith('/groups')) {
        if (failure === 'offline') throw new Error('offline');
        if (failure) return answer(500, { error: 'Could not read photos', code: 'internal' });
        if (!approved) return answer(403, { error: 'technical path error', code: 'forbidden' });
        return answer(200, [{ pixel: 'first.jpg' }, { pixel: 'second.jpg' }]);
      }
      if (route.pathname.endsWith('/pick')) { if (picked) approved = true; return answer(200, { path: picked }); }
      if (route.pathname.endsWith('/local-styles')) return answer(200, { styles: [{ id: 'standard', label: 'Simple' }] });
      if (route.pathname.endsWith('/project')) {
        assert(approved, 'reading/writing a saved project must wait for the picker');
        if (opts.method === 'POST') return answer(200, { saved: true });
        return answer(200, { project: saved });
      }
      if (route.pathname.endsWith('/jobs')) {
        const body = JSON.parse(opts.body);
        lastJob = { id: 'auto-job', status: 'running', total: body.files.length, processed: 0, done: 0,
          failed: 0, review: 0, out_dir: body.out_dir, items: body.files.map(src => ({ src, name: 'photo' })) };
        return answer(200, lastJob);
      }
      if (route.pathname.endsWith('/jobs/auto-job')) {
        return answer(200, { ...lastJob, status: 'done', processed: lastJob.total, done: lastJob.total,
          items: lastJob.items.map((i, n) => ({ ...i, out: lastJob.out_dir + '/' + n + '.jpg' })) });
      }
      if (route.pathname.endsWith('/list')) return answer(200, []);
      throw new Error('Unexpected request: ' + url);
    },
  });
  const node = () => new Element('div');
  const stubs = {
    './settings.js': { openSettings() {}, applySurround() {}, runOptions: () => ({}), assistantOptions: () => ({}), setAssistantKey() {} },
    './photo.js': { screen: { mount() {} }, loadEntries: () => [], textOf: () => '', marksOf: () => [], hideRequest: () => null },
    './tools.js': { runTool: async () => ({}) }, './share.js': { openShare() {}, exportsDir: () => folderPath + '/AutoEdit Exports' },
    './presets.js': { openPhotoPresets() {} }, './workflow.js': { workflowSteps: node, readinessCard: node },
  };
  const modules = new Map();
  function moduleFor(name) {
    if (!modules.has(name)) {
      const stub = stubs[name];
      modules.set(name, stub ? new vm.SyntheticModule(Object.keys(stub), function () {
        for (const [key, value] of Object.entries(stub)) this.setExport(key, value);
      }, { context }) : new vm.SourceTextModule(fs.readFileSync(path.join(app, name), 'utf8'), { context, identifier: name }));
    }
    return modules.get(name);
  }
  const main = moduleFor('./main.js'); await main.link(moduleFor); await main.evaluate();
  return { main: main.namespace, folder: moduleFor('./folder.js').namespace, project: moduleFor('./project.js').namespace,
    folderPath, photoPath, requests, saved, before, context,
    navigate: name => { context.location.hash = '#/' + name; events.hashchange(); },
    view: document.querySelector('#view'),
    get sheet() { return document.querySelector('#sheet'); }, get toast() { return document.querySelector('#toast').textContent; },
    button: label => document.querySelector('#sheet').find(e => e.tagName === 'BUTTON' && e.textContent === label),
    pick: value => { picked = value; }, fail: value => { failure = value; },
  };
}

(async () => {
  for (const single of [true, false]) {
    const mode = await fixture();
    assert(mode.view.find(e => e.href === '#/automatic'));
    assert(mode.view.find(e => e.href === '#/editor'));
    mode.navigate('automatic');
    const button = text => mode.view.find(e => e.tagName === 'BUTTON' && e.textContent === text);
    await button(single ? 'Choose photo' : 'Choose folder').click();
    assert(button('Run AI Edit & export').disabled, 'cancelling the picker keeps the job idle');
    mode.pick(single ? mode.photoPath : mode.folderPath);
    await button(single ? 'Choose photo' : 'Choose folder').click();
    assert(!mode.requests.some(r => r.route.endsWith('/jobs')), 'opening photos only shows the originals');
    const start = button('Run AI Edit & export');
    await Promise.all([start.click(), start.click()]);
    const posts = mode.requests.filter(r => r.route.endsWith('/jobs'));
    assert.equal(posts.length, 1);
    const submitted = JSON.parse(posts[0].opts.body);
    assert.deepEqual(submitted.files, single ? [mode.photoPath] : [mode.folderPath + '/first.jpg', mode.photoPath]);
    assert(submitted.local_only && submitted.out_dir.startsWith(mode.folderPath + '/AutoEdit Exports/Automatic '));
    mode.navigate('start'); mode.navigate('automatic');
    await new Promise(resolve => setImmediate(resolve));
    assert(mode.view.textContent.includes('Export complete')); assert(button('Show exports'));
    await button('Choose more photos').click(); assert(button('Choose photo'));
    assert.equal(mode.context.sessionStorage.getItem('autoedit.automatic.job'), undefined);
  }
  const f = await fixture();
  await assert.rejects(f.main.api('/groups?dir=locked'), error => error.status === 403 && error.code === 'forbidden');
  await f.folder.loadFolderPath(f.folderPath);
  assert(f.sheet.open); assert(f.sheet.textContent.includes(f.folderPath));
  assert(!f.sheet.textContent.includes('technical path error'));
  assert.equal(f.main.state.dir, '');
  assert(!f.requests.some(r => r.route.endsWith('/pick') || r.route.endsWith('/project')));
  await f.button('Choose folder').click(); // cancelled native dialog
  assert(f.sheet.open); assert(!f.button('Choose folder').disabled);
  assert.equal(JSON.stringify(f.saved), f.before); assert.equal(f.main.state.dir, '');
  await f.button('Not now').click(); assert(!f.sheet.open);
  await f.folder.loadFolderPath(f.folderPath);
  f.pick(f.folderPath); await f.button('Choose folder').click();
  assert(!f.sheet.open); assert.equal(f.main.state.dir, f.folderPath);
  assert.equal(f.main.state.look, 'natural_clean');
  const restored = f.project.getPhoto(f.photoPath);
  assert.equal(restored.sliders.exposure, .25); assert.equal(restored.region_settings.sky.highlights, -8);
  assert.equal(restored.history.length, 1);
  assert.equal(JSON.stringify(f.saved), f.before);
  assert.equal(f.requests.filter(r => r.route.endsWith('/pick')).length, 2);

  const photo = await fixture();
  await photo.folder.loadPhotoPath(photo.photoPath);
  photo.pick(photo.folderPath); await photo.button('Choose folder').click();
  assert.equal(photo.context.location.hash, '#/photo/1');

  for (const failure of ['server', 'offline']) {
    const failed = await fixture(); failed.fail(failure);
    await failed.folder.loadFolderPath(failed.folderPath);
    assert(!failed.sheet.open); assert(failed.toast.includes(failure === 'offline' ? 'not reachable' : 'Could not read photos'));
    assert(!failed.requests.some(r => r.route.endsWith('/pick') || r.route.endsWith('/project')));
    assert.equal(failed.main.state.dir, '');
  }
  console.log('Folder reopen: access recovery, cancel, saved edits/history, photo target and unrelated errors pass.');
})().catch(error => { console.error(error); process.exitCode = 1; });
