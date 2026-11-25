// One native canvas per open photo. The local and teaching views keep their own
// preview surface so a lesson can show intermediate pixels without altering the edit.
export function createManualPanel({el, path, getStage, saveProject, onSaved, onExport}) {
  const frame = el('iframe', {id:'photo-manual-adjust', class:'editor-manual-canvas', title:'Manual adjustments', inert:true});
  let active = false, ready = false, shown = false, signature = '', pending = null, exportPending = false;
  const host = () => frame.contentWindow?.autoeditManual;
  const message = el('span', null, 'Opening manual controls…');
  const retry = el('button', {type:'button', class:'line', hidden:true,
    on:{click:() => render().catch(failed)}}, 'Try again');
  const status = el('div', {class:'editor-manual-status', role:'status', hidden:true}, message, retry);
  function display(visible) {
    shown = visible;
    frame.inert = !(active && visible);
    frame.classList.toggle('visible', !frame.inert);
    frame.setAttribute('aria-hidden', String(frame.inert));
    status.hidden = !active || visible;
  }
  function key() { return JSON.stringify(getStage()); }
  async function render() {
    if (!active || !ready) return;
    if (pending) return pending;
    const requested = key();
    if (requested === signature) { display(true); return; }
    display(false); retry.hidden = true; message.textContent = 'Opening manual controls…';
    pending = (async () => {
      if (!await saveProject()) throw Error('Save the project before opening manual adjustments.');
      const {options, useAI} = JSON.parse(requested);
      const current = await host().open(path, options, useAI);
      onSaved(current.manual);
      signature = JSON.stringify({...JSON.parse(requested), manual:current.manual?.sha256 || null});
      display(true);
    })();
    try { await pending; } finally { pending = null; }
  }
  async function exportPhoto() {
    if (!ready) { exportPending = true; return; }
    await render();
    if (!active || !signature) return;
    if (host().busy) throw Error('An editor action is already in progress.');
    const result = await host().exportPhoto();
    if (result) await onExport(result);
  }
  function failed(error) {
    message.textContent = error.message || String(error);
    retry.hidden = false;
    if (active) display(false);
  }
  frame.addEventListener('manual-ready', async () => {
    ready = true; signature = '';
    try {
      await render();
      if (exportPending && active) await exportPhoto();
    } catch (error) { failed(error); }
    finally { exportPending = false; }
  });
  frame.addEventListener('manual-attention', () => {
    ready = false; signature = ''; exportPending = false; display(true);
  });
  frame.addEventListener('manual-loading', () => { ready = false; signature = ''; display(false); });
  frame.addEventListener('manual-saved', ({detail}) => {
    if (detail.path !== path) return;
    onSaved(detail.manual);
    if (signature) signature = JSON.stringify({...JSON.parse(signature), manual:detail.manual?.sha256 || null});
  });
  return {
    frame, status,
    async activate() {
      active = true; display(shown);
      if (!frame.getAttribute('src')) frame.src = '/editor/panel/';
      try { await render(); } catch (error) { failed(error); throw error; }
    },
    async leave() {
      exportPending = false;
      if (pending) await pending.catch(() => {});
      if (ready && signature) {
        if (host().busy) throw Error('Wait for the editor action to finish.');
        await host().flush();
      }
      active = false; display(shown);
    },
    exportPhoto, get active() { return active; }
  };
}
