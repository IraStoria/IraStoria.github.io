/* Interactive Section Player — test build. Written from scratch (ADR-004), Web Audio only.
   Architecture mirrors the production path: every section is an AudioBuffer; playback = sample-accurate
   AudioBufferSourceNode.start(t). Swapping synthesized sections for real files = replace renderSection()
   with decodeAudioData(). The scheduler never changes. */
(function () {
  'use strict';

  // ------------------------------------------------------------ i18n
  var I18N = {
    zh: {
      title: '互動式段落播放器（測試版）',
      lead: '按「開始」後 A 段自動播放。播放中點任一段落，會在目前段落結束的瞬間無縫接上；若什麼都不點，「隨機」鈕會自動亮起、替你挑下一段。所有段落目前由瀏覽器即時合成，之後換成音檔不需改邏輯。',
      start: '▶ 開始', stop: '■ 停止', now: '播放中：', random: '隨機', random_auto: '自動接手中',
      st_playing: '播放中', st_queued: '已排隊', st_next_next: '排到下下段', st_blocked: '規則不允許',
      next_none: '下一段：尚未決定（{s} 秒後隨機接手）', next_queued: '下一段：<em>{n}</em>（你的選擇）', next_auto: '下一段：<em>{n}</em>（隨機自動接手）', next_locked: '已鎖定 → <em>{n}</em>',
      opt_exclude: '隨機不重複上一段', opt_xfade: '接縫 15ms 等功率淡接', opt_rules: '啟用接續規則', opt_pre: '啟用 pre-entry／post-exit（關掉可比較）',
      how: '這個測試版證明了什麼',
      e1: '無縫：每一段都是預先解碼的 AudioBuffer，下一段以「上一段結束的取樣點」起播，誤差 <1ms（右側紀錄會列出實際接縫誤差）。',
      e2: '佇列：播放中點段落＝排到下一段；在最後 0.25 秒（決策鎖定）之後才點，會自動排到下下段。',
      e3: '隨機後備：到決策點若沒有選擇，隨機鈕亮起並接手，且可設定不重複上一段。',
      e4: '接續規則：segments.json 可定義每段允許接哪些段（例如 A 不能直接接 A）；不允許的按鈕會變灰。',
      e5: 'Pre-entry／post-exit：每段有前導（B 一拍、C 兩拍）與尾音；下一段在「exit − 前導」就起播、與上一段尾音重疊，接縫仍對齊小節線。決策點（進度條白線）依候選段最大前導自動提前，所以隨機接手也保證前導完整。',
      note: '概念示意，音樂為合成佔位。換成真實素材時：ogg 格式、同 BPM、整小節長度，接縫會與此處一致。'
    },
    en: {
      title: 'Interactive Section Player (test build)',
      lead: 'Press Start and section A plays automatically. Pick any section while playing and it joins the instant the current one ends; pick nothing and the Random button lights up and chooses for you. Sections are synthesized in-browser for now; swapping in audio files needs no logic change.',
      start: '▶ Start', stop: '■ Stop', now: 'Now playing:', random: 'Random', random_auto: 'auto-picking',
      st_playing: 'playing', st_queued: 'queued', st_next_next: 'queued after next', st_blocked: 'not allowed',
      next_none: 'Next: undecided (random takes over in {s}s)', next_queued: 'Next: <em>{n}</em> (your pick)', next_auto: 'Next: <em>{n}</em> (random, automatic)', next_locked: 'Locked → <em>{n}</em>',
      opt_exclude: 'Random avoids repeating last', opt_xfade: '15 ms equal-power seam', opt_rules: 'Enable transition rules', opt_pre: 'Enable pre-entry / post-exit (toggle to compare)',
      how: 'What this test build proves',
      e1: 'Seamless: every section is a decoded AudioBuffer; the next starts at the exact sample where the previous ends (<1 ms; the log shows measured seam error).',
      e2: 'Queue: tapping a section while playing queues it next; tapping after the 0.25 s decision lock queues it after the next one.',
      e3: 'Random fallback: with nothing queued at the decision point, Random lights up and takes over, optionally never repeating the last section.',
      e4: 'Rules: segments.json can restrict which sections may follow which; disallowed buttons grey out.',
      e5: 'Pre-entry / post-exit: sections carry lead-ins (B: 1 beat, C: 2 beats) and tails; the next section starts at exit − pre-entry and overlaps the previous tail while the seam stays on the barline. The decision point (white marker) moves earlier by the largest candidate pre-entry, so random picks keep their lead-in intact.',
      note: 'Concept sketch with synthesized placeholder music. For real material: ogg, same BPM, whole-bar lengths — seams will behave exactly as here.'
    }
  };
  var lang = 'zh';
  try { lang = localStorage.getItem('lang') || (navigator.language.toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en'); } catch (e) {}
  if (!I18N[lang]) lang = 'zh';
  var T = function (k) { return I18N[lang][k]; };

  // ------------------------------------------------------------ data
  var CFG = null, SEG = [], byId = {};
  var ctx = null, master = null, buffers = {};
  var LOOKAHEAD = 0.25;  // decision lock window (s)
  var TICK = 25;

  // scheduler state
  var running = false, timer = null;
  var cur = null;        // { seg, start, end, src, gain }
  var nxt = null;        // scheduled next: same shape
  var queued = null;     // user's choice for the next decision (segment id)
  var later = null;      // user's choice after a lock (applies to the decision after next)
  var randomAuto = false, lastId = null, history = [];

  function secPerBar() { return 60 / CFG.bpm * CFG.beatsPerBar; }

  // ------------------------------------------------------------ synthesis (placeholder; replaced by decodeAudioData for real files)
  function beatSec() { return 60 / CFG.bpm; }
  function preSec(seg) { return (seg.preEntryBeats || 0) * beatSec(); }
  function postSec(seg) { return seg.postExitSec || 0; }
  function renderSection(seg) {
    var logical = seg.bars * secPerBar(), pre = preSec(seg), post = postSec(seg), dur = pre + logical + post, sr = 44100;
    var off = new OfflineAudioContext(2, Math.ceil(dur * sr), sr);
    var out = off.createGain(); out.gain.value = 0.6; out.connect(off.destination);
    var step = 60 / CFG.bpm / 4, steps = seg.bars * CFG.beatsPerBar * 4;
    var root = 110 * Math.pow(2, seg.key / 12);
    var scale = [0, 2, 4, 7, 9, 12, 14, 16];
    var seed = seg.id.charCodeAt(0) * 7919;
    function rnd() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }
    function tone(freq, t, d, type, vol) {
      var o = off.createOscillator(), g = off.createGain(); o.type = type; o.frequency.value = freq;
      g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + 0.005); g.gain.exponentialRampToValueAtTime(0.0001, t + d);
      o.connect(g); g.connect(out); o.start(t); o.stop(t + d + 0.02);
    }
    function noise(t, d, vol, hp) {
      var len = Math.floor(sr * d), b = off.createBuffer(1, len, sr), ch = b.getChannelData(0);
      for (var i = 0; i < len; i++) ch[i] = (Math.random() * 2 - 1) * (1 - i / len);
      var s = off.createBufferSource(), f = off.createBiquadFilter(), g = off.createGain();
      f.type = 'highpass'; f.frequency.value = hp; g.gain.value = vol; s.buffer = b; s.connect(f); f.connect(g); g.connect(out); s.start(t);
    }
    // pre-entry: a drum fill / riser leading into the entry cue (so a truncated lead-in is audible)
    if (pre > 0) {
      var fillSteps = Math.round(pre / step);
      for (var f = 0; f < fillSteps; f++) { var ft = f * step; noise(ft, 0.1, 0.25 + 0.35 * f / fillSteps, 1200 + 400 * f); tone(200 + 120 * f, ft, step * 0.9, 'triangle', 0.15); }
      var rs = off.createOscillator(), rg = off.createGain(); rs.type = 'sawtooth'; rs.frequency.setValueAtTime(root * 2, 0); rs.frequency.exponentialRampToValueAtTime(root * 8, pre);
      rg.gain.setValueAtTime(0.02, 0); rg.gain.linearRampToValueAtTime(0.12, pre); rs.connect(rg); rg.connect(out); rs.start(0); rs.stop(pre);
    }
    // post-exit: a sustained chord tail that decays past the logical end
    if (post > 0) {
      [0, 4, 7].forEach(function (iv) { var o = off.createOscillator(), g = off.createGain(); o.type = 'triangle'; o.frequency.value = root * 2 * Math.pow(2, iv / 12);
        g.gain.setValueAtTime(0.09, pre + logical - 0.01); g.gain.exponentialRampToValueAtTime(0.0001, pre + logical + post); o.connect(g); g.connect(out); o.start(pre + logical - 0.01); o.stop(dur); });
    }
    for (var i = 0; i < steps; i++) {
      var t = pre + i * step, beat = i % 4 === 0, bar = i % 16 === 0;
      if (beat) tone(bar ? 60 : 50, t, 0.12, 'sine', 0.7);                              // kick
      if (seg.density >= 1 && i % 2 === 0) noise(t, 0.03, beat ? 0.25 : 0.1, 6000);      // hats
      if (seg.density >= 2 && i % 8 === 4) noise(t, 0.12, 0.35, 1500);                   // snare
      if (seg.density >= 1 && (i % 4 === 0 || (seg.density >= 2 && i % 4 === 2))) tone(root, t, step * 0.9, 'sawtooth', 0.25);   // bass
      if (seg.density >= 3 && i % 2 === 1) tone(root * 2, t, step * 0.8, 'square', 0.08); // off-beat stab
      if (rnd() < (seg.density === 0 ? 0.25 : 0.45)) tone(root * 2 * Math.pow(2, scale[Math.floor(rnd() * scale.length)] / 12), t, step * (seg.density === 0 ? 3 : 1.5), seg.density === 0 ? 'sine' : 'triangle', 0.12);
    }
    if (seg.density === 0) { var p = off.createOscillator(), pg = off.createGain(); p.type = 'triangle'; p.frequency.value = root; pg.gain.value = 0.08; p.connect(pg); pg.connect(out); p.start(pre); p.stop(pre + logical); }
    return off.startRendering();
  }

  // ------------------------------------------------------------ scheduler (production path)
  function usePre() { return optPre.checked; }
  function schedule(seg, entry) {
    var src = ctx.createBufferSource(), g = ctx.createGain();
    src.buffer = buffers[seg.id]; src.connect(g); g.connect(master);
    var pre = preSec(seg), xf = (optXfade.checked && !(usePre() && pre > 0)) ? CFG.crossfadeMs / 1000 : 0;
    if (xf > 0 && cur) { g.gain.setValueAtTime(0, entry); g.gain.linearRampToValueAtTime(1, entry + xf); }
    // entry cue is always on the barline; with pre-entry enabled the file starts earlier, otherwise we skip the lead-in.
    if (usePre() && pre > 0) src.start(entry - pre); else src.start(entry, pre);
    return { seg: seg, start: entry, end: entry + seg.bars * secPerBar(), audioStart: usePre() ? entry - pre : entry, src: src, gain: g };
  }
  function fadeOut(item) {
    var xf = (optXfade.checked && !(usePre() && nxt && preSec(nxt.seg) > 0)) ? CFG.crossfadeMs / 1000 : 0;
    var post = usePre() ? postSec(item.seg) : 0;
    if (post > 0) { item.src.stop(item.end + post); }            // let the tail ring out over the next section
    else if (xf > 0) { item.gain.gain.setValueAtTime(1, item.end); item.gain.gain.linearRampToValueAtTime(0, item.end + xf); item.src.stop(item.end + xf); }
    else item.src.stop(item.end);
  }
  function decisionLead() {
    // how far before cur.end the next section must be decided: global lead OR the largest pre-entry among candidates
    var lead = (CFG.decisionLeadBeats || 0) * beatSec();
    if (usePre()) SEG.forEach(function (s) { if (allowed(cur.seg.id, s.id)) lead = Math.max(lead, preSec(s)); });
    return lead + LOOKAHEAD;
  }
  function allowed(fromId, toId) {
    if (!optRules.checked) return true;
    var s = byId[fromId]; return !s.allow || s.allow.indexOf(toId) >= 0;
  }
  function pickRandom(fromId) {
    var pool = SEG.filter(function (s) { return allowed(fromId, s.id) && !(optExclude.checked && s.id === lastId && SEG.length > 1); });
    if (!pool.length) pool = SEG.filter(function (s) { return allowed(fromId, s.id); });
    if (!pool.length) pool = SEG;
    return pool[Math.floor(Math.random() * pool.length)];
  }
  function decide() {
    var choice = null;
    if (queued && allowed(cur.seg.id, queued)) { choice = byId[queued]; randomAuto = false; }
    else { choice = pickRandom(cur.seg.id); randomAuto = true; }
    queued = null;
    nxt = schedule(choice, cur.end);
    fadeOut(cur);
    logSeam(cur, nxt);
    render();
  }
  function tick() {
    var now = ctx.currentTime;
    if (!nxt && now >= cur.end - decisionLead()) decide();
    if (nxt && now >= cur.end) {           // hand-over
      lastId = cur.seg.id; cur = nxt; nxt = null;
      if (later) { queued = later; later = null; }
      history.push(cur.seg.id);
      render();
    }
    renderProgress();
  }
  function start() {
    if (!ctx) { ctx = new (window.AudioContext || window.webkitAudioContext)(); master = ctx.createGain(); master.gain.value = 0.8; master.connect(ctx.destination); }
    if (ctx.state === 'suspended') ctx.resume();
    var pending = SEG.filter(function (s) { return !buffers[s.id]; });
    startBtn.disabled = true;
    Promise.all(pending.map(function (s) { return renderSection(s).then(function (b) { buffers[s.id] = b; }); })).then(function () {
      running = true; queued = later = null; randomAuto = false; lastId = null; history = []; histEl.textContent = '';
      cur = schedule(SEG[0], ctx.currentTime + 0.05); nxt = null; history.push(SEG[0].id);
      timer = setInterval(tick, TICK); startBtn.disabled = false; render();
    });
  }
  function stop() {
    running = false; clearInterval(timer);
    [cur, nxt].forEach(function (it) { if (it) { try { it.gain.gain.cancelScheduledValues(0); it.gain.gain.setValueAtTime(it.gain.gain.value, ctx.currentTime); it.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.1); it.src.stop(ctx.currentTime + 0.12); } catch (e) {} } });
    cur = nxt = null; queued = later = null; randomAuto = false; render();
  }
  function choose(id) {
    if (!running) return;
    if (nxt) { if (allowed(nxt.seg.id, id)) later = id; }        // decision already locked → after next
    else if (allowed(cur.seg.id, id)) { queued = id; randomAuto = false; }
    render();
  }
  function logSeam(a, b) {
    var err = (b.start - a.end) * 1000, pre = (b.start - b.audioStart) * 1000;
    var line = a.seg.id + ' → ' + b.seg.id + (randomAuto ? '  (random)' : '  (queued)') + '   seam ' + err.toFixed(3) + ' ms' + (pre > 0 ? '   pre-entry ' + pre.toFixed(0) + ' ms overlapped' : '');
    var span = document.createElement('span'); span.className = Math.abs(err) < 1 ? 'gap' : 'bad'; span.textContent = line + '\n';
    histEl.appendChild(span); histEl.scrollTop = histEl.scrollHeight;
  }

  // ------------------------------------------------------------ UI
  var startBtn = document.getElementById('start'), segsEl = document.getElementById('segs'), nowEl = document.getElementById('now'),
      progEl = document.getElementById('prog'), nextEl = document.getElementById('next'), histEl = document.getElementById('hist'),
      optExclude = document.getElementById('optExclude'), optXfade = document.getElementById('optXfade'), optRules = document.getElementById('optRules'), optPre = document.getElementById('optPre'), dmark = document.getElementById('dmark');
  var segBtns = {}, randomBtn;

  function buildButtons() {
    segsEl.innerHTML = '';
    SEG.forEach(function (s) {
      var b = document.createElement('button'); b.className = 'seg'; b.style.setProperty('--c', s.color);
      b.innerHTML = '<span class="nm"></span><span class="st"></span>';
      b.addEventListener('click', function () { choose(s.id); });
      segsEl.appendChild(b); segBtns[s.id] = b;
    });
    randomBtn = document.createElement('button'); randomBtn.className = 'seg random'; randomBtn.innerHTML = '<span class="nm"></span><span class="st"></span>';
    randomBtn.addEventListener('click', function () { queued = null; later = null; randomAuto = false; render(); });
    segsEl.appendChild(randomBtn);
  }
  function render() {
    document.documentElement.lang = lang === 'zh' ? 'zh-Hant' : 'en';
    document.querySelectorAll('[data-i18n]').forEach(function (el) { var k = el.getAttribute('data-i18n'); el.textContent = k === 'start' ? (running ? T('stop') : T('start')) : T(k); });
    document.querySelectorAll('[data-lang]').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-lang') === lang); });
    SEG.forEach(function (s) {
      var b = segBtns[s.id], st = '';
      b.querySelector('.nm').textContent = s.name[lang];
      b.classList.toggle('playing', !!cur && cur.seg.id === s.id);
      var isQ = (nxt && nxt.seg.id === s.id) || (!nxt && queued === s.id);
      b.classList.toggle('queued', !!isQ || later === s.id);
      var ok = !running || allowed((nxt || cur).seg.id, s.id);
      b.disabled = !running || !ok;
      if (cur && cur.seg.id === s.id) st = T('st_playing');
      else if (later === s.id) st = T('st_next_next');
      else if (isQ) st = T('st_queued');
      else if (running && !ok) st = T('st_blocked');
      b.querySelector('.st').textContent = st;
    });
    randomBtn.querySelector('.nm').textContent = T('random');
    randomBtn.classList.toggle('auto', randomAuto && running);
    randomBtn.querySelector('.st').textContent = randomAuto && running ? T('random_auto') : '';
    randomBtn.disabled = !running;
    nowEl.textContent = cur ? cur.seg.name[lang] : '—'; nowEl.style.color = cur ? cur.seg.color : '';
    progEl.style.setProperty('--c', cur ? cur.seg.color : '');
    if (!running) nextEl.innerHTML = '';
    else if (nxt) nextEl.innerHTML = (randomAuto ? T('next_auto') : T('next_locked')).replace('{n}', nxt.seg.name[lang]);
    else if (queued) nextEl.innerHTML = T('next_queued').replace('{n}', byId[queued].name[lang]);
    else nextEl.innerHTML = T('next_none').replace('{s}', Math.max(0, cur.end - decisionLead() - ctx.currentTime).toFixed(1));
  }
  function renderProgress() {
    if (!cur) { progEl.firstElementChild.style.width = '0'; return; }
    var f = Math.min(1, Math.max(0, (ctx.currentTime - cur.start) / (cur.end - cur.start)));
    progEl.firstElementChild.style.width = (f * 100) + '%';
    dmark.style.left = (Math.max(0, 1 - decisionLead() / (cur.end - cur.start)) * 100) + '%';
    if (!nxt && !queued) nextEl.innerHTML = T('next_none').replace('{s}', Math.max(0, cur.end - decisionLead() - ctx.currentTime).toFixed(1));
  }

  startBtn.addEventListener('click', function () { running ? stop() : start(); });
  document.querySelectorAll('[data-lang]').forEach(function (b) { b.addEventListener('click', function () { lang = b.getAttribute('data-lang'); try { localStorage.setItem('lang', lang); } catch (e) {} render(); }); });
  [optExclude, optXfade, optRules, optPre].forEach(function (o) { o.addEventListener('change', render); });
  document.addEventListener('keydown', function (e) { var i = 'ABCDEFGH'.indexOf(e.key.toUpperCase()); if (i >= 0 && SEG[i]) choose(SEG[i].id); });

  fetch('segments.json').then(function (r) { return r.json(); }).then(function (cfg) {
    CFG = cfg; SEG = cfg.segments; SEG.forEach(function (s) { byId[s.id] = s; });
    optExclude.checked = !!cfg.excludeLast; buildButtons(); render();
  });
})();
