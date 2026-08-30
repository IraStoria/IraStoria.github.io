/* © 2026 IraStoria (https://irastoria.github.io/). All rights reserved. See /LICENSE. */
/* IraStoria site script — zero dependencies */
(function () {
  'use strict';
  // 1. Remember language preference (feat.i18n)
  var lang = document.body.getAttribute('data-lang');
  try { if (lang) localStorage.setItem('lang', lang); } catch (e) {}
  var sw = document.querySelector('[data-lang-switch]');
  if (sw) sw.addEventListener('click', function () {
    try { localStorage.setItem('lang', sw.getAttribute('data-lang-switch')); } catch (e) {}
  });

  // 2. Anti-scrape email: assemble on click (V2)
  document.querySelectorAll('a[data-email]').forEach(function (a) {
    a.addEventListener('click', function (ev) {
      var u = a.getAttribute('data-u'), d = a.getAttribute('data-d');
      if (!u || !d) return;
      var addr = u + '@' + d;
      if (a.getAttribute('href') === '#') {
        ev.preventDefault();
        a.setAttribute('href', 'mailto:' + addr);
        a.textContent = addr;
      }
    });
  });

  // 3. Works filter (feat.works)
  var bar = document.querySelector('[data-filter-bar]');
  var grid = document.querySelector('[data-work-grid]');
  if (bar && grid) {
    bar.addEventListener('click', function (ev) {
      var btn = ev.target.closest('button[data-filter]');
      if (!btn) return;
      var f = btn.getAttribute('data-filter');
      bar.querySelectorAll('button').forEach(function (b) { b.classList.toggle('active', b === btn); });
      grid.querySelectorAll('.card').forEach(function (c) {
        c.hidden = !(f === 'all' || c.getAttribute('data-type') === f);
      });
    });
  }
})();
