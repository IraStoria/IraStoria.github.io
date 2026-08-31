/* © 2026 IraStoria (https://irastoria.github.io/). All rights reserved. See /LICENSE. */
/* Interactive Section Player. Written from scratch (ADR-004), Web Audio only.
   Every section is a decoded AudioBuffer; playback = sample-accurate AudioBufferSourceNode.start(t).
   File model (from the file name Theme_Section_pickupBars_tailBars_Info):
     [pick-up bars @ bpmIn][logical section][tail bars @ bpmOut]
   The entry cue of the next section lands exactly on the logical end of the current one; the pick-up
   starts earlier and overlaps the previous tail. Logical length = decoded duration − pick-up − tail.
   A theme = fixed intro + free middle sections (grouped by letter, several versions each) + fixed outro. */
(function () {
  'use strict';
  /* stage mode (?stage=1): the OS desktop hosts this page in a chromeless layer — the page strips itself down to the control surface (demo.css body.stage) */
  var STAGE_HOST = /[?&]stage=1/.test(location.search);
  if (STAGE_HOST) document.body.classList.add('stage');

  /* ------------------------------------------------------------ i18n */
  var I18N = {
    zh: {
      title: '互動式段落播放器',
      lead: '按「開始」後從 A 播放。播放中點任一段落，會在目前段落結束的瞬間無縫接上；若什麼都不點，「順序」會照段落自然順序接下去（也可改成「隨機」）。同字母 1→2 是正常順序；E1→E2 是前後段的中繼；點 I 收尾。',
      start: '▶ 開始', stop: '■ 停止', now: '播放中：', random: '隨機', random_auto: '自動接手中', seq: '順序', seq_auto: '依序接手中', loading: '載入音檔…',
      zone_pre: '前段', zone_gate: '中繼', zone_post: '後段',
      st_playing: '播放中', st_queued: '已排隊', st_next_next: '排到下下段', st_blocked: '不可接', st_loop: '循環中',
      next_none: '下一段：尚未決定（{s} 秒後隨機接手）', next_none_seq: '下一段：尚未決定（{s} 秒後依序接手）', next_queued: '下一段：<em>{n}</em>（你的選擇）', next_auto: '下一段：<em>{n}</em>（隨機自動接手）', next_seq: '下一段：<em>{n}</em>（依序自動接手）', next_locked: '已鎖定 → <em>{n}</em>', next_loop: '下一段：<em>{n}</em>（自我循環，點其他段可離開）', next_end: 'I 播完即結束',
      opt_exclude: '隨機不重複上一段', opt_rules: '啟用接續規則',
      how: '這個播放器怎麼運作',
      e1: '無縫：每一段都是預先解碼的 AudioBuffer，下一段以「上一段結束的取樣點」起播（下方紀錄列出實際接縫誤差）。',
      e2: '佇列：播放中點段落＝排到下一段；在決策鎖定之後才點，會自動排到下下段。',
      e3: '自動接手：到決策點若沒有選擇，由「隨機」或「順序」接手（順序＝照段落自然順序一路到 I）。I loop 沒被指定下一段時會自我循環。',
      e4: '接續規則：每段允許接哪些段由設定檔定義；不允許的按鈕會變灰。',
      e5: '每段自帶前導與尾音：下一段的前導與上一段的尾音重疊，接縫仍落在小節線上；決策點（進度條白線）依候選段的前導自動提前。',
      note: 'Full on V6 - by Shiou Hsu'
    },
    en: {
      title: 'Interactive Section Player',
      lead: 'Press Start and A plays. Pick any section while playing and it joins the instant the current one ends; pick nothing and In order carries on through the natural sequence (or switch to Random). Within a letter, 1→2 is the natural order; E1→E2 bridges the two halves; pick I to finish.',
      start: '▶ Start', stop: '■ Stop', now: 'Now playing:', random: 'Random', random_auto: 'auto-picking', seq: 'In order', seq_auto: 'auto, in order', loading: 'Loading audio…',
      zone_pre: 'first half', zone_gate: 'bridge', zone_post: 'second half',
      st_playing: 'playing', st_queued: 'queued', st_next_next: 'queued after next', st_blocked: 'not allowed', st_loop: 'looping',
      next_none: 'Next: undecided (random takes over in {s}s)', next_none_seq: 'Next: undecided (in-order takes over in {s}s)', next_queued: 'Next: <em>{n}</em> (your pick)', next_auto: 'Next: <em>{n}</em> (random, automatic)', next_seq: 'Next: <em>{n}</em> (in order, automatic)', next_locked: 'Locked → <em>{n}</em>', next_loop: 'Next: <em>{n}</em> (self-loop; pick another section to leave)', next_end: 'Ends after I',
      opt_exclude: 'Random avoids repeating last', opt_rules: 'Enable transition rules',
      how: 'How it works',
      e1: 'Seamless: every section is a decoded AudioBuffer; the next starts at the exact sample where the previous ends (the log shows measured seam error).',
      e2: 'Queue: tapping a section while playing queues it next; tapping after the decision lock queues it after the next one.',
      e3: 'Auto hand-over: with nothing queued at the decision point, Random or In-order takes over (in order = the natural section order through to I). I loop repeats itself when nothing else is queued.',
      e4: 'Rules: the config restricts which sections may follow which; disallowed buttons grey out.',
      e5: 'Every section carries its own lead-in and tail: the next lead-in overlaps the previous tail while the seam stays on the barline; the decision point (white marker) moves earlier by the candidates\' lead-ins.',
      note: 'Full on V6 - by Shiou Hsu'
    }
  };
  var lang = 'zh';
  try { lang = localStorage.getItem('lang') || (navigator.language.toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en'); } catch (e) {}
  if (!I18N[lang]) lang = 'zh';
  var T = function (k) { return I18N[lang][k]; };

  /* ------------------------------------------------------------ data */
  var CFG = null, THEMES = [], TH = null;          /* TH = active theme */
  var SEG = [], byId = {}, GROUPS = [], byGroup = {}, INTRO = null, OUTRO = null;
  var ctx = null, master = null, buffers = {}, analyser = null;
  var LOOKAHEAD = 0.25;  /* decision lock window (s) */
  var TICK = 25;

  /* scheduler state */
  var running = false, timer = null, paused = false, muted = false, mgain = null, byeTimer = null, loadProg = null;   /* loadProg {d,t}: fetch+decode progress while the stage loads (the OS shows it at the line, like the ADE stage) */   /* paused = ctx.suspend(): the clock freezes, so the whole schedule freezes with it; muted drives mgain (after the analyser — the bars keep moving); byeTimer: the outro's panel collapse */
  var stageLead = 0, stageT0 = null;   /* stage mode: four 4/4 beats at the theme's tempo before the first entry — load time, like the ADE stage's count-in */
  var cur = null;        /* { seg, start, end, src, gain } */
  var nxt = null;        /* scheduled next: same shape */
  var queued = null;     /* user's choice for the next decision (segment id) */
  var later = null;      /* user's choice after a lock (applies to the decision after next) */
  var randomAuto = false, lastId = null, history = [], ending = false, loopCount = 0, mate = null;   /* loopCount: consecutive passes of the current loop segment (it leaves only on an even count); mate: the OTHER version of the group just entered — whichever version is picked plays first, its mate follows before anything else */
  function markPair(seg) {   /* entering a section: does it complete a pair, or open one? */
    if (mate && seg.id === mate.id) { mate = null; return; }
    if (!seg.loop && !isBookend(seg)) { var vs = groupVersions(seg); mate = vs.length === 2 ? vs[vs.indexOf(seg) ^ 1] : null; } else mate = null;
  }
  var autoMode = 'seq';   /* 'random' | 'seq' — what takes over at the decision point when nothing is queued */
  var trackListeners = [];   /* fired on every section change with the outgoing progress fraction (the OS shell sweeps its progress bar from there) */
  function fireTrack(fromFrac) { trackListeners.forEach(function (fn) { try { fn(fromFrac); } catch (e) {} }); }
  /* "now playing" title shared with the OS shell: V1.B2 Full on V6 - by Shiou Hsu (V = theme version, XX = section) */
  /* two parts: tag = 'V1 - B2' (flips on every section change in the OS caption), rest = 'Full on V6 - by Shiou Hsu' */
  function titleParts(seg) { var song = (CFG && CFG.song) || {}; return { tag: (TH.version || 'V1') + ' - ' + seg.id.replace('_loop', ' loop'), rest: (song.title || '') + (song.artist ? ' - by ' + song.artist : '') }; }
  function trackTitle(seg) { var p = titleParts(seg); return p.tag + ' | ' + p.rest; }
  /* public API for the host page (same-origin iframe): progress / spectrum / title of the current section */
  window.sectionPlayer = {
    state: function () {
      if (!running || !cur || !ctx) return { active: false, started: false, playing: false, title: '', pos: 0, dur: 0, frac: 0, muted: muted, loading: loadProg };
      var pos = Math.max(0, Math.min(cur.end - cur.start, ctx.currentTime - cur.start)), dur = cur.end - cur.start;
      var mark = cur.seg === OUTRO ? null : Math.max(0, 1 - decisionLead() / dur);   /* the decision-lock point as a fraction — the OS line draws it as a tick */
      var o = { active: true, started: true, playing: !paused, title: trackTitle(cur.seg), parts: titleParts(cur.seg), id: cur.seg.id, pos: pos, dur: dur, frac: dur ? pos / dur : 0, mark: mark, muted: muted };
      if (STAGE_HOST) { o.color = segColor(cur.seg); o.frozen = paused; }   /* stage: the OS line/bars wear the part's colour; frozen lets the bars decay while the suspended analyser holds its last frame */
      return o;
    },
    toggle: function () {   /* the OS transport's play/pause: suspending the context freezes the clock, so the whole schedule pauses in place */
      if (!running || !ctx) return;
      if (paused) { paused = false; ctx.resume(); } else { paused = true; ctx.suspend(); }
      render();
    },
    toggleMute: function () {   /* the OS transport's speaker: silences the output stage (after the analyser, so the visuals keep breathing) */
      muted = !muted;
      if (mgain && ctx) mgain.gain.setTargetAtTime(muted ? 0.0001 : 1, ctx.currentTime, 0.03);
    },
    analyser: function () { return running ? analyser : null; },
    onTrack: function (fn) { trackListeners.push(fn); }
  };

  function useTheme(th) {
    TH = th; INTRO = th.intro || null; OUTRO = th.outro || null;
    SEG = th.segments; GROUPS = th.groups || []; byId = {}; byGroup = {};
    SEG.forEach(function (s) { byId[s.id] = s; }); GROUPS.forEach(function (g) { byGroup[g.id] = g; });
    if (INTRO) byId[INTRO.id] = INTRO; if (OUTRO) byId[OUTRO.id] = OUTRO;
  }
  function isBookend(seg) { return seg === INTRO || seg === OUTRO; }
  function bufKey(seg) { return TH.id + ':' + seg.id; }
  function allSegs() { var a = SEG.slice(); if (INTRO) a.unshift(INTRO); if (OUTRO) a.push(OUTRO); return a; }

  /* ------------------------------------------------------------ timing model (per segment) */
  function barSec(bpm) { return 60 / bpm * CFG.beatsPerBar; }
  function preSec(seg) { return (seg.preBars || 0) * barSec(seg.bpmIn); }      /* pick-up runs at the incoming tempo */
  function postSec(seg) { return (seg.tailBars || 0) * barSec(seg.bpmOut); }   /* tail rings at the outgoing tempo */
  function logicalSec(seg) { var b = buffers[bufKey(seg)]; return b ? Math.max(0.1, (seg.durationSec || b.duration) - preSec(seg) - postSec(seg)) : 0; }   /* durationSec: true length for files whose mp3 has no gapless tag */
  function colorOf(seg) { return (byGroup[seg.group] || {}).color || '#e0b04a'; }
  function nameOf(seg) { return seg.id.replace('_', ' '); }
  function groupVersions(seg) { return SEG.filter(function (s) { return s.group === seg.group; }); }
  function verIdx(seg) { return isBookend(seg) ? 0 : groupVersions(seg).indexOf(seg); }
  function hexHsl(hex) {   /* -> [h 0-360, s 0-1, l 0-1] */
    var v = parseInt(hex.slice(1), 16), r = ((v >> 16) & 255) / 255, g = ((v >> 8) & 255) / 255, b = (v & 255) / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn, h = 0, s = 0;
    if (d > 0) { s = d / (1 - Math.abs(2 * l - 1)); h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h = (h * 60 + 360) % 360; }
    return [h, s, l];
  }
  function hslHex(h, s, l) {
    var c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = l - c / 2, r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; } else if (h < 120) { r = x; g = c; } else if (h < 180) { g = c; b = x; } else if (h < 240) { g = x; b = c; } else if (h < 300) { r = x; b = c; } else { r = c; b = x; }
    var q = function (u) { return Math.round((u + m) * 255); };
    return '#' + ((1 << 24) + (q(r) << 16) + (q(g) << 8) + q(b)).toString(16).slice(1);
  }
  function deep(hex) {   /* colour DEPTH (the 深藍紫 concept): richer and darker, with only a SMALL lean toward violet — capped at 12° so the pair stays in one colour family (amber -> deep amber, never red) */
    var t = hexHsl(hex), h = t[0], d = ((270 - t[0] + 540) % 360) - 180, shift = Math.max(-12, Math.min(12, d * 0.25));
    return hslHex((h + shift + 360) % 360, Math.min(1, t[1] * 1.12), Math.max(0, t[2] * 0.6));
  }
  function segColor(seg) { if (isBookend(seg)) return '#e8e8e4'; var c = colorOf(seg); return verIdx(seg) > 0 ? deep(c) : c; }   /* intro/outro = white light (a touch grey — pure #fff read too bright); version 1 = the group colour (淺), version 2 = its deep violet-leaning shade (深) */

  /* ------------------------------------------------------------ audio loading */
  /* decode with a concurrency cap: decoding twenty mp3s at once stalls mobile Safari (and peaks memory); Safari < 15 also only knows the callback form */
  /* loading must never stutter the HOST: this iframe shares the site's main thread, so the decode completions and their
     buffer allocations are paced — one decode at a time with a breather between them. Slower to finish, invisible to the frame rate. */
  var DECODE_AT_ONCE = 1, DECODE_GAP_MS = 60, decodeQueue = [], decoding = 0;
  function decode(ab) {
    return new Promise(function (res, rej) {
      decodeQueue.push(function () {
        decoding++;
        var done = function (fn) { return function (v) { decoding--; schedulePump(); fn(v); }; };
        try {
          var p = ctx.decodeAudioData(ab, done(res), done(function (e) { rej(e || new Error('decode failed')); }));
          if (p && p.then) p.then(function () {}, function () {});   /* promise form resolves through the callbacks above; swallow the duplicate rejection */
        } catch (e) { done(rej)(e); }
      });
      pump();
    });
  }
  function pump() { while (decoding < DECODE_AT_ONCE && decodeQueue.length) decodeQueue.shift()(); }
  function schedulePump() {   /* the next decode runs only in the IDLE time after the host's animation frame — animations always come first; a hidden tab (no frames, and 1 s timer throttling) decodes flat out */
    if (document.hidden) pump();
    else if (window.requestIdleCallback) requestIdleCallback(function () { pump(); }, { timeout: 400 });
    else setTimeout(pump, DECODE_GAP_MS);
  }
  /* fetches are capped too: twenty simultaneous responses landing at once (plus their decodes) is the first-launch stutter */
  var FETCH_AT_ONCE = 2, fetchQueue = [], fetching = 0;
  function fpump() { while (fetching < FETCH_AT_ONCE && fetchQueue.length) fetchQueue.shift()(); }
  function qFetch(url) {
    return new Promise(function (res, rej) {
      fetchQueue.push(function () {
        fetching++;
        var done = function (fn) { return function (v) { fetching--; fpump(); fn(v); }; };
        fetch(url).then(function (r) { if (!r.ok) throw new Error(url + ' ' + r.status); return r.arrayBuffer(); }).then(done(res), done(rej));
      });
      fpump();
    });
  }
  function loadBuffer(seg, onFetched) {
    return qFetch(seg.file).then(function (ab) { if (onFetched) onFetched(); return decode(ab); });
  }

  /* ------------------------------------------------------------ scheduler */
  function schedule(seg, entry) {
    var src = ctx.createBufferSource(), g = ctx.createGain();
    src.buffer = buffers[bufKey(seg)]; src.connect(g); g.connect(master);
    var pre = preSec(seg), xf = (cur && pre <= 0 && !postSec(cur.seg)) ? CFG.crossfadeMs / 1000 : 0;   /* tiny equal-power seam only when nothing overlaps */
    if (xf > 0) { g.gain.setValueAtTime(0, entry); g.gain.linearRampToValueAtTime(1, entry + xf); }
    src.start(entry - pre, seg.trimStart || 0);   /* trimStart skips an mp3 encoder delay when the file carries no gapless tag */
    return { seg: seg, start: entry, end: entry + logicalSec(seg), audioStart: entry - pre, src: src, gain: g };
  }
  function fadeOut(item) {
    var post = postSec(item.seg), xf = (!post && nxt && preSec(nxt.seg) <= 0) ? CFG.crossfadeMs / 1000 : 0;
    if (post > 0) { item.src.stop(item.end + post); }            /* let the tail ring out over the next section */
    else if (xf > 0) { item.gain.gain.setValueAtTime(1, item.end); item.gain.gain.linearRampToValueAtTime(0, item.end + xf); item.src.stop(item.end + xf); }
    else item.src.stop(item.end);
  }
  function candidates(fromId) { return allSegs().filter(function (s) { return s !== INTRO && allowed(fromId, s.id); }); }
  function decisionLead() {
    /* how far before cur.end the next section must be decided: global lead (at the outgoing tempo) OR the largest pick-up among candidates */
    var lead = (CFG.decisionLeadBeats || 0) * (60 / cur.seg.bpmOut);
    candidates(cur.seg.id).forEach(function (s) { lead = Math.max(lead, preSec(s)); });
    return lead + LOOKAHEAD;
  }
  function allowed(fromId, toId) {
    var to = byId[toId], from = byId[fromId];
    if (!to || !from || to === INTRO) return false;
    if (from === OUTRO) return false;
    if (!optRules.checked) return true;
    var A = TH.allow || {}, a = A[fromId] || A[from.group];   /* per-segment first, then per-group */
    if (!a) return true;
    return a.indexOf(toId) >= 0 || a.indexOf(to.group) >= 0 || (to === OUTRO && a.indexOf('OUTRO') >= 0);
  }
  /* natural order: the config order of the middle sections (I_loop skipped), then I.
     The CURRENT group is excluded: its versions were consumed as a pair (the mate rule), so in-order moves on to the
     next group's head — entering at B2 runs B2 -> B1 -> C1, never the B2/B1 ping-pong. */
  function pickNext(fromId) {
    var from = byId[fromId];
    var order = SEG.filter(function (s) { return !s.loop; }); if (OUTRO) order.push(OUTRO);
    var i = order.indexOf(from), pool = order.slice(i + 1).filter(function (s) { return s.group !== from.group && allowed(fromId, s.id); });
    if (from.loop) pool = order.filter(function (s) { return s.group !== from.group && allowed(fromId, s.id); });
    return pool.length ? pool[0] : pickRandom(fromId);
  }
  function pickRandom(fromId) {
    var from = byId[fromId];
    if (from.loop) return from;   /* outro loop: keeps looping itself until the user picks something */
    var pool = SEG.filter(function (s) { return s.group !== from.group && allowed(fromId, s.id) && !(optExclude.checked && s.id === lastId && SEG.length > 1) && s.id !== fromId; });   /* the group just completed as a pair — random moves to a different group */
    if (!pool.length) pool = SEG.filter(function (s) { return allowed(fromId, s.id); });
    if (!pool.length) pool = SEG;
    return pool[Math.floor(Math.random() * pool.length)];
  }
  function decide() {
    /* a group plays out whole, in the order it was entered: whichever version was picked plays first, its mate ALWAYS follows
       (any queued pick waits for the decision after); the outro loop leaves only after an even number of passes */
    var forced = null;
    if (cur.seg.loop && loopCount % 2 === 1) forced = cur.seg;
    else if (mate) forced = mate;   /* the pair rule is the user's own design — it outranks the allow list (schedule() still honours pre-entry/tail timing) */
    if (forced) {
      randomAuto = false;
      nxt = schedule(forced, cur.end);
      fadeOut(cur); logSeam(cur, nxt); render(); return;   /* `queued` is left untouched — it applies after the pair completes */
    }
    var choice = null;
    if (queued && allowed(cur.seg.id, queued)) { choice = byId[queued]; randomAuto = false; }
    else { choice = autoMode === 'seq' ? pickNext(cur.seg.id) : pickRandom(cur.seg.id); randomAuto = !(choice.loop && autoMode === 'random'); }
    queued = null;
    nxt = schedule(choice, cur.end);
    fadeOut(cur);
    logSeam(cur, nxt);
    render();
  }
  function tick() {
    var now = ctx.currentTime;
    if (cur.seg === OUTRO) {                /* terminal: let it finish, then stop */
      if (now >= cur.end + postSec(cur.seg) + 0.05) { stop(); return; }
      renderProgress(); return;
    }
    if (!nxt && now >= cur.end - decisionLead()) decide();
    if (nxt && now >= cur.end) {           /* hand-over */
      lastId = cur.seg.id; cur = nxt; nxt = null; fireTrack(1);
      loopCount = cur.seg.loop ? loopCount + 1 : 0;
      markPair(cur.seg);
      if (later) { queued = later; later = null; }
      history.push(cur.seg.id);
      if (STAGE_HOST && cur.seg === OUTRO && !byeTimer) byeTimer = setTimeout(function () { document.body.classList.add('bye'); }, Math.max(0, (cur.end - ctx.currentTime - 2 * barSec(OUTRO.bpmOut)) * 1000));   /* TWO bars before the outro's logical end: the surface compresses and the OS line retracts — the desktop player runs straight back out under the closing bars */
      render();
    }
    renderProgress();
  }
  function start() {
    if (!ctx) { ctx = new (window.AudioContext || window.webkitAudioContext)(); master = ctx.createGain(); master.gain.value = 0.9; analyser = ctx.createAnalyser(); analyser.fftSize = 2048; analyser.minDecibels = -96; analyser.maxDecibels = 6; if (STAGE_HOST) analyser.smoothingTimeConstant = 0.55; master.connect(analyser); mgain = ctx.createGain(); mgain.gain.value = muted ? 0.0001 : 1; analyser.connect(mgain); mgain.connect(ctx.destination); }   /* mgain: the mute stage sits after the analyser so the bars keep moving; a mute requested before the context existed is honoured here. Stage: lower smoothing — the bass/bk hits snap instead of blurring */
    if (ctx.state === 'suspended') ctx.resume();
    paused = false;
    if (stageLead && stageT0 === null) stageT0 = ctx.currentTime;   /* the count-in starts NOW; loading runs inside it */
    var pending = allSegs().filter(function (s) { return !buffers[bufKey(s)]; }), done = 0, fetched = 0;
    var prog = function () { startBtn.textContent = T('loading') + ' ' + fetched + '↓ ' + done + '/' + pending.length; loadProg = pending.length ? { d: fetched + done, t: pending.length * 2 } : null; };   /* downloaded↓ decoded/total — so a stall is visible as one or the other; loadProg counts both phases for the OS loading readout */
    startBtn.disabled = true; if (pending.length) prog();
    Promise.all(pending.map(function (s) { return loadBuffer(s, function () { fetched++; prog(); }).then(function (b) { buffers[bufKey(s)] = b; done++; prog(); }); })).then(function () {
      if (pending.length && /[?&]debug/.test(location.search)) logLoad(pending);   /* decode check only with ?debug */
      loadProg = null;
      running = true; queued = later = null; randomAuto = false; lastId = null; history = []; loopCount = 0; mate = null;
      var first = INTRO || SEG[0];
      var entry = ctx.currentTime + 0.05 + preSec(first);   /* first entry sits after its own pick-up: start times can't be negative */
      if (stageLead && stageT0 !== null) { entry = Math.max(entry, stageT0 + stageLead + preSec(first)); stageT0 = null; }   /* stage: the music enters on the count-in's downbeat (or as soon as loading allows, whichever is later) */
      cur = schedule(first, entry); nxt = null;
      markPair(first);   /* starting inside a two-version group opens its pair too */
      history.push(first.id); fireTrack(0);
      timer = setInterval(tick, TICK); startBtn.disabled = false; render();
    }).catch(function (err) { running = false; loadProg = null; clearInterval(timer); cur = nxt = null; startBtn.disabled = false; var s = document.createElement('span'); s.className = 'bad'; s.textContent = 'start error: ' + (err && err.stack || err) + '\n'; histEl.appendChild(s); render(); });
  }
  function stop() {
    running = false; clearInterval(timer);
    if (byeTimer) { clearTimeout(byeTimer); byeTimer = null; }
    if (paused) { paused = false; try { ctx.resume(); } catch (e) {} }   /* never leave the context suspended: the fade-out below needs a running clock */
    [cur, nxt].forEach(function (it) { if (it) { try { it.gain.gain.cancelScheduledValues(0); it.gain.gain.setValueAtTime(it.gain.gain.value, ctx.currentTime); it.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.1); it.src.stop(ctx.currentTime + 0.12); } catch (e) {} } });
    cur = nxt = null; queued = later = null; randomAuto = false; render(); fireTrack(0);
  }
  function choose(id) {
    if (!running) return;
    if (nxt) { if (allowed(nxt.seg.id, id)) later = id; }        /* decision already locked → after next */
    else if (allowed(cur.seg.id, id)) { queued = id; randomAuto = false; }
    render();
  }
  function logSeam(a, b) {
    var err = (b.start - a.end) * 1000, pre = (b.start - b.audioStart) * 1000;
    var how = (b.seg.loop && a.seg.id === b.seg.id) ? '(loop)' : randomAuto ? (autoMode === 'seq' ? '(in order)' : '(random)') : '(queued)';
    var line = a.seg.id + ' → ' + b.seg.id + '  ' + how + '   seam ' + err.toFixed(3) + ' ms' + (pre > 0 ? '   lead-in ' + pre.toFixed(0) + ' ms overlapped' : '');
    var span = document.createElement('span'); span.className = Math.abs(err) < 1 ? 'gap' : 'bad'; span.textContent = line + '\n';
    histEl.appendChild(span); histEl.scrollTop = histEl.scrollHeight;
  }
  function logLoad(list) {
    /* decoded length vs. the bar maths from the file name — a quick check that the mp3 decoded gaplessly */
    var lines = list.map(function (s) { var b = buffers[bufKey(s)], lg = logicalSec(s), bars = lg / barSec(s.bpmOut); return s.id + '  ' + b.duration.toFixed(3) + 's' + (s.durationSec ? ' (true ' + s.durationSec.toFixed(3) + 's)' : '') + '  logical ' + lg.toFixed(3) + 's ≈ ' + bars.toFixed(2) + ' bars'; });
    var span = document.createElement('span'); span.className = 'dim'; span.textContent = lines.join('\n') + '\n'; histEl.appendChild(span);
  }

  /* ------------------------------------------------------------ UI */
  var startBtn = document.getElementById('start'), segsEl = document.getElementById('segs'), nowEl = document.getElementById('now'),
      progEl = document.getElementById('prog'), nextEl = document.getElementById('next'), histEl = document.getElementById('hist'),
      optExclude = document.getElementById('optExclude'), optRules = document.getElementById('optRules'), dmark = document.getElementById('dmark'),
      themesEl = document.getElementById('themes');
  var segBtns = {}, randomBtn, seqBtn, outroBtn, colEls = {};

  function buildThemes() {
    themesEl.innerHTML = '';
    THEMES.forEach(function (th) {
      var b = document.createElement('button'); b.className = 'theme' + (th === TH ? ' active' : ''); b.textContent = th.name[lang] || th.id;
      b.addEventListener('click', function () { if (th === TH || running) return; useTheme(th); buildThemes(); buildButtons(); render(); });
      themesEl.appendChild(b);
    });
    themesEl.style.display = THEMES.length > 1 ? '' : 'none';
  }
  /* compact grid: one column per letter (header = letter), versions stacked under it; Outro + Random as the last columns */
  function buildButtons() {
    segsEl.innerHTML = ''; segBtns = {}; colEls = {};
    /* zones: first half {B,C,D} · bridge {E} · second half {F,G,H,I_loop}; within a column, 1 sits above 2 with a ↓ (natural order) */
    var zones = [{ k: 'zone_pre', g: ['B', 'C', 'D'] }, { k: 'zone_gate', g: ['E'] }, { k: 'zone_post', g: ['F', 'G', 'H', 'I_loop'] }];
    zones.forEach(function (z) {
      var band = document.createElement('div'); band.className = 'zone ' + z.k;
      var lab = document.createElement('div'); lab.className = 'zlab'; lab.textContent = T(z.k); band.appendChild(lab);
      var cols = document.createElement('div'); cols.className = 'zcols'; band.appendChild(cols);
      z.g.forEach(function (gid) {
        var g = byGroup[gid]; if (!g) return;
        var col = document.createElement('div'); col.className = 'col'; col.style.setProperty('--c', g.color);
        SEG.filter(function (s) { return s.group === g.id; }).forEach(function (s, i) {
          if (i > 0) { var arrow = document.createElement('i'); arrow.className = 'link'; arrow.textContent = '↓'; col.appendChild(arrow); }
          var b = document.createElement('button'); b.className = 'seg'; b.style.setProperty('--c', i > 0 ? deep(g.color) : g.color);   /* version 2 = the deep violet-leaning shade (matches the OS line) */
          b.innerHTML = '<span class="nm"></span><span class="st"></span>';
          b.addEventListener('click', function () { choose(s.id); });
          col.appendChild(b); segBtns[s.id] = b;
        });
        cols.appendChild(col); colEls[g.id] = col;
      });
      segsEl.appendChild(band);
    });
    var tail = document.createElement('div'); tail.className = 'zone ctrl';
    var tl = document.createElement('div'); tl.className = 'zlab'; tl.textContent = ' '; tail.appendChild(tl);
    if (OUTRO) {
      outroBtn = document.createElement('button'); outroBtn.className = 'seg outro'; outroBtn.innerHTML = '<span class="nm"></span><span class="st"></span>';
      outroBtn.addEventListener('click', function () { choose(OUTRO.id); }); tail.appendChild(outroBtn); segBtns[OUTRO.id] = outroBtn;
    }
    randomBtn = document.createElement('button'); randomBtn.className = 'seg random'; randomBtn.innerHTML = '<span class="nm"></span><span class="st"></span>';
    randomBtn.addEventListener('click', function () { autoMode = 'random'; queued = null; later = null; randomAuto = false; render(); });
    seqBtn = document.createElement('button'); seqBtn.className = 'seg random seq'; seqBtn.innerHTML = '<span class="nm"></span><span class="st"></span>';
    seqBtn.addEventListener('click', function () { autoMode = 'seq'; queued = null; later = null; randomAuto = false; render(); });
    tail.appendChild(randomBtn); tail.appendChild(seqBtn); segsEl.appendChild(tail);
  }
  function render() {
    document.documentElement.lang = lang === 'zh' ? 'zh-Hant' : 'en';
    document.querySelectorAll('[data-i18n]').forEach(function (el) { var k = el.getAttribute('data-i18n'); el.textContent = k === 'start' ? (running ? T('stop') : T('start')) : T(k); });
    document.querySelectorAll('[data-lang]').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-lang') === lang); });
    GROUPS.forEach(function (g) { if (colEls[g.id]) { var inG = !!cur && cur.seg.group === g.id; colEls[g.id].classList.toggle('active', inG); colEls[g.id].classList.toggle('pair', inG && SEG.filter(function (s) { return s.group === g.id; }).length === 2); } });   /* pair rule engaged: a rectangle frames BOTH versions of the group being played (stage styling) */
    var list = SEG.slice(); if (OUTRO) list.push(OUTRO);
    list.forEach(function (s) {
      var b = segBtns[s.id], st = '';
      b.querySelector('.nm').textContent = nameOf(s);
      var isCur = !!cur && cur.seg.id === s.id;
      b.classList.toggle('playing', isCur);
      var isQ = (nxt && nxt.seg.id === s.id) || (!nxt && queued === s.id);
      b.classList.toggle('queued', (!!isQ && !(isCur && s.loop)) || later === s.id);
      var ok = !running || allowed((nxt || cur).seg.id, s.id);
      b.disabled = !running || !ok;
      b.classList.toggle('off', running && !ok && !isCur && later !== s.id);   /* stage mode fades a blocked section off the screen — but never the one that is PLAYING (the outro may not follow itself, yet it must stay lit) or already picked (styled only under body.stage; the window keeps the greyed look) */
      if (isCur) st = (s.loop && (!nxt || nxt.seg.id === s.id) && !queued) ? T('st_loop') : T('st_playing');
      else if (later === s.id) st = T('st_next_next');
      else if (isQ) st = T('st_queued');
      else if (running && !ok) st = T('st_blocked');
      b.querySelector('.st').textContent = st;
    });
    randomBtn.querySelector('.nm').textContent = T('random'); seqBtn.querySelector('.nm').textContent = T('seq');
    randomBtn.classList.toggle('mode', autoMode === 'random'); seqBtn.classList.toggle('mode', autoMode === 'seq');
    randomBtn.classList.toggle('auto', randomAuto && running && autoMode === 'random'); seqBtn.classList.toggle('auto', randomAuto && running && autoMode === 'seq');
    randomBtn.querySelector('.st').textContent = randomAuto && running && autoMode === 'random' ? T('random_auto') : '';
    seqBtn.querySelector('.st').textContent = randomAuto && running && autoMode === 'seq' ? T('seq_auto') : '';
    randomBtn.disabled = seqBtn.disabled = !running || (cur && cur.seg === OUTRO);
    nowEl.textContent = cur ? nameOf(cur.seg) : '—'; nowEl.style.color = cur ? segColor(cur.seg) : '';
    progEl.style.setProperty('--c', cur ? segColor(cur.seg) : '');
    if (!running) nextEl.innerHTML = '';
    else if (cur.seg === OUTRO) nextEl.innerHTML = T('next_end');
    else if (nxt) nextEl.innerHTML = (nxt.seg.loop && nxt.seg.id === cur.seg.id ? T('next_loop') : randomAuto ? (autoMode === 'seq' ? T('next_seq') : T('next_auto')) : T('next_locked')).replace('{n}', nameOf(nxt.seg));
    else if (queued) nextEl.innerHTML = T('next_queued').replace('{n}', nameOf(byId[queued]));
    else if (cur.seg.loop) nextEl.innerHTML = T('next_loop').replace('{n}', nameOf(cur.seg));
    else nextEl.innerHTML = STAGE_HOST ? '' : T(autoMode === 'seq' ? 'next_none_seq' : 'next_none').replace('{s}', Math.max(0, cur.end - decisionLead() - ctx.currentTime).toFixed(1));   /* stage: no "undecided" countdown — the tick on the OS line says it */
  }
  function renderProgress() {
    if (!cur) { progEl.firstElementChild.style.width = '0'; return; }
    var f = Math.min(1, Math.max(0, (ctx.currentTime - cur.start) / (cur.end - cur.start)));
    progEl.firstElementChild.style.width = (f * 100) + '%';
    var showMark = cur.seg !== OUTRO;
    dmark.style.display = showMark ? '' : 'none';
    if (showMark) dmark.style.left = (Math.max(0, 1 - decisionLead() / (cur.end - cur.start)) * 100) + '%';
    if (!nxt && !queued && !cur.seg.loop && showMark && !STAGE_HOST) nextEl.innerHTML = T(autoMode === 'seq' ? 'next_none_seq' : 'next_none').replace('{s}', Math.max(0, cur.end - decisionLead() - ctx.currentTime).toFixed(1));
  }

  /* spectrum display (same style as the OS player) + segment colour */
  var viz = document.getElementById('viz'), vg = viz.getContext('2d');
  (function drawViz() {
    if (STAGE_HOST) return;   /* on the desktop stage the OS spectrum line is the display — the hidden canvas must not burn frames */
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
  /* language switch: cross-fade instead of a hard swap — text fades out (LANG_FADE_MS), the strings are replaced, then it fades back in */
  var LANG_FADE_MS = 220, langFading = false;
  document.querySelectorAll('[data-lang]').forEach(function (b) { b.addEventListener('click', function () {
    var next = b.getAttribute('data-lang'); if (next === lang || langFading) return;
    langFading = true; document.body.classList.add('lang-fade');
    setTimeout(function () {
      lang = next; try { localStorage.setItem('lang', lang); } catch (e) {}
      buildThemes(); buildButtons(); render();
      var back = function () { if (!langFading) return; document.body.classList.remove('lang-fade'); langFading = false; };
      requestAnimationFrame(back); setTimeout(back, 80);   /* timer fallback for throttled tabs */
    }, LANG_FADE_MS);
  }); });
  [optExclude, optRules].forEach(function (o) { o.addEventListener('change', render); });
  /* keyboard: a letter queues that group's first version; the same letter again cycles through its versions; O = outro */
  document.addEventListener('keydown', function (e) {
    if (!running) return;
    var g = e.key.toUpperCase();
    if (g === 'I' && OUTRO) { choose(OUTRO.id); return; }
    if (g === 'L' && byId.I_loop) { choose('I_loop'); return; }
    if (!byGroup[g]) return;
    var segs = SEG.filter(function (s) { return s.group === g; }), i = 0;
    var curPick = nxt ? later : queued;
    if (curPick && byId[curPick] && byId[curPick].group === g) i = (segs.findIndex(function (s) { return s.id === curPick; }) + 1) % segs.length;
    choose(segs[i].id);
  });

  fetch('segments.json').then(function (r) { return r.json(); }).then(function (cfg) {
    CFG = cfg; THEMES = cfg.themes || []; useTheme(THEMES[0]);
    optExclude.checked = !!cfg.excludeLast; buildThemes(); buildButtons(); render();
    if (STAGE_HOST) {   /* stage: no options — the transition rules always hold (they ARE the pre-entry/tail model) and repeats are the rules' business; no Start button — entering the stage starts the count-in */
      optRules.checked = true; optExclude.checked = false;
      var first = INTRO || SEG[0], bpm = first.bpmIn || first.bpmOut || 120;
      stageLead = 4 * 60 / bpm; stageT0 = null;   /* four 4/4 beats at the theme's tempo (145 -> ~1.7 s of load time) */
      setTimeout(start, 1100);   /* NOTHING loads while the entrance plays: the panel and button fade-ins own the thread until they have landed; the count-in begins with the loading */
    }
  });
  /* stage: the parent's click opened the stage, but autoplay activation does not always reach the iframe — the first tap inside revives a
     context the policy kept suspended. Never while paused: pause IS a suspend, and a tap on a section button must not silently resume it. */
  if (STAGE_HOST) document.addEventListener('pointerdown', function () { if (ctx && ctx.state === 'suspended' && !paused) ctx.resume(); }, true);
  /* stage: the same glass droplet the OS bars have — a spring-driven bubble glides between the buttons (velocity kept on retarget, a
     small overshoot wobble on landing). Kept in sync with the shell's feel: K/D/squash mirror os.js. */
  if (STAGE_HOST) (function () {
    var host = document.querySelector('.panel'); if (!host) return; host.style.position = 'relative';
    var b = document.createElement('i'); b.className = 'hb2'; b.setAttribute('aria-hidden', 'true'); host.insertBefore(b, host.firstChild);
    var K = 300, DA = 24, SQ = 0.0003, SQM = 0.10;
    var S = { x: 0, y: 0, w: 0, h: 0, vx: 0, vy: 0, vw: 0, vh: 0 }, TT = { x: 0, y: 0, w: 0, h: 0 }, on = false, raf = 0, lastT = 0;
    function apply() {
      var a = Math.min(SQM, Math.abs(S.vx) * SQ), c = Math.min(SQM, Math.abs(S.vy) * SQ);
      var w = Math.max(0, S.w) * (1 + a - c * 0.6), h = Math.max(0, S.h) * (1 + c - a * 0.6);
      b.style.transform = 'translate(' + (S.x - w / 2) + 'px,' + (S.y - h / 2) + 'px)'; b.style.width = w + 'px'; b.style.height = h + 'px';
    }
    function step(t) {
      raf = 0; var dt = Math.min(0.032, Math.max(0.001, (t - lastT) / 1000)); lastT = t;
      S.vx += ((TT.x - S.x) * K - S.vx * DA) * dt; S.x += S.vx * dt;
      S.vy += ((TT.y - S.y) * K - S.vy * DA) * dt; S.y += S.vy * dt;
      S.vw += ((TT.w - S.w) * K - S.vw * DA) * dt; S.w += S.vw * dt;
      S.vh += ((TT.h - S.h) * K - S.vh * DA) * dt; S.h += S.vh * dt;
      var done = Math.abs(TT.x - S.x) < 0.3 && Math.abs(TT.y - S.y) < 0.3 && Math.abs(TT.w - S.w) < 0.3 && Math.abs(TT.h - S.h) < 0.3 &&
                 Math.abs(S.vx) < 6 && Math.abs(S.vy) < 6 && Math.abs(S.vw) < 6 && Math.abs(S.vh) < 6;
      if (done) { S.x = TT.x; S.y = TT.y; S.w = TT.w; S.h = TT.h; S.vx = S.vy = S.vw = S.vh = 0; }
      apply(); if (!done) raf = requestAnimationFrame(step);
    }
    function wake() { if (!raf) { lastT = performance.now(); raf = requestAnimationFrame(step); } }
    host.addEventListener('pointerover', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('.seg,.big') : null; if (!t || t.disabled || t.classList.contains('off')) return;
      var r = t.getBoundingClientRect(), hr = host.getBoundingClientRect();
      TT.x = r.left - hr.left + host.scrollLeft + r.width / 2; TT.y = r.top - hr.top + host.scrollTop + r.height / 2; TT.w = r.width; TT.h = r.height;
      b.style.borderRadius = getComputedStyle(t).borderRadius;
      if (!on) { on = true; S.x = TT.x; S.y = TT.y; S.w = S.h = 0; S.vx = S.vy = S.vw = S.vh = 0; }
      b.style.opacity = 1; wake();
    });
    host.addEventListener('pointerleave', function () { on = false; TT.w = 0; TT.h = 0; b.style.opacity = 0; wake(); });
  })();
})();
