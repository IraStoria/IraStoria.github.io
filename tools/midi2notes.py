"""midi2notes.py — zero-dependency MIDI -> notes JSON for the desktop piano waterfall (IDEA-004).

usage: python tools/midi2notes.py in.mid out.json [--map map.json] [--list]
  --list   print tracks (index, name, channel, note count, range) and exit
  --map    per-track layout: {"tracks": {"<name>": {"lane": "drum|fx|pitch|beat", "color": "#hex", "show": true}}, "offset_ms": 0}
output: {"ppq":..., "duration": s, "tracks":[{"name","lane","color","notes":[[t_on, t_off, pitch, vel, (bend_to_pitch, (bend_start_frac))], ...]}]}
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
        open_ = {}; notes = []
        for tick, kind, p, v in tr['events']:
            if kind == 'on':
                if p in open_: notes.append([open_[p][0], sec(tick), p, open_[p][1]])
                open_[p] = (sec(tick), v)
            elif p in open_:
                notes.append([open_[p][0], sec(tick), p, open_[p][1]]); del open_[p]
        for p, (t0, v) in open_.items(): notes.append([t0, t0 + 0.25, p, v])
        notes.sort(); dur = max(dur, notes[-1][1] if notes else 0)
        out.append({'name': tr['name'], 'chans': tr['chans'], 'notes': [[round(a, 4), round(b, 4), p, v] for a, b, p, v in notes]})
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
    for t in m['tracks']:
        cfg = tm.get(t['name'])
        if tm and (cfg is None or cfg.get('show') is False): continue
        keep.append({'name': t['name'], 'lane': (cfg or {}).get('lane', 'pitch'), 'color': (cfg or {}).get('color', '#e0b04a'), 'row': (cfg or {}).get('row'), 'notes': t['notes']})
    res = {'duration': m['duration'], 'offset_ms': mp.get('offset_ms', 0), 'tracks': keep}
    json.dump(res, open(a[2], 'w', encoding='utf-8'), separators=(',', ':'))
    print('wrote', a[2], sum(len(t['notes']) for t in keep), 'notes in', len(keep), 'tracks')
    return 0

if __name__ == '__main__': sys.exit(main(sys.argv))
