# © 2026 IraStoria (https://irastoria.github.io/). All rights reserved. See /LICENSE.
"""roadnotes.py — the road's display track (MIDITRACKV6.mid) -> a per-segment note table in segments.json.

usage: python tools/roadnotes.py MIDITRACKV6.mid [--write]

The road used to invent its own traffic: one note per beat, always in the centre lane, straight off the beat
grid. That threw away everything the piece actually does at those moments - A's eighth/sixteenth build-up, the
flare that lands on B's downbeat, the quarter-note triplets at the end of C2, and the seventeen alternating
sixteenths of D2's 17/16 bar. This track IS that choreography, hand-authored: pitch 72 is the centre lane and
everything else is a lane either side of it.

Output per segment: "road": [[beat, semitonesFromCentre], ...] - `beat` counted in quarter notes from the
segment's own logical start, so it is tempo-free and survives the tempoMap's audio scaling untouched.
"""
import json, struct, sys, collections, io, os

PPQ_SEGMENTS = [   # (id, start tick, end tick) - the marker track of TEMPOV6/MIDITRACKV6
    ('A', 0, 30720), ('B1', 30720, 46080), ('B2', 46080, 61440), ('C1', 61440, 76800),
    ('C2', 76800, 92520), ('D1', 92520, 107880), ('D2', 107880, 123360), ('E1', 123360, 138720),
    ('E2', 138720, 154080), ('F1', 154080, 169440), ('F2', 169440, 184800), ('G1', 184800, 200160),
    ('G2', 200160, 215520), ('H1', 215520, 230880), ('H2', 230880, 246240), ('I', 246240, 10 ** 9),
]
CENTRE = 72   # the author's centre pitch (their C4; MIDI 72)


def vlq(d, i):
    v = 0
    while True:
        b = d[i]; i += 1; v = (v << 7) | (b & 0x7F)
        if not b & 0x80: return v, i


def notes_of(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'MThd', 'not a MIDI file'
    hlen, fmt, ntr, div = struct.unpack('>IHHH', d[4:14])
    assert not div & 0x8000, 'SMPTE time division not supported'
    ppq = div; i = 8 + hlen; out = []
    for _ in range(ntr):
        ln = struct.unpack('>I', d[i + 4:i + 8])[0]; j = i + 8; stop = j + ln; tick = 0; status = 0
        while j < stop:
            dt, j = vlq(d, j); tick += dt; b = d[j]
            if b == 0xFF:
                mt = d[j + 1]; l, j = vlq(d, j + 2); j += l; continue
            if b in (0xF0, 0xF7): l, j = vlq(d, j + 1); j += l; continue
            if b & 0x80: status = b; j += 1
            hi = status & 0xF0
            if hi in (0xC0, 0xD0): j += 1; continue
            p1, p2 = d[j], d[j + 1]; j += 2
            if hi == 0x90 and p2 > 0: out.append((tick, p1))
        i = stop
    return ppq, sorted(out)


def tables(ppq, notes):
    per = collections.OrderedDict()
    for sid, a, b in PPQ_SEGMENTS:
        rows = []
        for tk, p in notes:
            if a <= tk < b:
                beat = round((tk - a) / float(ppq), 4)
                semi = p - CENTRE
                rows.append([beat] if semi == 0 else [beat, semi])
        per[sid] = rows
    return per


if __name__ == '__main__':
    ppq, notes = notes_of(sys.argv[1])
    per = tables(ppq, notes)
    total = sum(len(v) for v in per.values())
    print('ppq %d  notes %d  placed %d' % (ppq, len(notes), total))
    for sid, rows in per.items():
        subs = collections.Counter()
        for r in rows:
            f = round(r[0] % 1, 4)
            subs['beat' if f == 0 else ('8th' if f == 0.5 else ('16th' if f in (0.25, 0.75) else ('trip' if abs(f - 0.6667) < 2e-3 or abs(f - 0.3333) < 2e-3 else '?%s' % f)))] += 1
        lanes = sorted(set(r[1] for r in rows if len(r) > 1))
        print('  %-3s %3d notes  %-42s lanes %s' % (sid, len(rows), dict(subs), lanes or '-'))
    if '--write' in sys.argv:
        p = 'demos/interactive-player/segments.json'
        d = json.loads(io.open(p, encoding='utf-8').read(), object_pairs_hook=collections.OrderedDict)
        t = d['themes'][0]
        by = {s['id']: s for s in t['segments']}
        by[t['intro']['id']] = t['intro']; by[t['outro']['id']] = t['outro']
        for sid, rows in per.items():
            if sid in by: by[sid]['road'] = rows
        io.open(p, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2) + '\n')
        print('written', p)
