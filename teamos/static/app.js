// Progressive enhancement only — every action works without JavaScript.
(function () {
  // Mobile menu
  var t = document.querySelector('.menu-toggle');
  if (t) t.addEventListener('click', function () { document.querySelector('.side').classList.toggle('open'); });

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

  // Conditional fields: <div data-show-if="business_model=franchise">
  function refresh() {
    document.querySelectorAll('[data-show-if]').forEach(function (el) {
      var parts = el.dataset.showIf.split('='), src = document.querySelector('[name="' + parts[0] + '"]');
      if (!src) return;
      el.classList.toggle('hide', src.value !== parts[1]);
    });
  }
  document.querySelectorAll('select').forEach(function (s) { s.addEventListener('change', refresh); });
  refresh();

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
})();
