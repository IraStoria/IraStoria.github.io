/* Interactive Section Player — Theme A. Written from scratch (ADR-004), Web Audio only.
   Every section is a decoded AudioBuffer; playback = sample-accurate AudioBufferSourceNode.start(t).
   File model (from the file name Theme_Section_pickupBars_tailBars_Info):
     [pick-up bars @ bpmIn][logical section][tail bars @ bpmOut]
   The entry cue of the next section lands exactly on the logical end of the current one; the pick-up
   starts earlier and overlaps the previous tail. Logical length = decoded duration − pick-up − tail. */
(function () {
  'use strict';

  /* ------------------------------------------------------------ i18n */
  var I18N = {
    zh: {
      title: '互動式段落播放器 · Theme A',
      lead: '按「開始」後從第一段播放。播放中點任一段落，會在目前段落結束的瞬間無縫接上；若什麼都不點，「隨機」鈕會自動亮起、替你挑下一段。每段自帶 pick-up（前導）與 tail（尾音），接縫永遠落在小節線上。',
      start: '▶ 開始', stop: '■ 停止', now: '播放中：', random: '隨機', random_auto: '自動接手中', loading: '載入音檔…',
      st_playing: '播放中', st_queued: '已排隊', st_next_next: '排到下下段', st_blocked: '規則不允許', st_loop: '循環中',
      next_none: '下一段：尚未決定（{s} 秒後隨機接手）', next_queued: '下一段：<em>{n}</em>（你的選擇）', next_auto: '下一段：<em>{n}</em>（隨機自動接手）', next_locked: '已鎖定 → <em>{n}</em>', next_loop: '下一段：<em>{n}</em>（自我循環，點其他段可離開）',
      opt_exclude: '隨機不重複上一段', opt_xfade: '接縫 15ms 等功率淡接（僅無 pick-up 時）', opt_rules: '啟用接續規則', opt_pre: '啟用 pick-up／tail（關掉可比較）',
      pick: 'pick-up', tail: 'tail',
      how: '這個播放器怎麼運作',
      e1: '無縫：每一段都是預先解碼的 AudioBuffer，下一段以「上一段邏輯結束的取樣點」起播，誤差 <1ms（下方紀錄會列出實際接縫誤差）。',
      e2: '佇列：播放中點段落＝排到下一段；在決策鎖定之後才點，會自動排到下下段。',
      e3: '隨機後備：到決策點若沒有選擇，隨機鈕亮起並接手，且可設定不重複上一段。I（尾奏循環）沒被指定下一段時會自我循環。',
      e4: '接續規則：segments.json 的 allow 定義每段允許接哪些段；不允許的按鈕會變灰。',
      e5: 'Pick-up／tail：檔名「段落_pick-up小節_tail小節」直接決定時間模型。下一段在「exit − pick-up」就起播、與上一段 tail 重疊；決策點（進度條白線）依候選段最大 pick-up 自動提前。',
      e6: '速度：B–D 為 145 BPM；E 段在段內加速；E 之後（F–I）為 172 BPM。每段各自帶 bpm，小節換算依段落而定。',
      note: '素材為 IraStoria 原創曲 Theme A 的段落切片（mp3）。'
    },
    en: {
      title: 'Interactive Section Player · Theme A',
      lead: 'Press Start and the first section plays. Pick any section while playing and it joins the instant the current one ends; pick nothing and the Random button lights up and chooses for you. Every section carries its own pick-up and tail; seams always land on the barline.',
      start: '▶ Start', stop: '■ Stop', now: 'Now playing:', random: 'Random', random_auto: 'auto-picking', loading: 'Loading audio…',
      st_playing: 'playing', st_queued: 'queued', st_next_next: 'queued after next', st_blocked: 'not allowed', st_loop: 'looping',
      next_none: 'Next: undecided (random takes over in {s}s)', next_queued: 'Next: <em>{n}</em> (your pick)', next_auto: 'Next: <em>{n}</em> (random, automatic)', next_locked: 'Locked → <em>{n}</em>', next_loop: 'Next: <em>{n}</em> (self-loop; pick another section to leave)',
      opt_exclude: 'Random avoids repeating last', opt_xfade: '15 ms equal-power seam (only without pick-up)', opt_rules: 'Enable transition rules', opt_pre: 'Enable pick-up / tail (toggle to compare)',
      pick: 'pick-up', tail: 'tail',
      how: 'How it works',
      e1: 'Seamless: every section is a decoded AudioBuffer; the next starts at the exact sample where the previous logically ends (<1 ms; the log shows measured seam error).',
      e2: 'Queue: tapping a section while playing queues it next; tapping after the decision lock queues it after the next one.',
      e3: 'Random fallback: with nothing queued at the decision point, Random lights up and takes over, optionally never repeating the last section. I (outro loop) loops itself when nothing else is queued.',
      e4: 'Rules: the allow map in segments.json restricts which sections may follow which; disallowed buttons grey out.',
      e5: 'Pick-up / tail: the file name "Section_pickupBars_tailBars" defines the timing model. The next section starts at exit − pick-up and overlaps the previous tail; the decision point (white marker) moves earlier by the largest candidate pick-up.',
      e6: 'Tempo: B–D run at 145 BPM; E accelerates inside the section; everything after E (F–I) runs at 172 BPM. Each section carries its own bpm for bar maths.',
      note: 'Material: section slices (mp3) of the original IraStoria track Theme A.'
    }
  };
  var lang = 'zh';
  try { lang = localStorage.getItem('lang') || (navigator.language.toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en'); } catch (e) {}
  if (!I18N[lang]) lang = 'zh';
  var T = function (k) { return I18N[lang][k]; };

  /* ------------------------------------------------------------ data */
  var CFG = null, SEG = [], byId = {}, GROUPS = [], byGroup = {};
  var ctx = null, master = null, buffers = {}, analyser = null;
  var LOOKAHEAD = 0.25;  /* decision lock window (s) */
  var TICK = 25;

  /* scheduler state */
  var running = false, timer = null;
  var cur = null;        /* { seg, start, end, src, gain } */
  var nxt = null;        /* scheduled next: same shape */
  var queued = null;     /* user's choice for the next decision (segment id) */
  var later = null;      /* user's choice after a lock (applies to the decision after next) */
  var randomAuto = false, lastId = null, history = [];

  /* ------------------------------------------------------------ timing model (per segment) */
  function barSec(bpm) { return 60 / bpm * CFG.beatsPerBar; }
  function preSec(seg) { return (seg.preBars || 0) * barSec(seg.bpmIn); }      /* pick-up runs at the incoming tempo */
  function postSec(seg) { return (seg.tailBars || 0) * barSec(seg.bpmOut); }   /* tail rings at the outgoing tempo */
  function logicalSec(seg) { var b = buffers[seg.id]; return b ? Math.max(0.1, b.duration - preSec(seg) - postSec(seg)) : 0; }
  function colorOf(seg) { return (byGroup[seg.group] || {}).color || '#e0b04a'; }
  function labelOf(seg) { return seg.id.split('_')[0]; }

  /* ------------------------------------------------------------ audio loading */
  function loadBuffer(seg) {
    return fetch(seg.file).then(function (r) { if (!r.ok) throw new Error(seg.file + ' ' + r.status); return r.arrayBuffer(); })
      .then(function (ab) { return ctx.decodeAudioData(ab); });
  }

  /* ------------------------------------------------------------ scheduler */
  function usePre() { return optPre.checked; }
  function schedule(seg, entry) {
    var src = ctx.createBufferSource(), g = ctx.createGain();
    src.buffer = buffers[seg.id]; src.connect(g); g.connect(master);
    var pre = preSec(seg), xf = (optXfade.checked && !(usePre() && pre > 0)) ? CFG.crossfadeMs / 1000 : 0;
    if (xf > 0 && cur) { g.gain.setValueAtTime(0, entry); g.gain.linearRampToValueAtTime(1, entry + xf); }
    /* entry cue is always on the barline; with pick-up enabled the file starts earlier, otherwise we skip the lead-in */
    if (usePre() && pre > 0) src.start(entry - pre); else src.start(entry, pre);
    return { seg: seg, start: entry, end: entry + logicalSec(seg), audioStart: usePre() ? entry - pre : entry, src: src, gain: g };
  }
  function fadeOut(item) {
    var xf = (optXfade.checked && !(usePre() && nxt && preSec(nxt.seg) > 0)) ? CFG.crossfadeMs / 1000 : 0;
    var post = usePre() ? postSec(item.seg) : 0;
    if (post > 0) { item.src.stop(item.end + post); }            /* let the tail ring out over the next section */
    else if (xf > 0) { item.gain.gain.setValueAtTime(1, item.end); item.gain.gain.linearRampToValueAtTime(0, item.end + xf); item.src.stop(item.end + xf); }
    else item.src.stop(item.end);
  }
  function decisionLead() {
    /* how far before cur.end the next section must be decided: global lead (at the outgoing tempo) OR the largest pick-up among candidates */
    var lead = (CFG.decisionLeadBeats || 0) * (60 / cur.seg.bpmOut);
    if (usePre()) SEG.forEach(function (s) { if (allowed(cur.seg.id, s.id)) lead = Math.max(lead, preSec(s)); });
    return lead + LOOKAHEAD;
  }
  function allowed(fromId, toId) {
    if (!optRules.checked) return true;
    var a = CFG.allow && CFG.allow[fromId]; if (!a) a = CFG.allow && CFG.allow[byId[fromId].group];   /* per-segment first, then per-group */
    if (!a) return true;
    return a.indexOf(toId) >= 0 || a.indexOf(byId[toId].group) >= 0;
  }
  function pickRandom(fromId) {
    var from = byId[fromId];
    if (from.loop) return from;   /* outro loop: keeps looping itself until the user picks something */
    var pool = SEG.filter(function (s) { return allowed(fromId, s.id) && !(optExclude.checked && s.id === lastId && SEG.length > 1) && s.id !== fromId; });
    if (!pool.length) pool = SEG.filter(function (s) { return allowed(fromId, s.id); });
    if (!pool.length) pool = SEG;
    return pool[Math.floor(Math.random() * pool.length)];
  }
  function decide() {
    var choice = null;
    if (queued && allowed(cur.seg.id, queued)) { choice = byId[queued]; randomAuto = false; }
    else { choice = pickRandom(cur.seg.id); randomAuto = !choice.loop; }
    queued = null;
    nxt = schedule(choice, cur.end);
    fadeOut(cur);
    logSeam(cur, nxt);
    render();
  }
  function tick() {
    var now = ctx.currentTime;
    if (!nxt && now >= cur.end - decisionLead()) decide();
    if (nxt && now >= cur.end) {           /* hand-over */
      lastId = cur.seg.id; cur = nxt; nxt = null;
      if (later) { queued = later; later = null; }
      history.push(cur.seg.id);
      render();
    }
    renderProgress();
  }
  function start() {
    if (!ctx) { ctx = new (window.AudioContext || window.webkitAudioContext)(); master = ctx.createGain(); master.gain.value = 0.9; analyser = ctx.createAnalyser(); analyser.fftSize = 2048; analyser.minDecibels = -96; analyser.maxDecibels = 6; master.connect(analyser); analyser.connect(ctx.destination); }
    if (ctx.state === 'suspended') ctx.resume();
    var pending = SEG.filter(function (s) { return !buffers[s.id]; }), done = 0;
    startBtn.disabled = true; if (pending.length) startBtn.textContent = T('loading') + ' 0/' + pending.length;
    Promise.all(pending.map(function (s) { return loadBuffer(s).then(function (b) { buffers[s.id] = b; done++; startBtn.textContent = T('loading') + ' ' + done + '/' + pending.length; }); })).then(function () {
      if (pending.length) logLoad(pending);
      running = true; queued = later = null; randomAuto = false; lastId = null; history = [];
      cur = schedule(SEG[0], ctx.currentTime + 0.05 + (usePre() ? preSec(SEG[0]) : 0)); nxt = null;   /* first entry sits after its own pick-up: start times can't be negative */ history.push(SEG[0].id);
      timer = setInterval(tick, TICK); startBtn.disabled = false; render();
    }).catch(function (err) { running = false; clearInterval(timer); cur = nxt = null; startBtn.disabled = false; var s = document.createElement('span'); s.className = 'bad'; s.textContent = 'start error: ' + (err && err.stack || err) + '\n'; histEl.appendChild(s); render(); });
  }
  function stop() {
    running = false; clearInterval(timer);
    [cur, nxt].forEach(function (it) { if (it) { try { it.gain.gain.cancelScheduledValues(0); it.gain.gain.setValueAtTime(it.gain.gain.value, ctx.currentTime); it.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.1); it.src.stop(ctx.currentTime + 0.12); } catch (e) {} } });
    cur = nxt = null; queued = later = null; randomAuto = false; render();
  }
  function choose(id) {
    if (!running) return;
    if (nxt) { if (allowed(nxt.seg.id, id)) later = id; }        /* decision already locked → after next */
    else if (allowed(cur.seg.id, id)) { queued = id; randomAuto = false; }
    render();
  }
  function logSeam(a, b) {
    var err = (b.start - a.end) * 1000, pre = (b.start - b.audioStart) * 1000;
    var how = (b.seg.loop && a.seg.id === b.seg.id) ? '(loop)' : randomAuto ? '(random)' : '(queued)';
    var line = a.seg.id + ' → ' + b.seg.id + '  ' + how + '   seam ' + err.toFixed(3) + ' ms' + (pre > 0 ? '   pick-up ' + pre.toFixed(0) + ' ms overlapped' : '');
    var span = document.createElement('span'); span.className = Math.abs(err) < 1 ? 'gap' : 'bad'; span.textContent = line + '\n';
    histEl.appendChild(span); histEl.scrollTop = histEl.scrollHeight;
  }
  function logLoad(list) {
    /* decoded length vs. the bar maths from the file name — a quick check that the mp3 decoded gaplessly */
    var lines = list.map(function (s) { var b = buffers[s.id], lg = logicalSec(s), bars = lg / barSec(s.bpmOut); return s.id + '  ' + b.duration.toFixed(3) + 's  logical ' + lg.toFixed(3) + 's ≈ ' + bars.toFixed(2) + ' bars@' + s.bpmOut; });
    var span = document.createElement('span'); span.className = 'dim'; span.textContent = lines.join('\n') + '\n'; histEl.appendChild(span);
  }

  /* ------------------------------------------------------------ UI */
  var startBtn = document.getElementById('start'), segsEl = document.getElementById('segs'), nowEl = document.getElementById('now'),
      progEl = document.getElementById('prog'), nextEl = document.getElementById('next'), histEl = document.getElementById('hist'),
      optExclude = document.getElementById('optExclude'), optXfade = document.getElementById('optXfade'), optRules = document.getElementById('optRules'), optPre = document.getElementById('optPre'), dmark = document.getElementById('dmark');
  var segBtns = {}, randomBtn, groupEls = {};

  function buildButtons() {
    segsEl.innerHTML = ''; segBtns = {}; groupEls = {};
    GROUPS.forEach(function (g) {
      var row = document.createElement('div'); row.className = 'grp'; row.style.setProperty('--c', g.color);
      var head = document.createElement('div'); head.className = 'ghead'; head.innerHTML = '<b></b><i></i>';
      var list = document.createElement('div'); list.className = 'gsegs';
      row.appendChild(head); row.appendChild(list); segsEl.appendChild(row); groupEls[g.id] = { row: row, head: head, list: list };
      SEG.filter(function (s) { return s.group === g.id; }).forEach(function (s) {
        var b = document.createElement('button'); b.className = 'seg'; b.style.setProperty('--c', g.color);
        b.innerHTML = '<span class="nm"></span><span class="meta"></span><span class="st"></span>';
        b.addEventListener('click', function () { choose(s.id); });
        list.appendChild(b); segBtns[s.id] = b;
      });
    });
    var rrow = document.createElement('div'); rrow.className = 'grp rnd';
    randomBtn = document.createElement('button'); randomBtn.className = 'seg random'; randomBtn.innerHTML = '<span class="nm"></span><span class="meta"></span><span class="st"></span>';
    randomBtn.addEventListener('click', function () { queued = null; later = null; randomAuto = false; render(); });
    rrow.appendChild(randomBtn); segsEl.appendChild(rrow);
  }
  function tempoText(seg) { return seg.bpmIn === seg.bpmOut ? seg.bpmIn + ' BPM' : seg.bpmIn + '→' + seg.bpmOut + ' BPM'; }
  function render() {
    document.documentElement.lang = lang === 'zh' ? 'zh-Hant' : 'en';
    document.querySelectorAll('[data-i18n]').forEach(function (el) { var k = el.getAttribute('data-i18n'); el.textContent = k === 'start' ? (running ? T('stop') : T('start')) : T(k); });
    document.querySelectorAll('[data-lang]').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-lang') === lang); });
    GROUPS.forEach(function (g) {
      var ge = groupEls[g.id], segs = SEG.filter(function (s) { return s.group === g.id; });
      ge.head.querySelector('b').textContent = g.name[lang];
      ge.head.querySelector('i').textContent = segs.length ? tempoText(segs[0]) : '';
      ge.row.classList.toggle('active', !!cur && cur.seg.group === g.id);
    });
    SEG.forEach(function (s) {
      var b = segBtns[s.id], st = '';
      b.querySelector('.nm').textContent = labelOf(s);
      b.querySelector('.meta').textContent = (s.loop ? 'loop' : T('pick') + ' ' + s.preBars + ' · ' + T('tail') + ' ' + s.tailBars) + (s.info && s.info !== 'loop' ? ' · ' + s.info : '');
      var isCur = !!cur && cur.seg.id === s.id;
      b.classList.toggle('playing', isCur);
      var isQ = (nxt && nxt.seg.id === s.id) || (!nxt && queued === s.id);
      b.classList.toggle('queued', (!!isQ && !(isCur && s.loop)) || later === s.id);
      var ok = !running || allowed((nxt || cur).seg.id, s.id);
      b.disabled = !running || !ok;
      if (isCur) st = (s.loop && (!nxt || nxt.seg.id === s.id) && !queued) ? T('st_loop') : T('st_playing');
      else if (later === s.id) st = T('st_next_next');
      else if (isQ) st = T('st_queued');
      else if (running && !ok) st = T('st_blocked');
      b.querySelector('.st').textContent = st;
    });
    randomBtn.querySelector('.nm').textContent = T('random');
    randomBtn.classList.toggle('auto', randomAuto && running);
    randomBtn.querySelector('.st').textContent = randomAuto && running ? T('random_auto') : '';
    randomBtn.disabled = !running;
    nowEl.textContent = cur ? cur.seg.id : '—'; nowEl.style.color = cur ? colorOf(cur.seg) : '';
    progEl.style.setProperty('--c', cur ? colorOf(cur.seg) : '');
    if (!running) nextEl.innerHTML = '';
    else if (nxt) nextEl.innerHTML = (nxt.seg.loop && nxt.seg.id === cur.seg.id ? T('next_loop') : randomAuto ? T('next_auto') : T('next_locked')).replace('{n}', nxt.seg.id);
    else if (queued) nextEl.innerHTML = T('next_queued').replace('{n}', queued);
    else if (cur.seg.loop) nextEl.innerHTML = T('next_loop').replace('{n}', cur.seg.id);
    else nextEl.innerHTML = T('next_none').replace('{s}', Math.max(0, cur.end - decisionLead() - ctx.currentTime).toFixed(1));
  }
  function renderProgress() {
    if (!cur) { progEl.firstElementChild.style.width = '0'; return; }
    var f = Math.min(1, Math.max(0, (ctx.currentTime - cur.start) / (cur.end - cur.start)));
    progEl.firstElementChild.style.width = (f * 100) + '%';
    dmark.style.left = (Math.max(0, 1 - decisionLead() / (cur.end - cur.start)) * 100) + '%';
    if (!nxt && !queued && !cur.seg.loop) nextEl.innerHTML = T('next_none').replace('{s}', Math.max(0, cur.end - decisionLead() - ctx.currentTime).toFixed(1));
  }

  /* spectrum display (same style as the OS player) + segment colour */
  var viz = document.getElementById('viz'), vg = viz.getContext('2d');
  (function drawViz() {
    requestAnimationFrame(drawViz);
    if (viz.width !== viz.clientWidth) viz.width = viz.clientWidth;
    var W = viz.width, H = viz.height; vg.clearRect(0, 0, W, H);
    if (!analyser || !running) { vg.strokeStyle = 'rgba(224,176,74,.25)'; vg.beginPath(); vg.moveTo(0, H / 2); vg.lineTo(W, H / 2); vg.stroke(); return; }
    var data = new Uint8Array(analyser.frequencyBinCount); analyser.getByteFrequencyData(data);
    var n = 64, bw = W / n, col = cur ? colorOf(cur.seg) : '#e0b04a';
    vg.fillStyle = col + '99';
    /* log-frequency bars from bin 1 with sub-bin interpolation (same as the OS shell): no flat plateau in the bass */
    var lo0 = 1, hi0 = data.length - 1, ratio = hi0 / lo0;
    for (var i = 0; i < n; i++) {
      var a = lo0 * Math.pow(ratio, i / n), b2 = lo0 * Math.pow(ratio, (i + 1) / n), m;
      if (b2 - a < 1) { var k = Math.floor(a), f = a - k; m = data[k] * (1 - f) + (data[Math.min(k + 1, hi0)] || 0) * f; }
      else { m = 0; for (var b = Math.floor(a); b < b2 && b <= hi0; b++) m = Math.max(m, data[b]); }
      var h = Math.pow(m / 255, 0.7) * H; vg.fillRect(i * bw, H - h, bw - 1, h);
    }
    var td = new Uint8Array(analyser.fftSize); analyser.getByteTimeDomainData(td);
    var peak = 0.02; for (var q = 0; q < td.length; q++) peak = Math.max(peak, Math.abs((td[q] - 128) / 128));
    vg.strokeStyle = 'rgba(255,255,255,.7)'; vg.lineWidth = 1.5; vg.beginPath();
    for (var k = 0; k < td.length; k++) { var x = k / (td.length - 1) * W, y = H / 2 + (td[k] - 128) / 128 / peak * H * 0.35; k ? vg.lineTo(x, y) : vg.moveTo(x, y); }
    vg.stroke();
  })();

  startBtn.addEventListener('click', function () { running ? stop() : start(); });
  document.querySelectorAll('[data-lang]').forEach(function (b) { b.addEventListener('click', function () { lang = b.getAttribute('data-lang'); try { localStorage.setItem('lang', lang); } catch (e) {} render(); }); });
  [optExclude, optXfade, optRules, optPre].forEach(function (o) { o.addEventListener('change', render); });
  /* keyboard: a group letter queues that group's first version; the same letter again cycles through its versions */
  document.addEventListener('keydown', function (e) {
    var g = e.key.toUpperCase(); if (!byGroup[g] || !running) return;
    var segs = SEG.filter(function (s) { return s.group === g; }), i = 0;
    var curPick = nxt ? later : queued;
    if (curPick && byId[curPick].group === g) i = (segs.findIndex(function (s) { return s.id === curPick; }) + 1) % segs.length;
    choose(segs[i].id);
  });

  fetch('segments.json').then(function (r) { return r.json(); }).then(function (cfg) {
    CFG = cfg; SEG = cfg.segments; GROUPS = cfg.groups || []; SEG.forEach(function (s) { byId[s.id] = s; }); GROUPS.forEach(function (g) { byGroup[g.id] = g; });
    optExclude.checked = !!cfg.excludeLast; buildButtons(); render();
  });
})();
