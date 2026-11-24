// LightCraft v0.21.1's render worker reads its own stored original and renders settings
// without selecting photos, rewriting edits, exporting files, or changing undo history.
const $ = id => document.getElementById(id);

function cameraSettings(photo) {
  const settings = {};
  const relative = photo.kind === 'raw' && !photo.preview_only && photo.develop.wb?.scale !== 'kelvin';
  const white = relative ? [6500, 0] : photo.as_shot_wb;
  if (white) {
    settings.wb = {mode: 'asShot', temp: white[0], tint: white[1]};
    if (!relative && photo.kind === 'raw' && !photo.preview_only) settings.wb.scale = 'kelvin';
    settings.detail = {sharpen_amount: 40, nr_color: 25, nr_outliers: true};
  }
  if (photo.embedded_lens) settings.optics = {lens_profile: true};
  return settings;
}

function importSettings(photo) {
  // Upstream's before side retains the current crop, orientation and process version.
  return {...(photo.import_look || cameraSettings(photo)), process: photo.develop.process,
    crop: photo.develop.crop, orientation: photo.develop.orientation};
}

async function render(frame, photo, store, settings, size, signal) {
  const raw = photo.source.type === 'demo' || (photo.kind === 'raw' && !photo.preview_only);
  const job = {
    level: 'preview', origin: photo.source, max_edge: size, raw,
    as_shot_temp: photo.as_shot_wb?.[0] || 6500, as_shot_tint: photo.as_shot_wb?.[1] || 0,
    lens: photo.embedded_lens || null, native_long: Math.max(photo.width, photo.height) || null,
    max_w: size, max_h: size, draft: false, apply_crop: true, thumb_cached: false
  };
  const worker = new Worker('/editor/release/worker.js', {type:'module'});
  let timeout;
  let onAbort;
  try {
    return await new Promise((resolve, reject) => {
      const frames = [];
      const post = () => {
        const id = frames.length;
        worker.postMessage({id, job:JSON.stringify({...job, settings: settings[id]})});
      };
      onAbort = () => reject(new DOMException('Comparison closed', 'AbortError'));
      signal?.addEventListener('abort', onAbort, {once:true});
      if (signal?.aborted) return onAbort();
      timeout = setTimeout(() => reject(new Error('Photo preview timed out. Try a smaller image or reopen the editor.')), 90000);
      worker.onerror = event => {
        if (/unreachable|out of memory/i.test(event.message)) {
          const error = new Error('LightCraft could not render this photo in the browser. For a camera RAW, use Open RAW as TIFF.');
          error.name = 'PhotoRenderError';
          reject(error);
        } else reject(new Error(event.message || 'Could not start the photo renderer.'));
      };
      worker.onmessage = ({data}) => {
        if (data.ready) { post(); return; }
        if (!data.ok) { reject(new Error(data.err || 'Could not render this photo.')); return; }
        const canvas = document.createElement('canvas');
        canvas.width = data.w;
        canvas.height = data.h;
        const pixels = new Uint8ClampedArray(data.rgba.buffer, data.rgba.byteOffset, data.rgba.byteLength);
        canvas.getContext('2d').putImageData(new ImageData(pixels, data.w, data.h), 0, 0);
        frames.push(canvas);
        if (frames.length === settings.length) resolve(frames); else post();
      };
      worker.postMessage({module:frame.contentWindow.lightcraftModule, store});
    });
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', onAbort);
    worker.terminate();
  }
}

export async function thumbnail(frame, photo, stats) {
  const [canvas] = await render(frame, photo, stats.storage, [photo.develop], 512);
  return canvas;
}

const dialog = $('comparison'), pair = $('compare-pair'), area = $('canvas-area'), wipe = $('wipe');
let current;
function position() {
  $('before').style.clipPath = 'inset(0 ' + (100 - wipe.valueAsNumber) + '% 0 0)';
  $('divider').style.left = wipe.value + '%';
  wipe.setAttribute('aria-valuetext', wipe.value + '% original');
}
function fit() {
  const edited = $('edited');
  const scale = Math.min(area.clientWidth / edited.width, area.clientHeight / edited.height);
  pair.style.width = Math.round(edited.width * scale) + 'px';
  pair.style.height = Math.round(edited.height * scale) + 'px';
}

new ResizeObserver(() => { if (dialog.open && !pair.hidden) fit(); }).observe(area);
wipe.addEventListener('input', position);
// The default range thumb has an inset on WebKit. Map the whole photo, including its edges.
function drag(event) {
  const bounds = pair.getBoundingClientRect();
  wipe.value = String(Math.max(0, Math.min(100, (event.clientX - bounds.left) / bounds.width * 100)));
  position();
}
wipe.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  event.preventDefault(); wipe.focus(); wipe.setPointerCapture(event.pointerId); drag(event);
});
wipe.addEventListener('pointermove', event => { if (wipe.hasPointerCapture(event.pointerId)) drag(event); });
wipe.addEventListener('pointerup', event => {
  if (wipe.hasPointerCapture(event.pointerId)) { drag(event); wipe.releasePointerCapture(event.pointerId); }
});
$('close').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', () => {
  current?.abort();
  // Release full-size preview backing stores when returning to editing.
  for (const id of ['before', 'edited']) $(id).width = 1;
});

export async function comparePhoto(frame, photo, stats) {
  current?.abort();
  const controller = new AbortController();
  current = controller;
  pair.hidden = true;
  $('compare-feedback').hidden = false;
  $('native-split').hidden = true;
  $('compare-status').textContent = 'Rendering comparison…';
  dialog.showModal();
  try {
    const size = Math.min(1536, Math.max(512, area.clientWidth, area.clientHeight));
    const frames = await render(frame, photo, stats.storage, [importSettings(photo), photo.develop], size, controller.signal);
    if (controller.signal.aborted || !dialog.open) return;
    frames.forEach((image, i) => {
      const canvas = $(i ? 'edited' : 'before');
      canvas.width = image.width; canvas.height = image.height;
      canvas.getContext('2d').drawImage(image, 0, 0);
    });
    $('compare-feedback').hidden = true;
    pair.hidden = false;
    wipe.value = '50';
    fit(); position(); wipe.focus();
  } catch (error) {
    if (!controller.signal.aborted) {
      $('compare-status').textContent = error.message || String(error);
      $('native-split').hidden = error.name === 'PhotoRenderError';
    }
  }
}
