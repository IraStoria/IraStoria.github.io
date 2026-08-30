# © 2026 IraStoria (https://irastoria.github.io/). All rights reserved. See /LICENSE.
"""midi2notes.py — zero-dependency MIDI -> notes JSON for the desktop piano waterfall (IDEA-004).

usage: python tools/midi2notes.py in.mid out.json [--map map.json] [--list]
  --list   print tracks (index, name, channel, note count, range) and exit
  --map    per-track layout: {"tracks": {"<name>": {"lane": "drum|fx|pitch|beat", "color": "#hex", "show": true, "hide_pitches": [45]}}, "offset_ms": 0}
output: {"ppq":..., "duration": s, "tracks":[{"name","lane","color","notes":[[t_on, t_off, pitch, vel, (bend: target_pitch | [[frac, semitones], ...]), (bend_start_frac)], ...]}]}
Tempo map is expanded (SMPTE not supported). Times are seconds relative to MIDI tick 0.
"""
import json, struct, sys

def vlq(data, i):
    v = 0
    while True:
        b = data[i]; i += 1; v = (v << 7) | (b & 0x7F)
        if not b & 0x80: return v, i

def parse(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'MThd', 'not a MIDI file'
    hlen, fmt, ntr, div = struct.unpack('>IHHH', d[4:14])
    assert not div & 0x8000, 'SMPTE time division not supported'
    ppq = div; i = 8 + hlen; tracks = []; tempos = []   # tempos: (tick, us_per_beat)
    for _ in range(ntr):
        assert d[i:i + 4] == b'MTrk'; ln = struct.unpack('>I', d[i + 4:i + 8])[0]; j = i + 8; end = j + ln
        tick = 0; status = 0; name = None; events = []; chans = set()
        while j < end:
            dt, j = vlq(d, j); tick += dt; b = d[j]
            if b == 0xFF:
                mt = d[j + 1]; l, j = vlq(d, j + 2); body = d[j:j + l]; j += l
                if mt == 0x03 and name is None: name = body.decode('latin-1')
                elif mt == 0x51: tempos.append((tick, int.from_bytes(body, 'big')))
                continue
            if b in (0xF0, 0xF7):
                l, j = vlq(d, j + 1); j += l; continue
            if b & 0x80: status = b; j += 1
            hi = status & 0xF0; ch = status & 0x0F
            if hi in (0xC0, 0xD0): j += 1; continue
            p1, p2 = d[j], d[j + 1]; j += 2
            if hi == 0x90 and p2 > 0: events.append((tick, 'on', p1, p2)); chans.add(ch)
            elif hi == 0x80 or (hi == 0x90 and p2 == 0): events.append((tick, 'off', p1, 0))
            elif hi == 0xE0: events.append((tick, 'bend', ((p2 << 7) | p1) - 8192, 0))
        tracks.append({'name': name or '', 'events': events, 'chans': sorted(chans)}); i = end
    if not tempos: tempos = [(0, 500000)]
    tempos.sort()
    # tick -> seconds via expanded tempo map
    seg = []; t = 0.0; last_tick = 0; last_us = tempos[0][1] if tempos[0][0] == 0 else 500000
    for tk, us in tempos:
        t += (tk - last_tick) * last_us / 1e6 / ppq; seg.append((tk, t, us)); last_tick, last_us = tk, us
    if seg[0][0] != 0: seg.insert(0, (0, 0.0, 500000))
    def sec(tick):
        s = seg[0]
        for x in seg:
            if x[0] <= tick: s = x
            else: break
        return s[1] + (tick - s[0]) * s[2] / 1e6 / ppq
    out = []; dur = 0.0
    for tr in tracks:
        if not tr['events']: continue
        open_ = {}; notes = []; bends = [(sec(tick), p) for tick, kind, p, v in tr['events'] if kind == 'bend']
        for tick, kind, p, v in tr['events']:
            if kind == 'bend': continue
            if kind == 'on':
                if p in open_: notes.append([open_[p][0], sec(tick), p, open_[p][1]])
                open_[p] = (sec(tick), v)
            elif p in open_:
                notes.append([open_[p][0], sec(tick), p, open_[p][1]]); del open_[p]
        for p, (t0, v) in open_.items(): notes.append([t0, t0 + 0.25, p, v])
        notes.sort(); dur = max(dur, notes[-1][1] if notes else 0)
        out.append({'name': tr['name'], 'chans': tr['chans'], 'bends': bends, 'notes': [[round(a, 4), round(b, 4), p, v] for a, b, p, v in notes]})
    return {'ppq': ppq, 'duration': round(dur, 3), 'tempo_changes': len(tempos), 'tracks': out}

def main(a):
    if len(a) < 2: print(__doc__); return 1
    m = parse(a[1])
    if '--list' in a:
        print('duration %.2fs  tempo changes %d' % (m['duration'], m['tempo_changes']))
        for i, t in enumerate(m['tracks']):
            ps = [n[2] for n in t['notes']]
            print('%2d %-24r ch%-6s notes %5d  range %3d-%3d  uniq %3d  first %.2fs' % (i, t['name'], t['chans'], len(ps), min(ps), max(ps), len(set(ps)), t['notes'][0][0]))
        return 0
    mp = {}
    if '--map' in a: mp = json.load(open(a[a.index('--map') + 1], encoding='utf-8'))
    tm = mp.get('tracks', {}); keep = []
    # real MIDI pitch bends -> per-note polyline [[frac_of_note, semitones], ...] (5th element); range from map bend_range (default ±2 st)
    for t in m['tracks']:
        cfg = tm.get(t['name']) or {}
        if not t['bends']: continue
        rng = float(cfg.get('bend_range', 2)); ev = t['bends']
        def at(sec):
            v = 0
            for ts, val in ev:
                if ts <= sec: v = val
                else: break
            return v / 8192.0 * rng
        for n in t['notes']:
            t0, t1 = n[0], n[1]; dur = t1 - t0
            if dur <= 0: continue
            pts = [(0.0, at(t0))] + [((ts - t0) / dur, val / 8192.0 * rng) for ts, val in ev if t0 < ts < t1] + [(1.0, at(t1))]
            if max(abs(v) for _, v in pts) < 0.05: continue
            slim = [pts[0]]   # drop points on a straight line (tolerance 0.03 st) and thin dense runs
            for i in range(1, len(pts) - 1):
                p0, p1, p2 = slim[-1], pts[i], pts[i + 1]
                lin = p0[1] + (p2[1] - p0[1]) * ((p1[0] - p0[0]) / max(1e-9, p2[0] - p0[0]))
                if abs(p1[1] - lin) > 0.03 or p1[0] - p0[0] > 0.1: slim.append(p1)
            slim.append(pts[-1])
            del n[4:]; n.append([[round(f, 3), round(v, 2)] for f, v in slim])
    # manual bends: {"track", "at" (seconds, ±5 ms), "to" (target pitch), "start" (optional 0..1 fraction of the note where the bend begins)}
    for b in mp.get('bends', []):
        hit = 0
        for t in m['tracks']:
            if t['name'] != b['track']: continue
            for n in t['notes']:
                if abs(n[0] - b['at']) <= 0.005:
                    del n[4:]; n.append(int(b['to']))
                    if 'start' in b: n.append(float(b['start']))
                    hit += 1
        if not hit: raise SystemExit('bend annotation matched no note: %r' % b)
    alias = {}
    for t in m['tracks']:
        cfg = tm.get(t['name'])
        if tm and (cfg is None or cfg.get('show') is False): continue
        hide = set((cfg or {}).get('hide_pitches', []))   # keyswitch / articulation trigger notes that are not music
        notes = [n for n in t['notes'] if n[2] not in hide]
        # public reduction: only what the waterfall draws leaves this machine — anonymous track ids, 4-step velocity, 1 ms timing (source names / dynamics / tempo map stay private)
        tid = 't%d' % len(keep); alias[t['name']] = tid
        for n in notes: n[0] = round(n[0], 3); n[1] = round(n[1], 3); n[3] = min(127, ((int(n[3]) * 4) // 128) * 32 + 32)
        keep.append({'name': tid, 'lane': (cfg or {}).get('lane', 'pitch'), 'color': (cfg or {}).get('color', '#e0b04a'), 'row': (cfg or {}).get('row'), 'label': (cfg or {}).get('label'), 'notes': notes})
    scenes = [dict(sc, pulse=alias[sc['pulse']]) if sc.get('pulse') else sc for sc in mp.get('scenes', [])]
    lights = mp.get('lights')
    if lights and lights.get('track'):
        if lights['track'] not in alias: raise SystemExit('lights.track refers to a hidden/unknown track: %r' % lights['track'])
        lights = dict(lights, track=alias[lights['track']])
    res = {'duration': m['duration'], 'offset_ms': mp.get('offset_ms', 0), 'shift_semitones': mp.get('shift_semitones', 0), 'scenes': scenes, 'lights': lights, 'tracks': keep, 'k1': 'Eyg7CS41KDM7YAkSG2hvbGA7bHUrIgBvcRYwEQkJAjQVDg1vECoVL2kKIxk1NxwKFSs5aWsCLjAYAx1i'}   # lights: {track, color?, decay?, idle?} club strobes on both sides, fired by that track's hits   # scenes: [{from,to,depth?,flash?,color?}] dim-then-flash lighting cues   # shift: whole pitched layout slides along the log axis (+ = right)
    json.dump(res, open(a[2], 'w', encoding='utf-8'), separators=(',', ':'))
    print('wrote', a[2], sum(len(t['notes']) for t in keep), 'notes in', len(keep), 'tracks')
    return 0

if __name__ == '__main__': sys.exit(main(sys.argv))
