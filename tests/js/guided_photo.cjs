// Drive the real Guided photo screen at its view and request boundaries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function check() {
  const requests = [], timers = new Map(), nodes = [], stored = new Map();
  let timer = 0, projectEvent, destination = '', pointerCapture;
  const files = [{ path: '/photos/first.raw', thumb: '/photos/first.jpg', name: 'first.raw' },
    { path: '/photos/next.jpg', thumb: '/photos/next.jpg', name: 'next.jpg' }];
  function el(tag, attrs, ...children) {
    const events = {};
    const node = { tag, children: children.flat().filter(Boolean), hidden: false, textContent: '', style: { setProperty() {} },
      dataset: {}, classList: { toggle() {}, remove() {} }, append(...kids) { this.children.push(...kids); },
      prepend(...kids) { this.children.unshift(...kids); }, replaceChildren(...kids) { this.children = kids; },
      setAttribute(key, value) { this[key] = String(value); }, removeAttribute(key) { delete this[key]; },
      get lastChild() { return this.children.at(-1); }, addEventListener(name, fn) { (events[name] ||= []).push(fn); },
      removeEventListener(name, fn) { events[name] = (events[name] || []).filter(f => f !== fn); },
      async fire(name, props = {}) {
        const event = { target: this, preventDefault() {}, stopPropagation() {}, ...props };
        for (const fn of events[name] || []) await fn(event);
      }, querySelector() { return null; }, setPointerCapture(id) { pointerCapture = id; },
      focus() {}, contains(target) { return target === this; }, getBoundingClientRect() { return { left: 12, top: 8, width: 200, height: 100 }; },
    };
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === 'on') for (const [name, fn] of Object.entries(value)) node.addEventListener(name, fn);
      else if (key === 'data') node.dataset = value;
      else node[key] = value;
    }
    nodes.push(node); return node;
  }
  const noop = () => {}, pending = new Promise(noop), sheet = el('dialog');
  const doc = Object.assign(el('document'), { getElementById: () => sheet });
  const context = vm.createContext({ window: el('window'), document: doc, crypto: { randomUUID: () => 'tab' },
    ResizeObserver: class { observe() {} disconnect() {} }, AbortController,
    setTimeout(fn) { timers.set(++timer, fn); return timer; }, clearTimeout: id => timers.delete(id),
    setInterval: () => 0, clearInterval: noop, sessionStorage: { setItem: (k, val) => stored.set(k, val) },
    location: { assign: value => { destination = value; } },
  });
  const routes = {
    './main.js': { el, api: async (url, body) => { requests.push([url, body]); return { result: {} }; },
      state: { files, settings: {}, folderSliders: {} }, store: { get: (_key, fallback) => fallback }, nice: String,
      thumbUrl: path => path, go: noop, toast: noop, getSchema: () => pending, setBar: noop, backButton: noop, gearButton: noop, openSheet: noop },
    './settings.js': { runOptions: () => ({}) }, './tools.js': { runTool: noop }, './share.js': { exportsDir: noop },
    './project.js': { getPhoto: () => ({ sliders: { exposure: 0.4 } }), updatePhoto: noop, undoPhoto: noop,
      redoPhoto: noop, canUndo: () => false, canRedo: () => false,
      subscribeProject: fn => { projectEvent = fn; return noop; }, persistProject: async () => true },
    './photo_tools.js': { createPhotoTools: () => ({ panels: [], mode: 'none', detailButton: () => el('button'),
      acceptPreview: noop, sync: noop, dispose: noop }) },
    './presets.js': { openPhotoPresets: noop },
  };
  const module = new vm.SourceTextModule(fs.readFileSync(path.resolve(__dirname, '../../src/autoedit/web/static/app/photo.js'), 'utf8'), { context });
  await module.link(name => new vm.SyntheticModule(Object.keys(routes[name]), function () {
    for (const [key, value] of Object.entries(routes[name])) this.setExport(key, value);
  }, { context }));
  await module.evaluate();
  const screen = module.namespace.screen, root = el('main');
  const find = predicate => nodes.findLast(predicate);
  const button = name => find(n => n.tag === 'button' && n.children.includes(name));
  screen.mount(root, { index: 0 });
  const frame = find(n => n.class === 'pv-frame');
  assert.equal(frame.dataset.showing, 'before');
  assert.equal(frame.children[0].src, files[0].path, 'the main view should decode the actual original of a RAW/JPEG pair');
  projectEvent({ type: 'load' });
  await sheet.fire('close');
  for (const fn of [...timers.values()]) fn();
  assert.equal(screen.key({ key: 's', ctrlKey: true }), false);
  assert.equal(screen.key({ key: 'm' }), false);
  assert.equal(requests.length, 0, 'opening, restoring or browsing a photo should never request a render');
  await button('Preview AutoEdit').fire('click');
  assert.equal(requests[0][0], '/preview');
  assert.equal(requests[0][1].photo_settings.exposure, .4);
  await button('Before / after').fire('click');
  const divider = find(n => n.role === 'slider');
  assert.equal(divider.hidden, false);
  const pointer = { pointerId: 1, button: 0, clientY: 30, target: divider };
  await frame.fire('pointerdown', { ...pointer, clientX: 112 });
  assert.equal(pointerCapture, pointer.pointerId);
  await frame.fire('pointermove', { ...pointer, clientX: 162 });
  assert.equal(divider['aria-valuenow'], '75');
  await frame.fire('pointerup', { ...pointer, clientX: 162 });
  await divider.fire('keydown', { key: 'Home' });
  await divider.fire('keydown', { key: 'ArrowRight' });
  assert.equal(divider['aria-valuenow'], '1');
  screen.unmount(); screen.mount(root, { index: 1 });
  assert.equal(find(n => n.class === 'pv-frame').dataset.showing, 'before');
  await button('Open original in Photo editor').fire('click');
  assert.equal(stored.get('autoedit.editor.open'), files[1].path);
  assert.equal(destination, '/editor/');
  screen.unmount();
}
check().catch(error => { console.error(error); process.exitCode = 1; });
