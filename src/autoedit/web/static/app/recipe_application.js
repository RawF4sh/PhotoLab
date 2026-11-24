// Build one undoable photo patch. An ordered plan owns at most one recipe per photo.
import { checkStepCapacity } from './edit_steps.js';

const properties = ['look', 'intensity'];

export function recipePatch(photo, recipe, planned = false, missing = []) {
  const steps = [...(photo.edit_steps || [])];
  const patch = {};
  const manual = new Map();
  if (planned) {
    const index = steps.findIndex(step => step.plan);
    if (index >= 0) {
      const { prior, applied, previous } = steps[index].plan;
      // Preserve look/strength edits the photographer made after the last plan.
      for (const key of Object.keys(applied)) {
        if (photo[key] === applied[key]) patch[key] = prior[key];
        else manual.set(key, applied[key]);
      }
      if (previous) steps.splice(index, 1, previous);
      else steps.splice(index, 1);
    } else if (!recipe) return null;
  }
  if (recipe) {
    const id = 'recipe-' + recipe.id;
    const index = steps.findIndex(step => step.id === id);
    const settings = recipe.settings;
    const entry = { id, label: Array.from(recipe.name).slice(0, 60).join(''), origin: 'recipe', on: true,
      amount: 1, global: settings.sliders || {}, regions: settings.region_settings || {}, ref: recipe.id };
    const prior = {}, applied = {};
    for (const key of properties) {
      if (planned && manual.has(key)) {
        // Remember the last automatic value so a later replan still respects the manual edit.
        applied[key] = manual.get(key);
        if (photo[key] !== undefined) prior[key] = photo[key];
        continue;
      }
      if (settings[key] === undefined) continue;
      const value = Object.hasOwn(patch, key) ? patch[key] : photo[key];
      if (value !== undefined) prior[key] = value;
      applied[key] = patch[key] = settings[key];
    }
    if (planned) {
      entry.plan = { prior, applied };
      if (index >= 0) entry.plan.previous = steps[index];
      if (missing.length) entry.plan.missing = missing;
    }
    if (index < 0) steps.push(entry); else steps[index] = entry;
  }
  checkStepCapacity(photo, steps);
  return { ...patch, edit_steps: steps.length ? steps : undefined };
}
