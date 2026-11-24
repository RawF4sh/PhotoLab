// LightCraft's full develop state lives in the AutoEdit project. The web catalog
// supplies fast previews and undo while this window is open.
const signature = settings => JSON.stringify(settings);
const checksum = entry => entry?.sha256 ?? null;
const originalCrop = {angle:0, rect:{x0:0, x1:1, y0:0, y1:1}};

function frameSettings(settings, geometry) {
  return {...settings, crop:{...settings.crop, geometry: geometry || originalCrop}};
}

export function nativeStage({command, api, tell, importPicture, removePicture}) {
  let record = null, saving = Promise.resolve(), exportResult = null;
  let busy = false, blocked = false;
  async function store(current) {
    if (!current || current !== record || blocked) return;
    const settings = await command('develop.get', {id: current.id});
    const encoded = signature(settings);
    if (encoded === current.saved) return;
    const isOriginal = encoded === signature(current.baseline);
    try {
      const result = await api('/editor/manual', {path:current.path, base:current.base,
        expected:checksum(current.manual), settings:isOriginal ? null : settings, baseline:current.baseline});
      current.manual = result.manual;
      current.saved = encoded;
    } catch (error) {
      if (/another window/.test(error.message)) blocked = true;
      throw error;
    }
  }
  async function flush() {
    const current = record;
    saving = saving.catch(() => {}).then(() => store(current));
    await saving;
    if (blocked) throw new Error('This photo changed in another window. Reopen it before saving.');
  }
  async function open(path, options, useAI, fullSize = false) {
    if (busy) throw new Error('Wait for the current photo to finish opening.');
    busy = true;
    const previous = record;
    try {
      if (!blocked) await flush();
      const [source, persisted] = await Promise.all([
        api(useAI ? '/editor/working' : '/editor/import', {path, options, full_size:fullSize}), api('/editor/manual?path='+encodeURIComponent(path))]);
      const photo = await importPicture(source);
      const current = await command('develop.get', {id:photo.id});
      const legacyEdit = !persisted.manual && !persisted.baseline && !photo.managed && (photo.edited || photo.history?.length);
      let baseline = persisted.baseline || photo.import_look || (legacyEdit ? {} : current);
      let restore = persisted.manual ? persisted.settings : persisted.baseline;
      // Transfer framing as a native crop on the uncropped source. A crop made by the
      // photographer in LightCraft takes precedence over AutoEdit's suggested frame.
      if (useAI && (source.native_crop || persisted.baseline?.crop)) {
        const inheritsFrame = !restore || signature(restore.crop) === signature(baseline.crop);
        baseline = frameSettings(baseline, source.native_crop);
        if (inheritsFrame) restore = frameSettings(restore || current, source.native_crop);
      }
      if (restore && signature(current) !== signature(restore)) {
        await command('develop.merge', {settings:restore, label:'Restore manual edit'});
      }
      const live = await command('develop.get', {id:photo.id});
      // Switching resolution changes the working bytes, but preserves the same AI stage.
      const base = fullSize && previous?.path === path ? previous.base : source.id;
      const rebased = persisted.manual && (signature(live) !== signature(persisted.settings) ||
        (persisted.base !== base && persisted.baseline));
      record = {id:photo.id, path, options, useAI, fullSize, base, source,
        baseline, manual:persisted.manual, lost:persisted.lost, saved:legacyEdit || rebased ? null : signature(live), photo};
      blocked = false;
      if (legacyEdit || rebased) await store(record);
      if (previous && previous.id !== photo.id) await removePicture(previous.photo);
      // Cache metadata is session-only. Keys are never written by this module.
      sessionStorage.setItem('autoedit.finetune.source', path);
      sessionStorage.setItem('autoedit.finetune.stage', JSON.stringify({options, useAI}));
      return record;
    } catch (error) {
      if (previous) {
        await command('library.select', {ids:[previous.id]}).catch(() => {});
        await command('view.detail').catch(() => {});
      }
      throw error;
    } finally { busy = false; }
  }

  async function inspectDetail() {
    if (busy) throw new Error('Wait for the current photo to open.');
    const current = record;
    const active = (await command('library.state')).selection?.active;
    if (!current || active !== current.id) throw new Error('Open this photo through AutoEdit to inspect its original pixels.');
    const {pan} = await command('view.navigate');
    if (!current.fullSize) await open(current.path, current.options, current.useAI, true);
    await command('view.navigate', {zoom:{percent:100}, pan});
  }

  async function compareOriginal(compare) {
    const r = record;
    if (!r?.useAI) return compare(null);
    if (busy) throw new Error('Wait for the current photo to open.');
    await flush();
    busy = true;
    let original;
    try {
      // A hidden object must stay hidden on both sides of the comparison.
      const source = await api('/editor/import', {path:r.path, options:r.options, full_size:r.fullSize});
      original = await importPicture(source);
      await command('library.select', {ids:[r.id]});
      await command('view.detail');
      return await compare(original);
    } finally {
      try {
        if (original && original.id !== r.id) await removePicture(original);
      } finally { busy = false; }
    }
  }
  function receive(data) {
    if (!exportResult || data.name !== exportResult.name) return false;
    exportResult.resolve(data.bytes);
    return true;
  }
  async function renderBytes() {
    const name = 'autoedit-' + crypto.randomUUID() + '.tif';
    let timer;
    const bytes = new Promise((resolve, reject) => {
      exportResult = {name, resolve};
      timer = setTimeout(() => reject(new Error('The full-size export timed out. Your edit is saved.')), 180000);
    });
    try {
      const [, result] = await Promise.all([
        command('app.export', {path:name, format:'tiff', bitDepth:16, colorSpace:'sRGB'}), bytes]);
      return result;
    } finally { clearTimeout(timer); exportResult = null; }
  }
  async function exportPhoto() {
    if (busy) return;
    await flush();
    const r = record;
    const active = (await command('library.state')).selection?.active;
    if (!r || active !== r.id) throw new Error('Open or link the original through AutoEdit before exporting.');
    busy = true;
    let fullPhoto;
    try {
      if (!r.manual && r.useAI) {
        return await api('/run-one', {...r.options, file:r.path, local_only:true, decider:'rules', translator:'off'});
      }
      const source = await api('/editor/working', {path:r.path, full_size:true,
        options:{...r.options, editor_original:!r.useAI}});
      fullPhoto = await importPicture(source);
      if (r.manual) {
        let settings = JSON.parse(r.saved);
        if (r.useAI && signature(settings.crop) === signature(r.baseline.crop)) {
          settings = frameSettings(settings, source.native_crop);
        }
        await command('develop.merge', {settings, label:'Apply saved manual edit'});
      }
      const bytes = await renderBytes();
      const query = new URLSearchParams({path:r.path, ticket:source.ticket, manual:checksum(r.manual) || ''});
      const response = await fetch('/api/v1/editor/export?' + query, {method:'POST',
        headers:{'Content-Type':'image/tiff'}, body:bytes});
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.error || 'Export failed.');
      return answer;
    } finally {
      try {
        await command('library.select', {ids:[r.id]});
        await command('view.detail');
        if (fullPhoto && fullPhoto.id !== r.id) await removePicture(fullPhoto);
      } finally { busy = false; }
    }
  }
  return {open, flush, receive, exportPhoto, compareOriginal, inspectDetail, get current() { return record; }, get busy() { return busy; },
    tick() {
      if (busy || blocked || !record || this.polling) return;
      this.polling = true;
      flush().catch(e => tell(e.message, true)).finally(() => { this.polling = false; });
    }};
}
