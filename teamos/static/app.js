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
  function hiddenSource(name) {
    var src = document.querySelector('[name="' + name + '"]'), box = src && src.closest('[data-show-if], [data-also-if]');
    return !!(box && box.classList.contains('hide'));
  }
  // one rule "a=x|y;b=z": true when ANY part holds (null when none of its questions is on this page)
  function rule(text) {
    var known = false, ok = false;
    text.split(';').forEach(function (cond) {
      var parts = cond.split('='), v = valueOf(parts[0]);
      if (v === null) return;
      known = true;
      if (!hiddenSource(parts[0]) && parts[1].split('|').indexOf(v || '') !== -1) ok = true;
    });
    return known ? ok : null;
  }
  function refresh() {
    for (var pass = 0; pass < 3; pass++) {      // a few passes so "depends on a hidden question" settles
      document.querySelectorAll('[data-show-if], [data-also-if]').forEach(function (el) {
        var a = el.dataset.showIf ? rule(el.dataset.showIf) : true, b = el.dataset.alsoIf ? rule(el.dataset.alsoIf) : true;
        if (a === null && b === null) return;
        el.classList.toggle('hide', a === false || b === false);
      });
    }
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
    if (!m) return v ? 'Type the amount in rupees, numbers only (e.g. 45000)' : '';
    var mult = {l: 1e5, lac: 1e5, lakh: 1e5, lakhs: 1e5, cr: 1e7, crore: 1e7, crores: 1e7, k: 1e3, thousand: 1e3, thousands: 1e3}[m[2]] || 1;
    var n = Math.round(parseFloat(m[1]) * mult);
    var T = formText();
    var words = n >= 1e7 ? (n / 1e7).toFixed(2).replace(/\.?0+$/, '') + ' ' + (T.crore || 'crore') : n >= 1e5 ? (n / 1e5).toFixed(2).replace(/\.?0+$/, '') + ' ' + (T.lakh || 'lakh') : '';
    return '= ₹' + n.toLocaleString('en-IN') + (words ? ' (' + words + ')' : '');
  }
  document.querySelectorAll('input[data-inr]').forEach(function (i) {
    var box = i.closest('.qf, .field, .intake-row') || i.parentNode;
    var out = box.querySelector('.inr-words');
    if (!out) {                                   // staff forms: add the "= Rs 25 lakh" line under the amount
      out = document.createElement('div'); out.className = 'inr-words help'; out.setAttribute('aria-live', 'polite');
      (i.closest('.inr') || i).insertAdjacentElement('afterend', out);
    }
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

  // Forms: live "n of m answered" bar, save as you go (each step), and "Use my location"
  var pform = document.querySelector('form[data-progress]');
  if (pform) {
    var qs = Array.prototype.slice.call(pform.querySelectorAll('[data-q]'));
    var ptxt = pform.querySelector('.answered-text'), pbar = pform.querySelector('.answered-bar');
    var answered = function (f) {
      return Array.prototype.some.call(f.querySelectorAll('input, textarea, select'), function (i) {
        if (i.type === 'radio' || i.type === 'checkbox') return i.checked;
        return i.type !== 'hidden' && i.type !== 'button' && String(i.value || '').trim() !== '';
      });
    };
    var count = function () {
      var n = qs.filter(answered).length;
      if (ptxt) ptxt.textContent = n + ' of ' + qs.length + ' answered';
      if (pbar) { pbar.max = qs.length || 1; pbar.value = n; }
    };
    pform.addEventListener('input', count); pform.addEventListener('change', count); count();
    var saveUrl = pform.getAttribute('data-autosave'), note = pform.querySelector('.saved-note');
    var save = function (beacon) {
      if (!saveUrl) return;
      var fd = new FormData(pform);
      if (beacon && navigator.sendBeacon) { navigator.sendBeacon(saveUrl, fd); return; }
      fetch(saveUrl, {method: 'POST', body: fd, credentials: 'same-origin'}).then(function (r) { return r.json(); })
        .then(function (j) {
          if (j && j.ok && j.draft) { pform.querySelector('input[name=draft]').value = j.draft; if (note) note.textContent = 'Saved'; }
        }).catch(function () {});
    };
    pform.querySelectorAll('[data-step]').forEach(function (b) { b.addEventListener('click', function () { save(false); }); });
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') save(true); });
    pform.querySelectorAll('[data-geo-for]').forEach(function (b) {
      b.addEventListener('click', function () {
        var inp = document.getElementById(b.getAttribute('data-geo-for')), msg = b.parentNode.querySelector('.geo-msg');
        var fail = function () { if (msg) msg.textContent = 'Could not get your location — please type the area.'; };
        if (!navigator.geolocation) { fail(); return; }
        if (msg) msg.textContent = 'Finding your area…';
        navigator.geolocation.getCurrentPosition(function (pos) {
          fetch('/f/geo/near?lat=' + pos.coords.latitude + '&lng=' + pos.coords.longitude).then(function (r) { return r.json(); })
            .then(function (j) {
              if (j && j.ok && j.label) { inp.value = j.label; if (msg) msg.textContent = 'Filled from your location — change it if needed.'; count(); }
              else { fail(); }
            }).catch(fail);
        }, fail, {timeout: 10000, maximumAge: 600000});
      });
    });
  }

  // Lists: tick records, choose an action (only that action's inputs show), select-all
  var bulk = document.getElementById('bulk');
  if (bulk) {
    var act = bulk.querySelector('[data-bulk-action]');
    var cnt = bulk.querySelector('.bulk-count');
    var boxes = function () { return Array.prototype.slice.call(document.querySelectorAll('input[name=ids][form=bulk]')); };
    var refresh = function () {
      var n = boxes().filter(function (b) { return b.checked; }).length;
      cnt.textContent = n ? (n + (cnt.getAttribute('data-some') || ' ticked — choose what to do:'))
                          : (cnt.getAttribute('data-empty') || 'Tick leads to change owner, add a follow-up or remove them.');
      bulk.classList.toggle('has-selection', n > 0);
    };
    if (act) act.addEventListener('change', function () {   // the review page's bar has a single action, no picker
      bulk.querySelectorAll('.bulk-opt').forEach(function (o) { o.hidden = o.getAttribute('data-for') !== act.value; });
    });
    document.addEventListener('change', function (e) { if (e.target.name === 'ids') refresh(); });
    var all = document.querySelector('[data-select-all]');
    if (all) all.addEventListener('change', function () { boxes().forEach(function (b) { b.checked = all.checked; }); refresh(); });
    bulk.addEventListener('submit', function (e) {
      if (act && act.value === 'delete' && !window.confirm('Delete the ticked records for good? This cannot be undone.')) e.preventDefault();
    });
    refresh();
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
  // Clickable rows and task cards: a click anywhere opens the record (its first link), except on buttons, links,
  // form fields or selected text. Ctrl/Cmd-click opens a new tab, like a normal link.
  document.querySelectorAll('tr.row-link, li.task, .click-card').forEach(function (row) {
    var link = row.getAttribute('data-href') ? null : row.querySelector('a[href]:not([href^="tel:"]):not([href="#"])');
    var href = row.getAttribute('data-href') || (link && link.getAttribute('href'));
    if (!href) return;
    row.classList.add('is-clickable');
    row.addEventListener('click', function (e) {
      if (e.target.closest('a, button, input, select, textarea, label, summary, form, details')) return;
      if (window.getSelection && String(window.getSelection())) return;
      if (e.ctrlKey || e.metaKey) { window.open(href, '_blank'); } else { window.location.href = href; }
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


// Investor screening: live estimate while the team clicks the 5 answers (the saved verdict also checks red flags).
(function () {
  var f = document.querySelector('form[data-screen]');
  if (!f) return;
  var out = f.querySelector('[data-screen-live]');
  var upd = function () {
    var picked = f.querySelectorAll('input[data-pts]:checked');
    var total = 0;
    picked.forEach(function (i) { total += parseInt(i.getAttribute('data-pts'), 10) || 0; });
    if (!out) return;
    if (!picked.length) { out.hidden = true; return; }
    out.hidden = false;
    var v = total >= 65 ? ['Pursue', 'ok'] : total >= 40 ? ['Nurture', 'warn'] : ['Not now', 'grey'];
    out.className = 'live-verdict tone-' + v[1];
    out.textContent = (picked.length < 5 ? 'So far: ' : 'Estimate: ') + v[0] + ' · ' + total + '/100' +
      (picked.length < 5 ? ' (' + picked.length + ' of 5 answered)' : '');
  };
  f.addEventListener('change', upd);
  upd();
})();

// -- Phone / computer alerts (Web Push). The settings card has [data-push]; My Day shows a nudge until it's on. ---
(function () {
  var card = document.querySelector('[data-push]'), nudge = document.querySelector('[data-push-nudge]');
  if (!card && !nudge) return;
  var supported = window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  var ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  var standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  function token() { var m = document.querySelector('[name=csrf_token]'); return m ? m.value : ''; }
  function post(url, body) {
    return fetch(url, {method: 'POST', credentials: 'same-origin', body: JSON.stringify(body),
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token()}});
  }
  function keyBytes(b64) {
    var s = atob((b64 + '==='.slice((b64.length + 3) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i); return out;
  }
  function device() {
    var u = navigator.userAgent, os = /android/i.test(u) ? 'Android' : ios ? 'iPhone/iPad' : /windows/i.test(u) ? 'Windows' : /mac/i.test(u) ? 'Mac' : 'Computer';
    var br = /edg\//i.test(u) ? 'Edge' : /firefox/i.test(u) ? 'Firefox' : /chrome|crios/i.test(u) ? 'Chrome' : /safari/i.test(u) ? 'Safari' : 'browser';
    return os + ' · ' + (standalone ? 'TeamOS app' : br);
  }
  function show(sel, on) { var el = card && card.querySelector(sel); if (el) el.hidden = !on; }
  function say(t) { var el = card && card.querySelector('[data-push-state]'); if (el) el.textContent = t; }
  function current() {
    return navigator.serviceWorker.register('/sw.js', {scope: '/'}).then(function () { return navigator.serviceWorker.ready; })
      .then(function (reg) { return reg.pushManager.getSubscription().then(function (s) { return {reg: reg, sub: s}; }); });
  }
  function render() {
    if (!supported) {
      if (card) { say(ios && !standalone ? 'Not on yet.' : 'This browser can’t receive alerts.'); show(ios && !standalone ? '[data-push-ios]' : '[data-push-unsupported]', true); }
      return;
    }
    if (Notification.permission === 'denied') { say('Blocked in this browser.'); show('[data-push-blocked]', true); return; }
    current().then(function (st) {
      if (st.sub) {
        say('✓ Alerts are on for this device.'); show('[data-push-on]', false); show('[data-push-off]', true);
        post('/me/push/subscribe', {subscription: st.sub.toJSON(), device: device()});   // keeps the server copy fresh
      } else {
        say('Alerts are off on this device.'); show('[data-push-on]', true); show('[data-push-off]', false);
        if (nudge) nudge.hidden = false;
      }
    }).catch(function () { say('Could not check alerts on this device.'); });
  }
  if (card) {
    card.addEventListener('click', function (e) {
      if (e.target.closest('[data-push-on]')) {
        say('Asking your browser…');
        Notification.requestPermission().then(function (perm) {
          if (perm !== 'granted') { render(); return null; }
          return fetch('/me/push/key', {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (k) {
            return current().then(function (st) {
              return st.reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: keyBytes(k.key)});
            });
          }).then(function (sub) { return post('/me/push/subscribe', {subscription: sub.toJSON(), device: device()}); })
            .then(function () { window.location.reload(); });
        }).catch(function () { say('Could not turn alerts on. Please try again.'); });
      } else if (e.target.closest('[data-push-off]')) {
        current().then(function (st) {
          if (!st.sub) return null;
          var ep = st.sub.endpoint;
          return st.sub.unsubscribe().then(function () { return post('/me/push/unsubscribe', {endpoint: ep}); });
        }).then(function () { window.location.reload(); });
      }
    });
  }
  render();
})();

// -- Copy buttons: next to every link or ready-made message shown for sharing (form links, report links, teaser and
//    progress links, calendar feed, webhook address). One tap copies it; the button says "Copied". -----------------
(function () {
  var sel = '.link-box, code.copy, textarea.msg-preview, [data-copy]';
  function text(el) { return (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') ? el.value : el.textContent.trim(); }
  function copy(value) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(value).catch(function () { return classic(value); });
    }
    return classic(value);
  }
  function classic(value) {
    return new Promise(function (ok, fail) {              // older phones / blocked clipboard: the classic way
      var t = document.createElement('textarea');
      t.value = value; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0';
      document.body.appendChild(t); t.select();
      try { document.execCommand('copy') ? ok() : fail(); } catch (e) { fail(e); }
      document.body.removeChild(t);
    });
  }
  document.querySelectorAll(sel).forEach(function (el) {
    if (el.closest('.public') || el.dataset.copyReady) return;
    el.dataset.copyReady = '1';
    var isMsg = el.tagName === 'TEXTAREA' || /\s/.test(text(el).trim());
    var label = el.getAttribute('data-copy-label') || (isMsg ? 'Copy message' : 'Copy link');
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'btn sm copy-btn'; b.textContent = '📋 ' + label;
    b.addEventListener('click', function () {
      copy(text(el)).then(function () {
        b.textContent = '✓ Copied'; b.classList.add('done');
        setTimeout(function () { b.textContent = '📋 ' + label; b.classList.remove('done'); }, 2000);
      }).catch(function () {
        var r = document.createRange(); r.selectNodeContents(el);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        b.textContent = 'Selected — press Ctrl+C';
      });
    });
    var wrap = document.createElement('span'); wrap.className = 'copy-wrap';
    el.parentNode.insertBefore(wrap, el.nextSibling); wrap.appendChild(b);
  });
})();


// -- Number fields take numbers only (typed, pasted or autofilled). data-num: int | dec | tel; phone-like fields too --
(function () {
  var PHONE = /^(phone|alt_phone|wa_number|contact_number|whatsapp|mobile)$/;
  function kindOf(el) {
    if (el.dataset.num) return el.dataset.num;
    if (el.type === 'tel' || el.getAttribute('inputmode') === 'tel' || PHONE.test(el.name || '')) return 'tel';
    if (el.getAttribute('inputmode') === 'numeric') return 'int';
    if (el.getAttribute('inputmode') === 'decimal') return 'dec';
    return null;
  }
  function clean(v, k) {
    if (k === 'int') return v.replace(/[^0-9]/g, '');
    if (k === 'dec') {
      v = v.replace(/[^0-9.]/g, '');
      var i = v.indexOf('.');
      return i < 0 ? v : v.slice(0, i + 1) + v.slice(i + 1).replace(/\./g, '');
    }
    if (k === 'tel') return v.replace(/(?!^\+)[^0-9]/g, '');
    return v;
  }
  function fix(el) {
    var k = kindOf(el); if (!k) return;
    var v = clean(el.value, k);
    if (v === el.value) return;
    el.value = v;
    el.dispatchEvent(new Event('input', {bubbles: true}));   // helpers (e.g. the rupee line) see the cleaned value
    var after = el.closest('.inr') || el;
    var hint = after.nextElementSibling && after.nextElementSibling.classList.contains('num-hint') ? after.nextElementSibling : null;
    if (!hint) { hint = document.createElement('div'); hint.className = 'num-hint small'; after.insertAdjacentElement('afterend', hint); }
    hint.textContent = k === 'tel' ? 'Numbers only (10-digit mobile).' : 'Numbers only, no letters.';
    clearTimeout(el._numT); el._numT = setTimeout(function () { hint.textContent = ''; }, 2500);
  }
  document.addEventListener('input', function (e) { if (e.target.tagName === 'INPUT') fix(e.target); });
  document.addEventListener('change', function (e) { if (e.target.tagName === 'INPUT') fix(e.target); });
  document.querySelectorAll('input').forEach(function (el) {
    var k = kindOf(el);
    if (k && !el.getAttribute('inputmode')) el.setAttribute('inputmode', k === 'tel' ? 'tel' : k === 'dec' ? 'decimal' : 'numeric');
  });
})();

// -- Nothing typed is ever lost: every form keeps a draft on this device until it is saved -------------------------
// If saving fails (a mistake to fix, the session ended...) the page comes back with everything put back in. If someone
// leaves without saving, the page offers to restore it next time. Passwords, files and hidden values are never kept.
(function () {
  var PREFIX = 'teamos-draft:', DAY = 864e5, KEEP_DAYS = 7, store;
  try { store = window.localStorage; store.setItem(PREFIX + 't', '1'); store.removeItem(PREFIX + 't'); } catch (e) { return; }
  function allKeys() { var out = []; for (var i = 0; i < store.length; i++) out.push(store.key(i)); return out; }
  document.querySelectorAll('form[action="/logout"]').forEach(function (f) {
    f.addEventListener('submit', function () {        // signing out: drafts leave the device too
      allKeys().forEach(function (k) { if (k && k.indexOf(PREFIX) === 0) store.removeItem(k); });
    });
  });
  allKeys().forEach(function (k) {                    // old drafts expire
    if (!k || k.indexOf(PREFIX) !== 0) return;
    try { if (Date.now() - JSON.parse(store.getItem(k)).at > KEEP_DAYS * DAY) store.removeItem(k); } catch (e) { store.removeItem(k); }
  });
  var forms = Array.prototype.filter.call(document.querySelectorAll('form'), function (f) {
    var m = (f.getAttribute('method') || 'get').toLowerCase();
    var act = f.getAttribute('action') || '';
    return m === 'post' && !f.hasAttribute('data-no-draft') && !/\/(login|logout|mfa|me\/password)/.test(act + ' ' + location.pathname)
      && f.querySelector('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]), textarea, select');
  });
  var seen = {};
  function keyOf(f) {
    var base = location.pathname + '|' + (f.getAttribute('action') || location.pathname);
    seen[base] = (seen[base] || 0) + 1;
    return PREFIX + base + '|' + seen[base];
  }
  function fields(f) {
    return Array.prototype.filter.call(f.elements, function (el) {
      return el.name && !/^(csrf_token|form_token|draft|website|next)$/.test(el.name) &&
        !/^(hidden|password|file|submit|button|reset)$/.test(el.type);
    });
  }
  function read(f) {
    var out = {}, any = false;
    fields(f).forEach(function (el) {
      if (el.type === 'radio') { if (el.checked) { out[el.name] = el.value; if (!el.defaultChecked) any = true; } return; }
      if (el.type === 'checkbox') {
        out[el.name] = out[el.name] || [];
        if (el.checked) out[el.name].push(el.value);
        if (el.checked !== el.defaultChecked) any = true;
        return;
      }
      if (el.tagName === 'SELECT') {
        out[el.name] = el.multiple ? Array.prototype.filter.call(el.options, function (o) { return o.selected; }).map(function (o) { return o.value; }) : el.value;
        if (Array.prototype.some.call(el.options, function (o) { return o.selected !== o.defaultSelected; })) any = true;
        return;
      }
      if (out[el.name] === undefined) out[el.name] = el.value;
      if (el.value !== el.defaultValue) any = true;
    });
    return any ? out : null;
  }
  function write(f, vals) {
    fields(f).forEach(function (el) {
      if (!(el.name in vals)) return;
      var v = vals[el.name];
      if (el.type === 'radio') el.checked = (el.value === v);
      else if (el.type === 'checkbox') el.checked = Array.isArray(v) && v.indexOf(el.value) >= 0;
      else if (el.tagName === 'SELECT' && el.multiple) Array.prototype.forEach.call(el.options, function (o) { o.selected = Array.isArray(v) && v.indexOf(o.value) >= 0; });
      else { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }
    });
    f.dispatchEvent(new Event('change', {bubbles: true}));
  }
  var failed = !!document.querySelector('.flash.error, .has-err, .page-error');
  var okFlash = !!document.querySelector('.flash.ok');
  // the form sent last: saved for sure when the app says so or moved on to another page; otherwise kept
  var pending = null;
  try { pending = JSON.parse(window.sessionStorage.getItem('teamos-pending') || 'null'); window.sessionStorage.removeItem('teamos-pending'); } catch (e) { pending = null; }
  if (pending && !failed && (okFlash || pending.path !== location.pathname)) store.removeItem(pending.key);
  forms.forEach(function (f) {
    var key = keyOf(f), saved = null;
    try { saved = JSON.parse(store.getItem(key) || 'null'); } catch (e) { saved = null; }
    if (saved && saved.vals) {
      if (saved.submitted && failed) {
        write(f, saved.vals);                       // saving failed: everything typed is put back
        var n = document.createElement('div'); n.className = 'draft-note flash warn';
        n.textContent = 'Your entries were kept. Fix the item mentioned above and save again.';
        f.insertBefore(n, f.firstChild);
      } else if (!(saved.submitted && pending && pending.key === key && !failed)) {
        var bar = document.createElement('div'); bar.className = 'draft-bar';
        var when = new Date(saved.at).toLocaleString([], {day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit'});
        var txt = document.createElement('span'); txt.textContent = 'You have unsaved entries here from ' + when + '.';
        var r = document.createElement('button'); r.type = 'button'; r.className = 'btn sm primary'; r.textContent = 'Restore';
        var d = document.createElement('button'); d.type = 'button'; d.className = 'btn sm link'; d.textContent = 'Discard';
        r.addEventListener('click', function () { write(f, saved.vals); bar.remove(); });
        d.addEventListener('click', function () { store.removeItem(key); bar.remove(); });
        bar.appendChild(txt); bar.appendChild(r); bar.appendChild(d);
        f.insertBefore(bar, f.firstChild);
      }
    }
    var t;
    var keep = function (submitted) {
      var vals = read(f);
      if (vals) store.setItem(key, JSON.stringify({at: Date.now(), vals: vals, submitted: !!submitted}));
      else if (!submitted) store.removeItem(key);
    };
    f.addEventListener('input', function () { clearTimeout(t); t = setTimeout(function () { keep(false); }, 400); });
    f.addEventListener('change', function (e) { if (e.isTrusted) keep(false); });
    f.addEventListener('submit', function () {
      clearTimeout(t); keep(true);
      try { window.sessionStorage.setItem('teamos-pending', JSON.stringify({key: key, path: location.pathname})); } catch (e) {}
    });
  });
})();

// A box that starts with a suggested value (City: Hyderabad): clicking in selects it, so typing replaces it instead
// of adding to it.
document.addEventListener("focusin", function (e) {
  var el = e.target;
  if (el && el.hasAttribute && el.hasAttribute("data-select-on-focus") && el.value === el.defaultValue) {
    setTimeout(function () { try { el.select(); } catch (x) {} }, 0);
  }
});

// Light / dark: dark is the default; this device can switch (kept for a year in a cookie the server reads)
document.addEventListener("click", function (e) {
  var b = e.target.closest && e.target.closest("[data-theme-toggle]");
  if (!b) return;
  var next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  document.cookie = "teamos_theme=" + next + "; path=/; max-age=31536000; samesite=lax";
  b.textContent = next === "dark" ? "\u2600\ufe0f Light mode" : "\ud83c\udf19 Dark mode";
  var m = document.querySelector('meta[name="theme-color"]');
  if (m) m.setAttribute("content", next === "dark" ? "#3b1a0e" : "#c04000");
});

// "Date & time" becomes required as soon as the next step it belongs to is written
(function () {
  function sync(el) {
    var f = el.form, other = f && f.elements[el.getAttribute("data-required-with")];
    var on = !!(other && other.value && other.value.trim());
    el.required = on;
    var lab = el.closest(".field") && el.closest(".field").querySelector("label");
    if (lab) lab.classList.toggle("is-req", on);
  }
  function all() { document.querySelectorAll("[data-required-with]").forEach(sync); }
  document.addEventListener("input", function (e) {
    if (e.target && e.target.form) e.target.form.querySelectorAll("[data-required-with]").forEach(sync);
  });
  document.addEventListener("change", all);
  all();
})();

// ---- Voice typing (speech to text) --------------------------------------------------------------------------------
// A 🎤 button on the boxes where the team writes sentences (call notes, notes, remarks, tasks, WhatsApp replies).
// Uses the browser's own speech recognition (Chrome / Edge: free, no key). Language: English (India), Hindi or
// Telugu - tap the small EN / हि / తె label to switch; remembered on this device. Words are added where the
// cursor is; the box still saves as usual (and the draft keeper sees every change).
(function () {
  if (document.body.classList.contains("public")) return;           // staff pages only
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var LANGS = [["en-IN", "EN", "English"], ["hi-IN", "हि", "Hindi"], ["te-IN", "తె", "Telugu"]];
  var SKIP = /^(path_text|links|split|localities_preferred|localities_excluded|t|csv|raw)$/;
  var lang = (function () { try { return localStorage.getItem("teamos-voice-lang") || "en-IN"; } catch (e) { return "en-IN"; } })();
  var active = null;   // {rec, box, btn}

  function langInfo(code) { for (var i = 0; i < LANGS.length; i++) if (LANGS[i][0] === code) return LANGS[i]; return LANGS[0]; }
  function say(wrap, msg, bad) {
    var n = wrap.querySelector(".voice-msg");
    if (!n) { n = document.createElement("div"); n.className = "voice-msg"; n.setAttribute("aria-live", "polite"); wrap.appendChild(n); }
    n.textContent = msg; n.classList.toggle("bad", !!bad); n.hidden = !msg;
  }
  function insert(box, text) {
    text = text.trim();
    if (!text) return;
    var v = box.value, s = box.selectionStart == null ? v.length : box.selectionStart, e = box.selectionEnd == null ? v.length : box.selectionEnd;
    var before = v.slice(0, s), after = v.slice(e);
    if (before && !/\s$/.test(before)) text = " " + text;
    if (before.trim() === "" || /[.!?]\s*$/.test(before)) text = text.replace(/^(\s*)(\S)/, function (m, sp, c) { return sp + c.toUpperCase(); });
    var max = box.maxLength > 0 ? box.maxLength : 100000;
    var next = (before + text + after).slice(0, max);
    box.value = next;
    var pos = Math.min((before + text).length, next.length);
    try { box.setSelectionRange(pos, pos); } catch (x) {}
    box.dispatchEvent(new Event("input", {bubbles: true}));
  }
  function stop() {
    if (!active) return;
    try { active.rec.stop(); } catch (e) {}
  }
  function start(box, btn, wrap) {
    if (!SR) {
      say(wrap, "Voice typing needs Chrome or Edge. On a phone you can also tap the 🎤 on the keyboard.", true);
      return;
    }
    if (active) { var same = active.box === box; stop(); if (same) return; }
    var rec = new SR();
    rec.lang = lang; rec.continuous = true; rec.interimResults = true; rec.maxAlternatives = 1;
    active = {rec: rec, box: box, btn: btn};
    btn.classList.add("on"); btn.setAttribute("aria-pressed", "true"); btn.title = "Listening… tap to stop";
    say(wrap, "Listening (" + langInfo(lang)[2] + ")… speak, then tap 🎤 to stop.");
    box.focus();
    rec.onresult = function (ev) {
      var interim = "";
      for (var i = ev.resultIndex; i < ev.results.length; i++) {
        var r = ev.results[i];
        if (r.isFinal) insert(box, r[0].transcript); else interim += r[0].transcript;
      }
      say(wrap, interim ? "… " + interim : "Listening (" + langInfo(lang)[2] + ")…");
    };
    rec.onerror = function (ev) {
      var m = {"not-allowed": "Allow the microphone for this site (tap the lock icon next to the address → Microphone → Allow), then try again.",
               "service-not-allowed": "Allow the microphone for this site, then try again.",
               "no-speech": "Didn't hear anything — tap 🎤 and speak again.",
               "audio-capture": "No microphone found on this device.",
               "network": "Voice typing needs internet — check the connection and try again."}[ev.error];
      say(wrap, m || "Voice typing stopped — tap 🎤 to try again.", true);
    };
    rec.onend = function () {
      btn.classList.remove("on"); btn.setAttribute("aria-pressed", "false"); btn.title = "Type by voice";
      var msg = wrap.querySelector(".voice-msg");
      if (msg && !msg.classList.contains("bad")) say(wrap, "");
      if (active && active.rec === rec) active = null;
    };
    try { rec.start(); } catch (e) { say(wrap, "Voice typing could not start — tap 🎤 again.", true); active = null; btn.classList.remove("on"); }
  }
  function attach(box) {
    if (box.dataset.voice === "off" || box.dataset.voiceReady || SKIP.test(box.name || "") || box.readOnly || box.disabled) return;
    if (box.type === "hidden") return;
    box.dataset.voiceReady = "1";
    var wrap = document.createElement("span");
    wrap.className = "voice-wrap" + (box.tagName === "TEXTAREA" ? " is-area" : " is-line");
    box.parentNode.insertBefore(wrap, box);
    wrap.appendChild(box);
    var bar = document.createElement("span");
    bar.className = "voice-bar";
    var lb = document.createElement("button");
    lb.type = "button"; lb.className = "voice-lang"; lb.textContent = langInfo(lang)[1];
    lb.title = "Voice language: " + langInfo(lang)[2] + " — tap to change";
    var mic = document.createElement("button");
    mic.type = "button"; mic.className = "voice-mic"; mic.title = "Type by voice"; mic.setAttribute("aria-label", "Type by voice");
    mic.setAttribute("aria-pressed", "false"); mic.textContent = "🎤";
    bar.appendChild(lb); bar.appendChild(mic); wrap.appendChild(bar);
    mic.addEventListener("click", function (e) { e.preventDefault(); start(box, mic, wrap); });
    lb.addEventListener("click", function (e) {
      e.preventDefault();
      var i = 0; for (; i < LANGS.length; i++) if (LANGS[i][0] === lang) break;
      lang = LANGS[(i + 1) % LANGS.length][0];
      try { localStorage.setItem("teamos-voice-lang", lang); } catch (x) {}
      document.querySelectorAll(".voice-lang").forEach(function (b) { b.textContent = langInfo(lang)[1]; b.title = "Voice language: " + langInfo(lang)[2] + " — tap to change"; });
      say(wrap, "Voice language: " + langInfo(lang)[2]);
      if (active) stop();
    });
  }
  function scan(root) {
    (root || document).querySelectorAll(".main textarea, .main input[data-voice], .pass-f input[name=note]").forEach(attach);
  }
  scan();
  document.addEventListener("toggle", function (e) { if (e.target && e.target.open) scan(e.target); }, true);
  document.addEventListener("submit", stop, true);
  window.addEventListener("pagehide", stop);
})();
