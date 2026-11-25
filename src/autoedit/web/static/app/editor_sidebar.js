// Tool navigation stays visible while each section uses its own part of the canvas.
const sections = [['adjust','Adjust'], ['ai','AI Edit'], ['isolate','Isolate'], ['learn','Learn'], ['gemini','Ask Gemini']];

export function createEditorSidebar(el, select) {
  const rail = el('nav', {class:'editor-rail', 'aria-label':'Editing tools'});
  const aside = el('aside', {class:'side pv-side editor-sidebar', 'aria-label':'Tool settings'});
  const panels = new Map();
  let active;
  function show(id) {
    active = id;
    for (const [key, {button, panel}] of panels) {
      const selected = id === key;
      button.setAttribute('aria-expanded', String(selected));
      panel.hidden = !selected;
    }
    // Adjust has its own native inspector beside the photograph.
    aside.hidden = id === 'adjust';
  }
  for (const [id,label] of sections) {
    const panel = el('section', {id:'editor-panel-' + id, class:'editor-section-content', 'aria-label':label});
    const button = el('button', {type:'button', class:'editor-section-tab', 'aria-controls':id === 'adjust' ? 'photo-manual-adjust' : panel.id,
      on:{click:() => select(id)}}, label);
    panels.set(id,{button,panel});
    rail.append(button); aside.append(panel);
  }
  show('adjust');
  return {rail, aside, show, active:() => active, panel:id => panels.get(id).panel};
}
