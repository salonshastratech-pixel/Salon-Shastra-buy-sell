// QA: layout scan — paste into the browser console of a signed-in TeamOS page (or run through a browser tool).
// Loads each page in a hidden frame at each width and reports: the page scrolling sideways, controls sticking out
// of their card, boxes in one form row not lined up, button labels wrapping, dropdowns cutting their text,
// overlapping buttons, broken images. Usage:
//   await layoutScan(['/', '/sellers/', '/team'], [1280, 390])
window.layoutScan = async function (paths, widths) {
  const ctx = document.createElement('canvas').getContext('2d');
  const out = [];
  for (const W of widths) for (const path of paths) {
    let html;
    try {
      const r = await fetch(path);
      if (!r.ok) { out.push(`${W} ${path}: HTTP ${r.status}`); continue; }
      html = await r.text();
    } catch (e) { out.push(`${W} ${path}: ${e}`); continue; }
    const fr = document.createElement('iframe');
    fr.style.cssText = `position:fixed;left:-5000px;top:0;width:${W}px;height:900px;border:0`;
    document.body.appendChild(fr);
    fr.srcdoc = html.replace('<head>', '<head><base href="' + location.origin + '/">');
    await new Promise(res => fr.onload = res);
    await new Promise(res => setTimeout(res, 500));
    const d = fr.contentDocument, w = fr.contentWindow, probs = new Map();
    const add = k => probs.set(k, (probs.get(k) || 0) + 1);
    const vis = el => {
      const r = el.getBoundingClientRect(), cs = w.getComputedStyle(el);
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' &&
        !el.closest('[hidden]') && !el.closest('details:not([open]) > :not(summary)');
    };
    const name = el => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += '#' + el.id;
      else if (typeof el.className === 'string' && el.className.trim()) s += '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.');
      const h = el.closest('.card') && el.closest('.card').querySelector('h2');
      return s + (h ? ` in "${h.textContent.trim().slice(0, 28)}"` : '');
    };
    if (d.documentElement.scrollWidth > W + 2) add(`page scrolls sideways (${d.documentElement.scrollWidth}px)`);
    for (const el of d.querySelectorAll('main input:not([type=hidden]), main select, main textarea, main .btn, main button')) {
      if (!vis(el)) continue;
      const card = el.closest('.card');
      if (!card || el.closest('.table-wrap')) continue;
      const r = el.getBoundingClientRect(), c = card.getBoundingClientRect();
      if (r.right > c.right + 1 || r.left < c.left - 1) add(`sticks out of card: ${name(el)}`);
    }
    for (const g of d.querySelectorAll('main .form-grid')) {
      const rows = new Map();
      for (const f of g.children) {
        if (!vis(f)) continue;
        const ctl = f.querySelector('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select');
        if (!ctl) continue;
        const top = Math.round(f.getBoundingClientRect().top);
        const key = [...rows.keys()].find(k => Math.abs(k - top) < 40) ?? top;
        if (!rows.has(key)) rows.set(key, []);
        rows.get(key).push(ctl);
      }
      for (const ctls of rows.values()) if (ctls.length > 1) {
        const t = ctls.map(c => Math.round(c.getBoundingClientRect().top));
        if (Math.max(...t) - Math.min(...t) > 3) add(`form row not aligned: ${name(ctls[0])}`);
      }
    }
    for (const b of d.querySelectorAll('main .btn, main button, .top .btn')) {
      if (!vis(b) || b.classList.contains('cbtn')) continue;
      const cs = w.getComputedStyle(b);
      if (cs.whiteSpace === 'nowrap') continue;
      const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.3;
      const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + 2 * parseFloat(cs.borderTopWidth);
      const t = b.textContent.trim();
      if (t.length > 1 && t.length < 40 && b.getBoundingClientRect().height - pad > lh * 1.6) add(`button text wraps: "${t.slice(0, 30)}"`);
    }
    for (const s of d.querySelectorAll('main select')) {
      if (!vis(s) || s.selectedIndex < 0) continue;
      const cs = w.getComputedStyle(s);
      ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const tw = ctx.measureText(s.options[s.selectedIndex].text).width;
      if (tw > s.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 16) add(`dropdown cuts text: "${s.options[s.selectedIndex].text.slice(0, 26)}" in ${name(s)}`);
    }
    for (const grp of d.querySelectorAll('main .inline, main .actions, .top .me')) {
      const kids = [...grp.querySelectorAll(':scope > a, :scope > button, :scope > form, :scope > details')].filter(vis).map(k => k.getBoundingClientRect());
      let hit = false;
      for (let i = 0; i < kids.length && !hit; i++) for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i], c = kids[j];
        if (a.left < c.right - 2 && c.left < a.right - 2 && a.top < c.bottom - 2 && c.top < a.bottom - 2) { add(`overlap in ${name(grp)}`); hit = true; break; }
      }
    }
    for (const im of d.querySelectorAll('img')) if (im.complete && im.naturalWidth === 0 && vis(im)) add(`broken image ${im.getAttribute('src')}`);
    fr.remove();
    out.push(`${W} ${path}: ` + (probs.size ? [...probs].map(([k, v]) => k + (v > 1 ? ' ×' + v : '')).join(' | ') : 'ok'));
  }
  return out.join('\n');
};
