// Named whole-photo choices shared by the folder and photo screens.
import { api, el, openSheet, toast, nice } from './main.js';

export function openPhotoPresets({ getOptions, onApply }) {
  return openSheet('My presets', (body, close) => {
    const list = el('div', { class: 'saved-presets' });
    const note = el('p', { class: 'fine', role: 'status' }, 'Loading your presets…');
    const name = el('input', { type: 'text', id: 'preset-name', maxLength: 80, placeholder: 'e.g. Soft afternoon' });
    const save = el('button', { type: 'submit', class: 'primary' }, 'Save current settings');
    let busy = false;
    const describe = settings => [settings.look ? nice(settings.look) : 'Default look',
      settings.intensity === undefined ? '' : 'strength ' + settings.intensity,
      Object.keys(settings.sliders || {}).filter(key => settings.sliders[key] !== 0).length + ' adjustments'].filter(Boolean).join(' · ');
    const take = async (fn, control) => {
      if (busy) return; busy = true; if (control) control.disabled = true;
      try { await fn(); } catch (error) { note.textContent = error.message; }
      finally { busy = false; if (control?.isConnected) control.disabled = false; }
    };
    const load = async () => {
      const result = await api('/edit-presets');
      if (!body.isConnected) return;
      list.replaceChildren();
      note.textContent = result.presets.length ? 'Apply a preset, or save your current settings below.' : 'No presets yet. Save the settings you want to reuse.';
      result.presets.forEach(preset => {
        const apply = el('button', { type: 'button', class: 'line', on: { click: e => take(async () => {
          await onApply(preset.settings); close(); toast('Applied ' + preset.name + '.');
        }, e.currentTarget) } }, preset.name, el('small', null, describe(preset.settings)));
        const remove = el('button', { type: 'button', class: 'link', 'aria-label': 'Delete preset ' + preset.name,
          on: { click: e => {
            if (e.currentTarget.dataset.confirm !== 'yes') { e.currentTarget.dataset.confirm = 'yes'; e.currentTarget.textContent = 'Confirm delete'; return; }
            take(async () => {
              const response = await fetch('/api/v1/edit-presets/' + encodeURIComponent(preset.id), { method: 'DELETE' });
              const data = await response.json(); if (!response.ok || data.error) throw new Error(data.error || 'Could not delete this preset.');
              await load();
            }, e.currentTarget);
          } } }, 'Delete');
        list.append(el('div', { class: 'saved-preset' }, apply, remove));
      });
    };
    const form = el('form', { class: 'preset-save', on: { submit: e => {
      e.preventDefault();
      if (!name.value.trim()) { name.focus(); note.textContent = 'Give your preset a name.'; return; }
      take(async () => { await api('/edit-presets', { name: name.value.trim(), settings: getOptions() }); name.value = ''; await load(); }, save);
    } } }, el('label', { htmlFor: 'preset-name' }, 'Name this preset'), name, save);
    body.append(el('p', { class: 'fine' }, 'Presets reuse your look, strength and whole-photo adjustments. Selections, crops and reference photos stay with the original photo.'), note, list, form);
    load().catch(error => { if (body.isConnected) note.textContent = error.message; });
  });
}
