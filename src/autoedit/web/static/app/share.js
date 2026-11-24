// The Share sheet over the Folder. Always enabled: a folder with no export yet gets the server's own message in the sheet.
import { el, state, joinPath, baseName, openSheet } from './main.js';
import { runTool, reveal, showResult, fileManager } from './tools.js';

// ponytail: assumes the default output folder; an export preset with a subdir puts photos one level down.
export const exportsDir = () => joinPath(state.dir, 'AutoEdit Exports');

export function openShare() {
  openSheet('Share ' + baseName(state.dir), body => {
    const res = el('div', { class: 'shres', role: 'status' });
    const jobs = [];   // the buttons that start a job: off while one runs
    const run = async (tool, body_, then) => {
      res.textContent = 'Working…'; jobs.forEach(b => { b.disabled = true; });
      try {
        const r = await runTool(tool, body_, m => { res.textContent = m; });
        if (then) then(r); else showResult(res, r);
      } catch (e) { res.textContent = e.message; }
      jobs.forEach(b => { b.disabled = false; });
    };
    const f = { folder: exportsDir() };
    const make = el('button', { type: 'button', class: 'primary', on: { click: () => run('gallery', f, r => showResult(res, r, r.page, 'Show gallery')) } }, 'Make gallery');
    jobs.push(make);   // declare, keywords and gallery read the exports' sidecars
    const row = (title, fn, job = true) => {
      const b = el('button', { type: 'button', class: 'shrow', on: { click: fn } }, title);
      if (job) jobs.push(b);
      return b;
    };
    body.append(
      row('Check before sharing', () => run('check', { folder: state.dir, backend: state.finder }, r => showResult(res, r, r.report, 'Show report'))),
      row('Create edit declaration', () => run('declare', f)),
      row('Write keyword files (.xmp)', () => run('keywords', f)),
      row('Show exports in ' + fileManager, () => { res.textContent = ''; reveal(exportsDir()).catch(e => { res.textContent = e.message; }); }, false),
      res,
      make);
  });
}
