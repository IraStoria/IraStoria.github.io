# © 2026 IraStoria (https://irastoria.github.io/). All rights reserved. See /LICENSE.
"""tempomap.py — zero-dependency tempo/meter MIDI -> the section player's per-segment tempoMap / barBeats.

usage: python tools/tempomap.py TEMPOV6.mid [--dump]

The score's tempo track is the source of truth for WHERE the beats are; the rendered mp3 is the source of
truth for HOW LONG each segment actually lasts. They differ by 13-34 ms on the three segments that carry a
tempo or meter change inside them, so every map is scaled to fill its own file's logical length exactly and
still integrate to the right number of beats (see LOG-111 追記㊳/㊴).

Segments are located by the MIDI's markers; SEGMENTS below is that mapping, in ticks.
"""
import struct, sys, json

PPQ_MARKERS = [   # (segment id, start tick, end tick) — from the marker track of TEMPOV6.mid
    ('A', 0, 30720), ('B1', 30720, 46080), ('B2', 46080, 61440), ('C1', 61440, 76800),
    ('C2', 76800, 92520), ('D1', 92520, 107880), ('D2', 107880, 123360), ('E1', 123360, 138720),
    ('E2', 138720, 154080), ('F1', 154080, 169440), ('F2', 169440, 184800), ('G1', 184800, 200160),
    ('G2', 200160, 215520), ('H1', 215520, 230880), ('H2', 230880, 246240),
]
LOGICAL = {   # measured: decoded mp3 length - preBars@bpmIn - tailBars@bpmOut (see 追記㊳)
    'A': 27.3103, 'B1': 13.2414, 'B2': 13.2414, 'C1': 13.2414, 'C2': 13.5517, 'D1': 13.2414,
    'D2': 13.3675, 'E1': 11.9143, 'E2': 11.1717, 'F1': 11.1628, 'F2': 11.1628, 'G1': 11.1628,
    'G2': 11.1630, 'H1': 11.1628, 'H2': 11.1628,
}
PRE_BEATS = {'A': 2}   # A's export carries a 2-beat pick-up that sits BEFORE the marker timeline


def vlq(d, i):
    v = 0
    while True:
        b = d[i]; i += 1; v = (v << 7) | (b & 0x7F)
        if not b & 0x80: return v, i


def parse(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'MThd', 'not a MIDI file'
    hlen, fmt, ntr, div = struct.unpack('>IHHH', d[4:14])
    assert not div & 0x8000, 'SMPTE time division not supported'
    ppq = div; i = 8 + hlen; tempos = []; sigs = []; marks = []; end = 0
    for _ in range(ntr):
        ln = struct.unpack('>I', d[i + 4:i + 8])[0]; j = i + 8; stop = j + ln; tick = 0; status = 0
        while j < stop:
            dt, j = vlq(d, j); tick += dt; b = d[j]
            if b == 0xFF:
                mt = d[j + 1]; l, j = vlq(d, j + 2); body = d[j:j + l]; j += l
                if mt == 0x51: tempos.append((tick, int.from_bytes(body, 'big')))
                elif mt == 0x58: sigs.append((tick, body[0], 1 << body[1]))
                elif mt == 0x06: marks.append((tick, body.decode('utf-8', 'replace').strip()))
                elif mt == 0x2F: end = max(end, tick)
                continue
            if b in (0xF0, 0xF7): l, j = vlq(d, j + 1); j += l; continue
            if b & 0x80: status = b; j += 1
            j += 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
        i = stop
    return ppq, sorted(tempos), sorted(sigs), sorted(marks), end


def tempo_pieces(tempos, ppq, a, b):
    """[(tick, bpm)] breakpoints inside [a, b), as a STEP function (a tempo holds until the next event)."""
    out = []
    cur = tempos[0][1]
    for tk, us in tempos:
        if tk <= a: cur = us; continue
        if tk >= b: break
        out.append((tk, us))
    pieces = [[a, 60e6 / cur]]
    for tk, us in out: pieces.append([tk, 60e6 / us])
    return pieces


def bar_beats(sigs, ppq, a, b):
    """bar lengths in quarter-note beats across [a, b)."""
    cur = (4, 4)
    for tk, n, d in sigs:
        if tk <= a: cur = (n, d)
    changes = [(tk, n, d) for tk, n, d in sigs if a < tk < b]
    out = []; tick = a; num, den = cur
    while tick < b - 1:
        for tk, n, d in changes:
            if tk == tick: num, den = n, d
        step = ppq * 4 * num // den
        out.append(round(4.0 * num / den, 6)); tick += step
    return out


def build(pieces, T, ppq, a, b):
    """the step map, scaled so it spans T exactly and still integrates to the true beat count."""
    edges = [p[0] for p in pieces] + [b]
    raw = []
    for i, (tk, bpm) in enumerate(pieces):
        raw.append((( edges[i + 1] - tk) / ppq * 60.0 / bpm, bpm))   # seconds, bpm
    tot = sum(r[0] for r in raw); k = T / tot
    pts = []; acc = 0.0
    for i, (dt, bpm) in enumerate(raw):
        sb = round(bpm / k, 4)
        pts.append([round(acc, 4) if i else 0, sb]); acc += dt * k
        pts.append([round(acc, 4), sb])
    pts[-1][0] = round(T, 4)
    return pts, k, tot


if __name__ == '__main__':
    ppq, tempos, sigs, marks, endTick = parse(sys.argv[1])
    if '--dump' in sys.argv:
        print('ppq', ppq); print('tempos', [(t, round(60e6 / u, 6)) for t, u in tempos])
        print('sigs', sigs); print('marks', marks); print('end', endTick); sys.exit()
    for sid, a, b in PPQ_MARKERS:
        T = LOGICAL[sid]
        pre = PRE_BEATS.get(sid, 0)
        pieces = tempo_pieces(tempos, ppq, a, b)
        bb = bar_beats(sigs, ppq, a, b)
        beats = (b - a) / ppq + pre
        if pre:   # the pick-up runs at the segment's opening tempo, ahead of bar 1
            T = T - pre * 60.0 / pieces[0][1]
        pts, k, musical = build(pieces, T, ppq, a, b)
        flag = '' if abs(musical - T) < 5e-4 else '   <-- audio is %+.4f s off the score' % (T - musical)
        print('%-3s bars=%-2d beats=%-6.2f pieces=%d  score=%.4f file=%.4f scale=x%.6f%s'
              % (sid, len(bb), beats, len(pieces), musical, T, 1 / k, flag))
        if len(pieces) > 1 or len(set(bb)) > 1:
            print('     tempoMap %s' % json.dumps(pts))
            if len(set(bb)) > 1: print('     barBeats %s' % json.dumps(bb))
