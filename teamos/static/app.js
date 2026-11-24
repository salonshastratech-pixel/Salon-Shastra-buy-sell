// Progressive enhancement only — every action works without JavaScript.
(function () {
  // Mobile menu
  var t = document.querySelector('.menu-toggle');
  if (t) t.addEventListener('click', function () {
    var open = document.querySelector('.side').classList.toggle('open');
    t.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  // Phone menu: tap outside it or press Escape to close
  function closeSide() {
    var s = document.querySelector('.side');
    if (s && s.classList.contains('open')) { s.classList.remove('open'); if (t) t.setAttribute('aria-expanded', 'false'); }
  }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSide(); });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.side') && !e.target.closest('.menu-toggle')) closeSide();
  });

  // Confirm dangerous actions: <form data-confirm="...">
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
  });

  // Typing a value switches the answer state to "Answered"; choosing Unknown etc. clears the value.
  document.querySelectorAll('[data-value-for]').forEach(function (input) {
    var state = document.querySelector('[name="' + input.dataset.valueFor + '__state"]');
    if (!state) return;
    input.addEventListener('input', function () { if (input.value) state.value = 'value'; });
    state.addEventListener('change', function () { if (state.value && state.value !== 'value') input.value = ''; });
  });

  // Conditional fields: <div data-show-if="business_model=franchise"> (select or tap-button radios)
  function valueOf(name) {
    var src = document.querySelector('[name="' + name + '"]');
    if (!src) return null;
    if (src.type === 'radio') { var c = document.querySelector('[name="' + name + '"]:checked'); return c ? c.value : ''; }
    return src.value;
  }
  function refresh() {
    document.querySelectorAll('[data-show-if]').forEach(function (el) {
      var parts = el.dataset.showIf.split('='), v = valueOf(parts[0]);
      if (v === null) return;
      el.classList.toggle('hide', v !== parts[1]);
    });
  }
  document.addEventListener('change', refresh);
  refresh();

  // Tap-button answers: tapping a selected button again clears it; answering clears "I don't know" and vice versa.
  document.querySelectorAll('.chips input[type="radio"]').forEach(function (r) {
    r.addEventListener('click', function () {
      if (r.dataset.was === '1') { r.checked = false; r.dataset.was = ''; refresh(); return; }
      document.querySelectorAll('[name="' + r.name + '"]').forEach(function (o) { o.dataset.was = ''; });
      r.dataset.was = '1';
      var field = r.closest('.qf'); if (!field) return;
      var isState = /__state$/.test(r.name), base = r.name.replace(/__state$/, '');
      if (isState) {
        field.querySelectorAll('[name="' + base + '"]').forEach(function (o) { if (o.type === 'radio') { o.checked = false; o.dataset.was = ''; } else o.value = ''; });
      } else if (!/__importance$/.test(r.name)) {
        field.querySelectorAll('[name="' + base + '__state"]').forEach(function (o) { o.checked = false; o.dataset.was = ''; });
      }
    });
    if (r.checked) r.dataset.was = '1';
  });
  document.querySelectorAll('.qf input:not([type]), .qf input[type="date"], .qf textarea, .qf select').forEach(function (i) {
    i.addEventListener('input', function () {
      if (!i.value) return;
      i.closest('.qf').querySelectorAll('[name$="__state"]').forEach(function (o) { o.checked = false; o.dataset.was = ''; });
    });
  });

  // Customer-form wording in the chosen language (set by the server on <body data-form-text>; CSP forbids inline JS)
  function formText() {
    try { return JSON.parse(document.body.dataset.formText || '{}'); } catch (e) { return {}; }
  }
  // Rupee amounts: show what was typed in words (45k -> ₹45,000; 25L -> ₹25 lakh)
  function inrWords(raw) {
    var v = (raw || '').toLowerCase().replace(/[,₹\s]|rs\.?/g, '');
    var m = v.match(/^(\d+(?:\.\d+)?)(l|lac|lakh|lakhs|cr|crore|crores|k|thousand|thousands)?$/);
    if (!m) return v ? 'Write a number like 45000, 45k or 25L' : '';
    var mult = {l: 1e5, lac: 1e5, lakh: 1e5, lakhs: 1e5, cr: 1e7, crore: 1e7, crores: 1e7, k: 1e3, thousand: 1e3, thousands: 1e3}[m[2]] || 1;
    var n = Math.round(parseFloat(m[1]) * mult);
    var T = formText();
    var words = n >= 1e7 ? (n / 1e7).toFixed(2).replace(/\.?0+$/, '') + ' ' + (T.crore || 'crore') : n >= 1e5 ? (n / 1e5).toFixed(2).replace(/\.?0+$/, '') + ' ' + (T.lakh || 'lakh') : '';
    return '= ₹' + n.toLocaleString('en-IN') + (words ? ' (' + words + ')' : '');
  }
  document.querySelectorAll('input[data-inr]').forEach(function (i) {
    var out = i.closest('.qf').querySelector('.inr-words');
    var upd = function () { if (out) out.textContent = inrWords(i.value); };
    i.addEventListener('input', upd); upd();
  });

  // Photos: shrink big phone photos before upload (keeps them under the 5 MB limit and quick on mobile data)
  document.querySelectorAll('input[type="file"][data-compress]').forEach(function (inp) {
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      var slot = inp.closest('.photo-slot');
      if (slot) slot.classList.toggle('picked', !!f);
      if (!f || !/^image\/(jpeg|png|webp|heic|heif)/i.test(f.type) || f.size < 900 * 1024 || !window.DataTransfer) return;
      var img = new Image(), url = URL.createObjectURL(f);
      img.onload = function () {
        var scale = Math.min(1, 1600 / Math.max(img.width, img.height)), c = document.createElement('canvas');
        c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(function (b) {
          URL.revokeObjectURL(url);
          if (!b) return;
          var dt = new DataTransfer();
          dt.items.add(new File([b], f.name.replace(/\.[^.]+$/, '') + '.jpg', {type: 'image/jpeg'}));
          inp.files = dt.files;
        }, 'image/jpeg', 0.82);
      };
      img.src = url;
    });
  });

  // Long forms: one step at a time with a progress bar (everything is still one normal form underneath)
  var stepper = document.querySelector('form[data-stepper]');
  if (stepper) {
    var steps = Array.prototype.slice.call(stepper.querySelectorAll('.step'));
    var bar = stepper.querySelector('.stepbar'), nav = stepper.querySelector('.stepnav'), cur = 0;
    var firstErr = steps.findIndex(function (s) { return s.querySelector('.has-err'); });
    if (firstErr >= 0) cur = firstErr;
    var show = function (i) {
      cur = Math.max(0, Math.min(steps.length - 1, i));
      steps.forEach(function (s, k) { s.hidden = k !== cur; });
      bar.hidden = false; nav.hidden = false;
      bar.querySelector('.stepbar-fill').style.width = Math.round(100 * (cur + 1) / steps.length) + '%';
      var T = formText();
      var pos = T.te ? (steps.length + ' ' + T.of + ' ' + T.step + ' ' + (cur + 1)) : ('Step ' + (cur + 1) + ' of ' + steps.length);
      bar.querySelector('.stepbar-text').textContent = pos + ' · ' + (steps[cur].dataset.title || '');
      nav.querySelector('[data-step="-1"]').hidden = cur === 0;
      nav.querySelector('[data-step="1"]').hidden = cur === steps.length - 1;
      window.scrollTo(0, stepper.offsetTop - 10);
    };
    nav.querySelectorAll('[data-step]').forEach(function (b) {
      b.addEventListener('click', function () { show(cur + parseInt(b.dataset.step, 10)); });
    });
    show(cur);
  }

  // Stage selector: show reason / follow-up inputs only when needed
  var stageSel = document.querySelector('#stage-to');
  if (stageSel) {
    var upd = function () {
      var opt = stageSel.options[stageSel.selectedIndex];
      document.querySelector('#stage-reason').classList.toggle('hide', !(opt && opt.dataset.reason === '1'));
      document.querySelector('#stage-followup').classList.toggle('hide', !(opt && (opt.dataset.followup === '1' || stageSel.value === 'nurture')));
    };
    stageSel.addEventListener('change', upd); upd();
  }
  // Press "/" anywhere (outside a text box) to jump to the global search.
  document.addEventListener('keydown', function (e) {
    var t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
      var q = document.querySelector('header input[type="search"], .top input[type="search"], input[name="q"]');
      if (q) { e.preventDefault(); q.focus(); q.select(); }
    }
  });
  // Message templates: show only the value boxes the chosen template needs.
  var tpl = document.querySelector('#tpl');
  if (tpl) {
    var showVars = function () {
      var need = (tpl.options[tpl.selectedIndex].dataset.fields || '').split(',');
      document.querySelectorAll('.tplvar').forEach(function (el) {
        var on = need.indexOf(el.dataset.var) !== -1;
        el.classList.toggle('hide', !on);
        el.querySelector('input').disabled = !on;
      });
    };
    tpl.addEventListener('change', showVars); showVars();
  }
  // Record summary: "Draft for me" asks the server for a draft; the person edits it and presses Save.
  document.querySelectorAll('form[data-summary] [data-draft-url]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var form = btn.closest('form'), box = form.querySelector('textarea[name=summary]'), note = form.querySelector('.summary-note');
      if (box.value.trim() && !window.confirm('Replace the current text with a new draft?')) return;
      btn.disabled = true; note.textContent = 'Drafting…';
      fetch(btn.dataset.draftUrl, {method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRF-Token': form.querySelector('[name=csrf_token]').value}})
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          box.value = d.text; box.rows = 5; form.querySelector('[name=source]').value = d.source;
          note.textContent = d.note; box.focus();
        })
        .catch(function () { note.textContent = 'Could not draft right now — please write it yourself.'; })
        .finally(function () { btn.disabled = false; });
    });
  });
  // Tap-to-call: the phone dials; this page then jumps to "Log a call" so the outcome is recorded straight away.
  document.querySelectorAll('[data-call-log]').forEach(function (a) {
    a.addEventListener('click', function () {
      var box = document.getElementById('log-call');
      if (!box) return;
      setTimeout(function () {
        box.scrollIntoView({behavior: 'smooth', block: 'center'});
        box.classList.add('pulse');
        var sel = box.querySelector('select[name=outcome]');
        if (sel) sel.focus();
      }, 400);
    });
  });
  // Selects marked data-autosubmit submit their form on change (CSP forbids inline handlers).
  document.querySelectorAll('select[data-autosubmit]').forEach(function (sel) {
    sel.addEventListener('change', function () { sel.form.submit(); });
  });
})();

// -- M10: install TeamOS on a phone (staff pages only; customer pages never register the app) ------------------
(function () {
  if (!('serviceWorker' in navigator) || document.body.classList.contains('public') || !window.isSecureContext) return;
  window.addEventListener('load', function () { navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {}); });
  var deferred = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault(); deferred = e;
    document.querySelectorAll('[data-install-hint]').forEach(function (el) { el.hidden = false; });
  });
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-install]');
    if (!b || !deferred) return;
    deferred.prompt(); deferred = null;
  });
})();

