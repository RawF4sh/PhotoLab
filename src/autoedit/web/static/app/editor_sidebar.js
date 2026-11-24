// Keep the photo and its zoom mounted when a photographer changes tools.
export function createEditorSidebar(el, select) {
  const aside = el('aside', { id: 'editing-tools', class: 'side pv-side editor-sidebar', 'aria-label': 'Editing tools' });
  const panels = new Map();
  let active = 'ai';
  function show(id) {
    if (!panels.has(id)) return;
    active = id;
    for (const [key, {button, panel}] of panels) {
      const current = id === key;
      button.setAttribute('aria-expanded', String(current));
      panel.hidden = !current;
    }
  }
  for (const [id, label] of [['adjust','Adjust'],['ai','AI Edit'],['isolate','Isolate'],['learn','Learn'],['gemini','Ask Gemini']]) {
    const panel = el('section', { id: 'editor-panel-' + id, class: 'editor-section-content', 'aria-label': label });
    const button = el('button', { type: 'button', class: 'editor-section-tab', 'aria-controls': panel.id,
      on: {click: () => { show(id); select(id); }} }, label);
    panels.set(id, {button, panel});
    aside.append(el('h2', null, button), panel);
  }
  show(active);
  function closeDrawer() {
    if (!aside.classList.contains('drawer-open')) return false;
    aside.classList.remove('drawer-open'); drawer.setAttribute('aria-expanded','false');
    drawer.focus({preventScroll:true});
    return true;
  }
  const drawer = el('button', { type: 'button', class: 'line editor-tools-toggle', 'aria-controls': aside.id, 'aria-expanded': 'false',
    on: {click() {
      const open = !aside.classList.contains('drawer-open');
      aside.classList.toggle('drawer-open', open);
      drawer.setAttribute('aria-expanded', String(open));
    }} }, 'Tools');
  aside.prepend(el('div', {class:'editor-drawer-head'}, el('span',null,'Editing tools'),
    el('button', {type:'button',class:'link',on:{click:closeDrawer}}, 'Close tools')));
  return {aside, drawer, closeDrawer, show, active: () => active, panel: id => panels.get(id).panel};
}
