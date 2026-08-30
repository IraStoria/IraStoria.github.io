/* © 2026 IraStoria (https://irastoria.github.io/). All rights reserved. See /LICENSE. */
/* Music Transition Concept Demo — written from scratch, Web Audio only, no external assets.
   Concept level only (ADR-004): shows *when* a section switch is allowed to happen, nothing else. */
(function () {
  'use strict';

  // ------------------------------------------------------------ i18n
  var I18N = {
    zh: {
      title: '音樂 Transition 概念展示',
      lead: '互動音樂常要在「段落 A」與「段落 B」之間切換。按下切換後，音樂什麼時候真的換過去，決定了聽起來順不順。試著在不同時機按切換，比較四種策略。',
      start: '▶ 開始', stop: '■ 停止', now: '目前段落：', bpm: 'BPM',
      mode: '轉場策略', m_immediate: '立即切換', m_beat: '等下一拍', m_bar: '等下一小節', m_crossfade: '淡入淡出（1 小節）',
      switch: '切換到 {X}', pending_beat: '已排程：下一拍切換', pending_bar: '已排程：下一小節切換', pending_xf: '淡入淡出中…',
      how: '四種策略在做什麼',
      e_immediate: '收到指令當下就切。最即時，但幾乎一定落在拍子中間，聽起來像被打斷。',
      e_beat: '把切換延後到下一個拍點。延遲最多一拍（120 BPM 約半秒），節奏感不斷。',
      e_bar: '延後到下一小節的第一拍。延遲最多一小節，但樂句完整，最「音樂」的切法。',
      e_crossfade: '兩段同時播放，用一小節的時間讓 A 淡出、B 淡入。適合氣氛型音樂；節奏強的音樂反而會糊。',
      note: '本展示為純概念示意：段落是瀏覽器即時合成的簡單音型，不代表任何實際作品或遊戲的實作。桌面瀏覽器體驗較佳。'
    },
    en: {
      title: 'Music Transition Concept Demo',
      lead: 'Interactive music often has to move between "section A" and "section B". Once you ask for a switch, WHEN the music actually changes decides whether it sounds smooth. Try switching at different moments and compare the four strategies.',
      start: '▶ Start', stop: '■ Stop', now: 'Now playing:', bpm: 'BPM',
      mode: 'Transition strategy', m_immediate: 'Immediate', m_beat: 'Next beat', m_bar: 'Next bar', m_crossfade: 'Crossfade (1 bar)',
      switch: 'Switch to {X}', pending_beat: 'Scheduled: on next beat', pending_bar: 'Scheduled: on next bar', pending_xf: 'Crossfading…',
      how: 'What each strategy does',
      e_immediate: 'Switches the instant the command arrives. Most responsive, but almost always lands mid-beat and sounds like an interruption.',
      e_beat: 'Defers the switch to the next beat. At most one beat of latency (~0.5 s at 120 BPM); the groove survives.',
      e_bar: 'Defers to the downbeat of the next bar. Up to one bar of latency, but phrases stay intact — the most "musical" cut.',
      e_crossfade: 'Both sections play while A fades out and B fades in over one bar. Great for ambient material; rhythmic music tends to smear.',
      note: 'This is a pure concept sketch: the sections are simple patterns synthesized live in the browser and do not represent any real work or game implementation. Best experienced on a desktop browser.'
    }
  };
  var lang = 'zh';
  try { lang = localStorage.getItem('lang') || (navigator.language.toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en'); } catch (e) {}
  if (!I18N[lang]) lang = 'zh';

  function applyLang() {
    document.documentElement.lang = lang === 'zh' ? 'zh-Hant' : 'en';
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var k = el.getAttribute('data-i18n');
      if (k === 'start') el.textContent = running ? I18N[lang].stop : I18N[lang].start;
      else if (k === 'switch') el.textContent = I18N[lang].switch.replace('{X}', current === 'A' ? 'B' : 'A');
      else el.textContent = I18N[lang][k];
    });
    document.querySelectorAll('[data-lang]').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-lang') === lang); });
    try { localStorage.setItem('lang', lang); } catch (e) {}
  }
  document.querySelectorAll('[data-lang]').forEach(function (b) {
    b.addEventListener('click', function () { lang = b.getAttribute('data-lang'); applyLang(); });
  });

  // ------------------------------------------------------------ music model
  var BEATS_PER_BAR = 4;
  var LOOKAHEAD = 0.1;       // seconds of audio scheduled ahead
  var TICK_MS = 25;
  // Two contrasting 1-bar patterns, 16th-note grid. [freq or 0 for rest] × 16
  var SECTIONS = {
    A: { color: '#5fb3c9', bass: [110, 0, 110, 0, 146.8, 0, 110, 0, 110, 0, 110, 0, 164.8, 0, 146.8, 0],
         lead: [440, 0, 0, 523.3, 0, 0, 659.3, 0, 587.3, 0, 0, 523.3, 0, 0, 440, 0], hat: [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0] },
    B: { color: '#e0b04a', bass: [82.4, 82.4, 0, 82.4, 0, 98, 0, 82.4, 82.4, 82.4, 0, 82.4, 0, 123.5, 0, 98],
         lead: [329.6, 0, 392, 0, 493.9, 0, 392, 0, 329.6, 0, 392, 0, 493.9, 0, 587.3, 0], hat: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1] }
  };

  var ctx = null, master = null, gains = {};
  var running = false, current = 'A', pending = null; // pending = {mode, at, to}
  var bpm = 120, step = 0, nextStepTime = 0, timer = null;
  var history = []; // for the timeline: [{t, section}] audio-time events
  var switchEvents = []; // markers: {t, kind:'request'|'switch'}

  function stepDur() { return 60 / bpm / 4; }

  function ensureAudio() {
    if (ctx) return;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain(); master.gain.value = 0.5; master.connect(ctx.destination);
    ['A', 'B'].forEach(function (s) {
      gains[s] = ctx.createGain(); gains[s].gain.value = s === current ? 1 : 0; gains[s].connect(master);
    });
  }

  function tone(dest, freq, t, dur, type, vol) {
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + 0.005); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(dest); o.start(t); o.stop(t + dur + 0.02);
  }
  function hat(dest, t, vol) {
    var len = Math.floor(ctx.sampleRate * 0.03), buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    var src = ctx.createBufferSource(), g = ctx.createGain(), f = ctx.createBiquadFilter();
    f.type = 'highpass'; f.frequency.value = 6000; src.buffer = buf; g.gain.value = vol;
    src.connect(f); f.connect(g); g.connect(dest); src.start(t);
  }

  function scheduleStep(sectionName, idx, t) {
    var s = SECTIONS[sectionName], dest = gains[sectionName], d = stepDur();
    if (s.bass[idx]) tone(dest, s.bass[idx], t, d * 0.9, 'sawtooth', 0.25);
    if (s.lead[idx]) tone(dest, s.lead[idx], t, d * 1.6, sectionName === 'A' ? 'triangle' : 'square', 0.12);
    if (s.hat[idx]) hat(dest, t, idx % 4 === 0 ? 0.25 : 0.12);
    if (idx % 4 === 0) tone(dest, idx === 0 ? 60 : 50, t, 0.12, 'sine', 0.6); // kick, accent on downbeat
  }

  function doSwitch(to, at, xfDur) {
    var from = current;
    if (xfDur > 0) {
      gains[from].gain.setValueAtTime(1, at); gains[from].gain.linearRampToValueAtTime(0, at + xfDur);
      gains[to].gain.setValueAtTime(0, at); gains[to].gain.linearRampToValueAtTime(1, at + xfDur);
    } else {
      gains[from].gain.setValueAtTime(0, at); gains[to].gain.setValueAtTime(1, at);
    }
    current = to;
    history.push({ t: at, section: to });
    switchEvents.push({ t: at, kind: 'switch' });
    applyLang();
  }

  function tick() {
    while (nextStepTime < ctx.currentTime + LOOKAHEAD) {
      var t = nextStepTime, idx = step % 16;
      // resolve pending transitions on the grid
      if (pending && pending.mode === 'beat' && idx % 4 === 0 && t >= pending.reqAt) { doSwitch(pending.to, t, 0); pending = null; }
      if (pending && (pending.mode === 'bar' || pending.mode === 'crossfade') && idx === 0 && t >= pending.reqAt) {
        doSwitch(pending.to, t, pending.mode === 'crossfade' ? stepDur() * 16 : 0);
        if (pending.mode === 'crossfade') pending = { mode: 'xf-running', until: t + stepDur() * 16 }; else pending = null;
      }
      if (pending && pending.mode === 'xf-running' && t >= pending.until) pending = null;
      // during crossfade both sections sound; otherwise only current is audible (the other gain = 0, so skip scheduling it)
      var both = pending && pending.mode === 'xf-running';
      scheduleStep(current, idx, t);
      if (both) scheduleStep(current === 'A' ? 'B' : 'A', idx, t);
      nextStepTime += stepDur();
      step++;
    }
    updatePending();
  }

  // ------------------------------------------------------------ controls
  var playBtn = document.getElementById('play'), switchBtn = document.getElementById('switch'),
      bpmInput = document.getElementById('bpm'), nowEl = document.getElementById('nowSection'), pendingEl = document.getElementById('pending');

  function mode() { return document.querySelector('input[name=mode]:checked').value; }

  playBtn.addEventListener('click', function () {
    ensureAudio();
    if (!running) {
      if (ctx.state === 'suspended') ctx.resume();
      running = true; step = 0; nextStepTime = ctx.currentTime + 0.05; history = [{ t: nextStepTime, section: current }]; switchEvents = []; pending = null;
      gains.A.gain.value = current === 'A' ? 1 : 0; gains.B.gain.value = current === 'B' ? 1 : 0;
      timer = setInterval(tick, TICK_MS); switchBtn.disabled = false;
    } else {
      running = false; clearInterval(timer); switchBtn.disabled = true; pending = null;
      gains.A.gain.cancelScheduledValues(0); gains.B.gain.cancelScheduledValues(0);
      gains.A.gain.value = 0; gains.B.gain.value = 0;
      setTimeout(function () { if (!running) { gains.A.gain.value = current === 'A' ? 1 : 0; gains.B.gain.value = current === 'B' ? 1 : 0; } }, 300);
    }
    applyLang();
  });

  switchBtn.addEventListener('click', function () {
    if (!running || (pending && pending.mode === 'xf-running')) return;
    var to = current === 'A' ? 'B' : 'A', m = mode(), now = ctx.currentTime;
    switchEvents.push({ t: now, kind: 'request' });
    if (m === 'immediate') { doSwitch(to, now, 0); pending = null; }
    else pending = { mode: m, to: to, reqAt: now };
    updatePending();
  });

  bpmInput.addEventListener('change', function () { bpm = Math.min(180, Math.max(60, +bpmInput.value || 120)); bpmInput.value = bpm; });

  function updatePending() {
    nowEl.textContent = current; nowEl.style.color = SECTIONS[current].color;
    var txt = '';
    if (pending) txt = pending.mode === 'beat' ? I18N[lang].pending_beat : pending.mode === 'bar' ? I18N[lang].pending_bar : I18N[lang].pending_xf;
    pendingEl.textContent = txt;
  }

  // ------------------------------------------------------------ timeline visualisation
  var cv = document.getElementById('viz'), g2 = cv.getContext('2d');
  var WINDOW = 8; // seconds shown
  function draw() {
    requestAnimationFrame(draw);
    var W = cv.width, H = cv.height;
    g2.clearRect(0, 0, W, H);
    if (!ctx) { g2.fillStyle = '#666'; g2.font = '16px system-ui'; g2.fillText(lang === 'zh' ? '按「開始」後這裡會顯示拍點與轉場時機' : 'Press Start to see beats and transition timing here', 20, H / 2); return; }
    var now = ctx.currentTime, t0 = now - WINDOW * 0.75, px = W / WINDOW;
    // section bands
    for (var i = 0; i < history.length; i++) {
      var a = history[i].t, b = i + 1 < history.length ? history[i + 1].t : now + WINDOW;
      g2.fillStyle = SECTIONS[history[i].section].color + '33';
      g2.fillRect((a - t0) * px, 40, (b - a) * px, H - 80);
    }
    // beat grid (derived from the scheduler's step clock)
    if (running) {
      var sd = stepDur(), firstStepT = nextStepTime - step * sd;
      var k0 = Math.floor((t0 - firstStepT) / (sd * 4));
      for (var k = k0; ; k++) {
        var bt = firstStepT + k * sd * 4; if (bt > now + WINDOW) break; if (k < 0) continue;
        var x = (bt - t0) * px, isBar = k % BEATS_PER_BAR === 0;
        g2.strokeStyle = isBar ? '#e8e6e1' : '#555'; g2.lineWidth = isBar ? 2 : 1;
        g2.beginPath(); g2.moveTo(x, isBar ? 30 : 40); g2.lineTo(x, H - (isBar ? 30 : 40)); g2.stroke();
      }
    }
    // events
    switchEvents.forEach(function (e) {
      var x = (e.t - t0) * px;
      g2.fillStyle = e.kind === 'request' ? '#ff6b6b' : '#7fd67f';
      g2.beginPath(); g2.arc(x, e.kind === 'request' ? 20 : H - 20, 6, 0, Math.PI * 2); g2.fill();
    });
    // playhead
    var xn = (now - t0) * px;
    g2.strokeStyle = '#fff'; g2.lineWidth = 2; g2.beginPath(); g2.moveTo(xn, 0); g2.lineTo(xn, H); g2.stroke();
    // legend
    g2.font = '12px system-ui'; g2.fillStyle = '#ff6b6b'; g2.fillText(lang === 'zh' ? '● 按下切換' : '● switch requested', 8, 14);
    g2.fillStyle = '#7fd67f'; g2.fillText(lang === 'zh' ? '● 實際切換' : '● actual switch', 8, H - 6);
  }

  applyLang();
  updatePending();
  draw();
})();
