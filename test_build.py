#!/usr/bin/env python3
# © 2026 IraStoria (https://irastoria.github.io/). All rights reserved. See /LICENSE.
"""Self-tests for build.py — run: python test_build.py
Covers: fail-closed rules (ADR-002/003/004/005), internal link integrity, bilingual page pairing."""
import copy
import json
import re
import sys
from pathlib import Path

import build as B

ROOT = B.ROOT
results = []


def expect_refused(name, fn, needle):
    try:
        fn()
    except B.BuildError as e:
        ok = needle in str(e)
        results.append((ok, name, "" if ok else f"wrong error: {e}"))
        return
    results.append((False, name, "was NOT refused"))


def ok(name, cond, msg=""):
    results.append((bool(cond), name, msg))


# ---- fixtures
site = B.load_site()
works = B.load_works()
demos = B.load_demos(works)
articles = B.load_articles()


def with_works(mut):
    """Return a callable that runs load_works() against an in-memory mutated copy of works.json."""
    w = copy.deepcopy(works)
    mut(w)

    def loader():
        real = B.read_json
        B.read_json = lambda p: copy.deepcopy(w) if p.name == "works.json" else real(p)
        try:
            return B.load_works()
        finally:
            B.read_json = real
    return loader


# ---- 1. bilingual fail-closed
expect_refused("missing en title refused", with_works(lambda w: w[0]["title"].pop("en")), "missing or empty 'en'")
expect_refused("empty zh desc refused", with_works(lambda w: w[0]["desc"].update(zh="  ")), "missing or empty 'zh'")
expect_refused("extra language key refused", with_works(lambda w: w[0]["title"].update(ja="x")), "unexpected language key")
expect_refused("bad type refused", with_works(lambda w: w[0].update(type="video")), "'type' must be one of")
expect_refused("bad platform refused", with_works(lambda w: w[0].update(platform="mobile")), "'platform' must be")
expect_refused("unknown media key refused", with_works(lambda w: w[0]["media"].update(vimeo="x")), "unknown media key")
expect_refused("missing local media refused", with_works(lambda w: w[0]["media"].update(local="assets/nope.mp3")), "local media not found")
expect_refused("duplicate id refused", with_works(lambda w: w.append(copy.deepcopy(w[0]))), "duplicate id")
expect_refused("missing demo folder refused", with_works(lambda w: w[0]["media"].update(demo="demos/ghost/")), "demo folder missing")

# wav local media refused (ADR-005) — create a temp file
wav = ROOT / "assets" / "_t.wav"
wav.write_bytes(b"RIFF")
expect_refused("wav local media refused", with_works(lambda w: w[0]["media"].update(local="assets/_t.wav")), "must be mp3/ogg")
wav.unlink()

# ---- LOG-172: transcription compare (feat.transcription-compare / ADR-006 追記①)
TR = [i for i, w in enumerate(works) if w.get("type") == "transcription"]
ok("works.json carries the two transcription entries (dp-tr, olympia-tr)", [works[i]["id"] for i in TR] == ["dp-tr", "olympia-tr"], str([works[i]["id"] for i in TR]))
_ti = TR[0]
_oly = works[TR[1]]   # LOG-176: the second entry - MIDI 0 sits 2.8 s BEFORE the recording starts (negative offsets are legal), eight sections, the hosted copy under the 10 MB line
ok("olympia-tr: hosted original (the video owner forbids embedding, IFrame error 150) with a negative offset", _oly["media"]["original"]["kind"] == "local" and _oly["media"]["original"]["offset_s"] == -2.8 and "fallback" not in _oly["media"]["original"])
ok("olympia-tr: its own rights text (the site-wide one names Death Piano), bilingual, with the {contact} hook", all(_oly["media"]["original"][k].get("zh") and _oly["media"]["original"][k].get("en") for k in ("notice", "notice_note")) and "{contact}" in _oly["media"]["original"]["notice"]["zh"] and "Oliver" in _oly["media"]["original"]["notice"]["en"])
expect_refused("a one-language notice is refused", with_works(lambda w: w[TR[1]]["media"]["original"]["notice"].pop("en")), "missing or empty 'en'")
ok("olympia-tr: opens with the transcription at half volume; dp keeps the default (no volume key)", _oly["media"]["rendition"].get("volume") == 0.5 and "volume" not in works[TR[0]]["media"]["rendition"])
expect_refused("a rendition volume outside (0, 1] is refused", with_works(lambda w: w[TR[1]]["media"]["rendition"].update(volume=1.5)), "media.rendition.volume must be")
ok("olympia-tr: the original's link rides on media.original.url (the stage offers it in the switch's seat)", _oly["media"]["original"].get("url") == "https://youtu.be/9ZDGVOjXaEY")
expect_refused("a non-http original url is refused", with_works(lambda w: w[TR[1]]["media"]["original"].update(url="javascript:alert(1)")), "media.original.url must be")
ok("olympia-tr: its own stage palette (peach-pink left, orange-red right)", _oly["media"].get("palette") == {"l": "#ff4f8b", "r": "#ff7a3a"})
expect_refused("a palette that is not two hex colours is refused", with_works(lambda w: w[TR[1]]["media"].update(palette={"l": "pink", "r": "#ff7a3a"})), "media.palette must be")
ok("olympia-tr: eight increasing sections, bilingual", len(_oly["sections"]) == 8 and all(_oly["sections"][i]["t"] > _oly["sections"][i - 1]["t"] for i in range(1, 8)) and all(x.get("zh") and x.get("en") for x in _oly["sections"]))
ok("olympia-tr: notes JSON generated from content/midi/olympia.mid", (ROOT / _oly["media"]["notes"]).exists() and (ROOT / "content/midi/olympia.map.json").exists())
ok("olympia-tr: hosted copy and render under 10 MB", (ROOT / _oly["media"]["original"]["src"]).stat().st_size < 10 * 1024 * 1024 and (ROOT / _oly["media"]["rendition"]["src"]).stat().st_size < 10 * 1024 * 1024)
expect_refused("transcription without rendition refused", with_works(lambda w: w[_ti]["media"].pop("rendition")), "media.rendition must be")
expect_refused("transcription rendition file missing refused", with_works(lambda w: w[_ti]["media"]["rendition"].update(src="assets/audio/nope.mp3")), "media.rendition.src not found")
expect_refused("transcription original with a bad kind refused", with_works(lambda w: w[_ti]["media"]["original"].update(kind="vimeo")), "must be 'youtube' or 'local'")
expect_refused("transcription with a malformed YouTube id refused", with_works(lambda w: w[_ti]["media"]["original"].update(id="abc")), "11-character YouTube video id")
expect_refused("transcription fallback must be a real mp3", with_works(lambda w: w[_ti]["media"]["original"].update(fallback="assets/audio/ghost.mp3")), "media.original.fallback not found")
expect_refused("transcription notes must exist", with_works(lambda w: w[_ti]["media"].update(notes="assets/notes/ghost.json")), "media.notes must name an existing")
expect_refused("transcription media may not carry the player's keys", with_works(lambda w: w[_ti]["media"].update(local="assets/audio/dp.mp3")), "unknown media key")
expect_refused("section cue points must increase", with_works(lambda w: w[_ti]["sections"][2].update(t=1.0)), "'t' must increase")
expect_refused("section labels are bilingual", with_works(lambda w: w[_ti]["sections"][0].pop("en")), "missing or empty 'en'")
expect_refused("rendition offset must be a number", with_works(lambda w: w[_ti]["media"]["rendition"].update(offset_s="2.4")), "offset_s must be a number")
expect_refused("fallback offset must be a number", with_works(lambda w: w[_ti]["media"]["original"].update(fallback_offset_s="x")), "fallback_offset_s must be a number")
expect_refused("original gain must sit in (0, 1]", with_works(lambda w: w[_ti]["media"]["original"].update(gain=1.5)), "gain must be a number in (0, 1]")
ok("a local-kind original needs no YouTube id", bool(with_works(lambda w: w[_ti]["media"].update(original={"kind": "local", "src": "assets/audio/dp.mp3", "offset_s": 0}))()))
for k in ("type_transcription", "tr_open", "tr_desktop_only", "tr_notice", "tr_notice_head", "tr_yt_fail", "tr_mode_lr", "tr_src_local"):
    ok(f"site.json ui.{k} present and bilingual", isinstance(site["ui"].get(k), dict) and site["ui"][k].get("zh") and site["ui"][k].get("en"))
ok("the notice names the contact app by a placeholder, never an email address", "{contact}" in site["ui"]["tr_notice"]["zh"] and "{contact}" in site["ui"]["tr_notice"]["en"] and "@" not in site["ui"]["tr_notice"]["zh"] and "@" not in site["ui"]["tr_notice"]["en"])

# ---- 2. demo contract (ADR-004)
dj = ROOT / "demos" / "interactive-player" / "demo.json"
orig_b = dj.read_bytes(); orig = orig_b.decode("utf-8")   # bytes: write_text would turn LF into CRLF on Windows and dirty the tree
try:
    d = json.loads(orig); d["concept_level_checked"] = False
    dj.write_text(json.dumps(d), encoding="utf-8")
    expect_refused("demo without concept check refused", lambda: B.load_demos(works), "concept_level_checked")
    d = json.loads(orig); d["title"].pop("en")
    dj.write_text(json.dumps(d), encoding="utf-8")
    expect_refused("demo missing en title refused", lambda: B.load_demos(works), "missing or empty 'en'")
finally:
    dj.write_bytes(orig_b)

# ---- 3. articles: reviewed + pairing (ADR-002)
adir = ROOT / "content" / "articles"
adir.mkdir(exist_ok=True)
zh = adir / "zz-test.zh.md"; en = adir / "zz-test.en.md"
try:
    zh.write_text("---\ntitle: 測試\ndate: 2026-08-28\nreviewed: true\n---\n\n# 標題\n\n內文 **粗體**。\n\n- 一\n- 二\n", encoding="utf-8")
    expect_refused("article missing en pair refused", B.load_articles, "missing zz-test.en.md")
    en.write_text("---\ntitle: Test\ndate: 2026-08-28\nreviewed: false\n---\n\nbody\n", encoding="utf-8")
    expect_refused("article not reviewed refused", B.load_articles, "'reviewed: true' required")
    en.write_text("---\ntitle: Test\ndate: 2026-08-28\nreviewed: true\n---\n\nbody [link](https://x.y)\n", encoding="utf-8")
    arts = B.load_articles()
    ok("reviewed pair accepted", len(arts) == 1 and arts[0]["slug"] == "zz-test")
    ok("markdown rendered", "<strong>粗體</strong>" in arts[0]["zh"]["html"] and "<ul>" in arts[0]["zh"]["html"]
       and '<a href="https://x.y">link</a>' in arts[0]["en"]["html"])
    pages = B.build_pages(site, works, demos, arts)
    ok("article pages generated in both languages",
       "zh/articles/zz-test/index.html" in pages and "en/articles/zz-test/index.html" in pages)
    ok("article page escapes and links root correctly", 'href="../../../assets/css/style.css?v=' in pages["zh/articles/zz-test/index.html"])
finally:
    zh.unlink(missing_ok=True); en.unlink(missing_ok=True)

# ---- 4. rendering: placeholders, pairing, links
pages = B.build_pages(site, works, demos, articles)
ok("no unresolved placeholders", not any(re.search(r"\{\{\w+\}\}", h) for h in pages.values()),
   str([p for p, h in pages.items() if re.search(r"\{\{\w+\}\}", h)]))
zh_pages = {p[3:] for p in pages if p.startswith("zh/")}
en_pages = {p[3:] for p in pages if p.startswith("en/")}
ok("zh/en page sets identical (S4)", zh_pages == en_pages, str(zh_pages ^ en_pages))
ok("html lang attributes", all(('<html lang="zh-Hant">' in h) == p.startswith("zh/") for p, h in pages.items() if p.startswith(("zh/", "en/"))))

# internal link integrity: every relative href/src must resolve to a file in pages or on disk
broken = []
for p, h in pages.items():
    base = Path(p).parent
    for m in re.finditer(r'(?:href|src)="([^"#]+)"', h):
        u = m.group(1).split("?")[0]
        if u.startswith(("http", "mailto:", "data:", "#")) or u == "#":   # data: URIs (e.g. inline SVG displacement maps) are content, not links
            continue
        target = (base / u) if not u.startswith("/") else Path(u.lstrip("/"))
        parts = [x for x in target.as_posix().split("/") if x not in ("", ".")]
        stack = []
        for x in parts:
            if x == "..":
                if stack: stack.pop()
                else: broken.append((p, u, "escapes root")); break
            else:
                stack.append(x)
        rel = "/".join(stack)
        if rel.endswith("/") or rel == "" or "." not in stack[-1] if stack else True:
            rel = (rel + "/" if rel and not rel.endswith("/") else rel) + "index.html"
        if rel not in pages and not (ROOT / rel).exists():
            broken.append((p, u, rel))
ok("no broken internal links", not broken, str(broken))

# email anti-scrape (V2): raw address must not appear in any page
addr = site["contact"]["email_user"] + "@" + site["contact"]["email_domain"]
ok("email never appears verbatim (V2)", not any(addr in h for h in pages.values()))
ok("email assembled on click present", all('data-email' in pages[f"{l}/about/index.html"] for l in ("zh", "en")))
ok("desktop shell embeds site data", all('id="site-data"' in pages[f"{l}/index.html"] and '"works"' in pages[f"{l}/index.html"] for l in ("zh", "en")))
ok("no </script> breakout in embedded JSON", all(pages[f"{l}/index.html"].count("</script>") == 2 for l in ("zh", "en")))

# lang switch on every page points to the mirrored path
mis = [p for p in pages if p.startswith("zh/") and f'href="{"../" * (p.count("/"))}en/{p[3:-len("index.html")]}"' not in pages[p]]
ok("lang switch mirrors path", not mis, str(mis))

# platform note (R3) present on demo card
ok("R3 platform note on demo card", "桌面瀏覽器限定" in pages["zh/index.html"] and "Desktop only" in pages["en/index.html"])

ok("assets carry cache-busting version", all(re.search(r'os\.js\?v=[0-9a-f]{8}', pages[f"{l}/index.html"]) and re.search(r'style\.css\?v=[0-9a-f]{8}', pages[f"{l}/about/index.html"]) for l in ("zh", "en")))

# XSS escape sanity
ok("html escaping", B.esc('<a "b">') == "&lt;a &quot;b&quot;&gt;")

# JS syntax (fail-closed): a stray comment once shipped a broken os.js — node --check every shipped script
import subprocess, shutil
_node = shutil.which("node")
for _js in sorted(list((B.ROOT / "assets" / "js").glob("*.js")) + list((B.ROOT / "demos").glob("*/demo.js"))):
    if _node:
        _r = subprocess.run([_node, "--check", str(_js)], capture_output=True, text=True)
        ok(f"js syntax {_js.relative_to(B.ROOT).as_posix()}", _r.returncode == 0, _r.stderr.strip().splitlines()[-1] if _r.returncode else "")
    else:
        ok(f"js syntax {_js.name}", False, "node not found")

# Swallowed-code guard: a `//` comment that itself contains a statement (e.g. "... line-only var t = list[i];") silently
# eats the code after it and still passes node --check. Flag comment tails that look like code.
_swallow = re.compile(r'//.*(var|let|const|return|function).*;\s*$')
for _js in sorted(list((B.ROOT / "assets" / "js").glob("*.js")) + list((B.ROOT / "demos").glob("*/demo.js"))):
    _bad = [i + 1 for i, ln in enumerate(_js.read_text(encoding="utf-8").splitlines()) if "://" not in ln and _swallow.search(ln)]
    ok(f"no code swallowed by // comment in {_js.relative_to(B.ROOT).as_posix()}", not _bad, f"lines {_bad}")

# Forced-flag registry (eng.ee-registry): one table, and every use derived from it. The accept-list and the expansion
# group were each spelled out by hand where they were used, and a key added to one but not the other shipped as a bug
# once already. This guards the shape, never the contents — no key of that table belongs in a test file.
_os_js = (B.ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8")
ok("forced-flag registry exists", "var EE_REG = {" in _os_js)
for _fn in ("eeKeys", "eeGroup", "eeOn", "eeTake", "eeMode"):
    ok(f"registry accessor {_fn}() defined", re.search(r"\n  function " + _fn + r"\(", _os_js) is not None)
# only the registry itself may touch the raw flag bag: everything else goes through an accessor
_raw = [i + 1 for i, ln in enumerate(_os_js.splitlines())
        if re.search(r"\bEE\s*(\.\w|\[)", ln) and "function ee" not in ln and "var EE = {}" not in ln]
ok("no raw forced-flag reads outside the registry", not _raw, f"lines {_raw}")


# ---- LOG-159: the About app's second window (resume.json)
_res = B.load_resume()
ok("resume.json loads with the three sections", all(isinstance(_res.get(k), list) and _res[k] for k in ("education", "current", "past")))
ok("desktop shell embeds the resume", all('"resume"' in pages[f"{l}/index.html"] and '"host"' in pages[f"{l}/index.html"] for l in ("zh", "en")))
_CJK = re.compile(r"[\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3040-\u30ff]")


def _leaves(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from _leaves(x)
    elif isinstance(v, list):
        for x in v:
            yield from _leaves(x)
    elif isinstance(v, str):
        yield v


_en = list(_leaves(B.loc_deep({k: v for k, v in _res.items() if not k.startswith("_")}, "en")))
_leak = [s for s in _en if _CJK.search(s) and not _KANA.search(s)]   # a Japanese title keeps its kanji; anything else with CJK is Chinese leaking into English
ok("English resume shows no Chinese (Japanese titles exempt)", not _leak, str(_leak))
_pp = B.loc_deep(site.get("prank_pages") or {}, "en")
_pleak = [x for x in _leaves({k: v for k, v in _pp.items() if not k.startswith("_")}) if _CJK.search(x)]
ok("prank pages: English side has no Chinese, both pools present", not _pleak and len(_pp.get("serious", [])) >= 4 and len(_pp.get("silly", [])) >= 4, str(_pleak))
ok("desktop shell embeds the prank pages", all('"prank"' in pages[f"{l}/index.html"] for l in ("zh", "en")))
ok("school names never in Chinese", all(not _CJK.search(e["school"]) for e in _res["education"]))
ok("Formosa Studio title is the 2026-09-08 one", any(c["title"]["zh"] in ("臺灣區域聯絡人 | 製作人助理", "臺灣區域聯絡人 / 製作人助理") for c in _res["current"]) and not any("端口" in json.dumps(c, ensure_ascii=False) for c in _res["current"]))

# ---- LOG-172: the compare stage's data reaches the desktop payload
_zh = (ROOT / "zh" / "index.html").read_text(encoding="utf-8") if (ROOT / "zh" / "index.html").exists() else ""
ok("desktop payload carries dp-tr with a versioned rendition and localised sections", '"id": "dp-tr"' in _zh and 'dp-tr.mp3?v=' in _zh and '"label": "序奏"' in _zh and '"label": "Intro"' in _zh)
_os = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8")
ok("os.js: the compare stage is asked first in ext.api()", "trStage.active()) return trStage.src()" in _os)
ok("os.js: the waterfall honours a source that insists on its notes (st.wf)", "(st.wf || wfOn())" in _os)
ok("os.js: the transport routes pause / prev / next / mute to the compare stage", _os.count("if (trStage.active()) trStage.") >= 4)
ok("os.js: YouTube's IFrame API loads only from the stage (no page-load third-party script)", _os.count("youtube.com/iframe_api") == 1 and "iframe_api" not in (ROOT / "templates" / "desktop.html").read_text(encoding="utf-8"))

# ---- LOG-179: the well's example wishes
_ex = B.load_site().get("wish_examples") or []
ok("site.json carries fourteen example wishes, every wisher 範例 / Example", len(_ex) == 14 and all(e["nick"] == {"zh": "範例", "en": "Example"} for e in _ex))
ok("the NieR line is there with the owner's reply", any("尼爾" in e["text"] and "尼爾" in e.get("reply", "") and "NieR" not in e.get("reply", "") for e in _ex))
ok("example statuses are wish statuses", all(e["status"] in B.WISH_STATUSES for e in _ex))
ok("the desktop page carries the examples in its language", '"wish_examples"' in (ROOT / "zh" / "index.html").read_text(encoding="utf-8") and "Example" in (ROOT / "en" / "index.html").read_text(encoding="utf-8"))
expect_refused("an example with a made-up status is refused", lambda: B.check_wish_examples([dict(_ex[0], status="maybe")]), "status must be one of")

# ---- LOG-161: 恥辱柱 / 許願池 / 合作聯絡
_bugs = B.load_bugs()
ok("bugs.json loads, newest first, >= 10 entries", len(_bugs) >= 10 and all(_bugs[i]["date"] >= _bugs[i + 1]["date"] for i in range(len(_bugs) - 1)), str(len(_bugs)))
_bleak = [b["id"] for b in B.loc_deep(_bugs, "en") if _CJK.search(b["title"] + b["desc"])]
ok("bugs: English side has no Chinese", not _bleak, str(_bleak))
_spoil = [b["id"] for b in _bugs if re.search(r"彩蛋|EE_|easter|secret|隱藏曲", json.dumps(b, ensure_ascii=False), re.I)]
ok("bugs: the roster never mentions easter eggs (不劇透)", not _spoil, str(_spoil))
_b1 = copy.deepcopy(_bugs)
expect_refused("bugs: unknown status refused", lambda: B.load_bugs({"bugs": [dict(_b1[0], status="zombie")]}), "status")
expect_refused("bugs: unknown where refused", lambda: B.load_bugs({"bugs": [dict(_b1[0], where="tablet")]}), "where")
expect_refused("bugs: missing English title refused", lambda: B.load_bugs({"bugs": [dict(_b1[0], title={"zh": _b1[0]["title"]["zh"]})]}), "missing or empty 'en'")
expect_refused("bugs: duplicate id refused", lambda: B.load_bugs({"bugs": [_b1[0], dict(_b1[1], id=_b1[0]["id"])]}), "duplicate")
ok("ui has the three app names", all(k in site["ui"] for k in ("app_pillar", "app_wishpool", "app_contact")))
ok("desktop shell embeds bugs + backend + contact services", all('"bugs"' in pages[f"{l}/index.html"] and '"backend"' in pages[f"{l}/index.html"] and '"services"' in pages[f"{l}/index.html"] for l in ("zh", "en")))
ok("desktop template carries the three icons", all(f'data-app="{a}"' in pages["zh/index.html"] for a in ("pillar", "wishpool", "contact")))
ok("contact services: three, bilingual (LOG-167: tools dropped)", len(site["contact"]["services"]) == 3 and all(not _CJK.search(s["label"]["en"] + s["desc"]["en"]) for s in site["contact"]["services"]))
expect_refused("backend.url with a trailing slash refused", lambda: B.backend_url({"backend": {"url": "https://pool.example.workers.dev/"}}), "trailing")
expect_refused("backend.url without a scheme refused", lambda: B.backend_url({"backend": {"url": "pool.example.workers.dev"}}), "http(s)")
ok("backend.url empty is fine (not wired)", B.backend_url({"backend": {"url": ""}}) == "")
ok("worker contract + script + deploy guide present", all((ROOT / "worker" / f).exists() for f in ("API.md", "worker.js", "DEPLOY.md", "test_worker.mjs")))
ok("worker.js carries no secret", not re.search(r"(ghp_|gho_)[A-Za-z0-9]{20,}|client_secret\s*[:=]\s*['\"][^'\"]{8,}", (ROOT / "worker" / "worker.js").read_text(encoding="utf-8")))


# ---- LOG-182 the simple stage (feat.stage-tutorial · ADR-011)
_tut = [k for k in site["ui"] if k.startswith("tut_")] + ["sec_simple", "sec_adv"]
ok("LOG-182: the lesson has its lines", len([k for k in _tut if k.startswith("tut_s")]) >= 10 and all(k in site["ui"] for k in ("tut_ask_t", "tut_ask_simple", "tut_ask_adv")))   # LOG-183: tut_skip is gone with the in-bubble skip button (the head row carries 結束教學 instead)
ok("LOG-182: every lesson string is bilingual", all(isinstance(site["ui"].get(k), dict) and site["ui"][k].get("zh") and site["ui"][k].get("en") for k in _tut))
ok("LOG-182: the English lesson has no Chinese", all(not _CJK.search(site["ui"][k]["en"]) for k in _tut))
ok("LOG-182: the lesson never spoils (不劇透)", not re.search(r"彩蛋|EE_|easter|secret|隱藏|字母門|letter door|tube|cloud|mask", json.dumps({k: site["ui"][k] for k in _tut}, ensure_ascii=False), re.I))
_js182 = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8")
ok("LOG-182: os.js carries the simple stage, the lesson, the question and the head switch", all(t in _js182 for t in ("sec_level", "tut-bub", "tut-ask", "makeTut(", "opts.mode === 'simple'", "st-level", "function buildLine()", "function lineOk(")))
ok("LOG-182: the locked stage swallows keys too (and the simple stage has no letters)", "function key(k) {\n      if (locked || SIMPLE) return;" in _js182)
_css182 = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")
ok("LOG-182: os.css styles the bubble, the question, the spent tile, the | and the float", all(t in _css182 for t in (".tut-bub", ".tut-ask", ".seg.done", ".ds-secs.locked", ".zsep", "@keyframes tfloat", ".seg.tile.movable", ".st-level")))
ok("LOG-182 追記⑤: five zone labels for the line", all(k in site["ui"] for k in ("sec_zone_intro", "sec_zone_pre", "sec_zone_gate", "sec_zone_post", "sec_zone_end")))
_segs182 = json.loads((ROOT / "demos" / "interactive-player" / "segments.json").read_text(encoding="utf-8"))["themes"][0]
_tu = _segs182.get("tutorial", {})
ok("LOG-182 追記③: the opening loop's three files are declared and present", all(k in _tu and (ROOT / "demos" / "interactive-player" / _tu[k]["file"]).exists() for k in ("hold", "drums", "release")))
_bar = 60 / 145 * 4
_logical = lambda k: _tu[k]["durationSec"] - _tu[k]["preBars"] * _bar - _tu[k]["tailBars"] * _bar
ok("LOG-182 追記④: a hold pass is the opening's two beats + eight bars (no seam pick-up), drums and release are eight bars", _tu and abs(_logical("hold") - 8.5 * _bar) < 1e-3 and _tu["hold"]["preBars"] == 0 and abs(_logical("drums") - 8 * _bar) < 1e-3 and abs(_logical("release") - 8 * _bar) < 1e-3)
ok("LOG-182 追記④: the drum loop enters after the opening's two beats and ends with the pass", _tu and abs(_tu["drums"]["offsetBars"] * _bar + _logical("drums") - _logical("hold")) < 1e-3)
ok("LOG-182 追記⑥: later passes of the hold skip the opening beats and are exactly eight bars", _tu and abs(_logical("hold") - _tu["hold"]["openBars"] * _bar - 8 * _bar) < 1e-3 and _tu["hold"]["openBars"] == _tu["drums"]["offsetBars"])
ok("LOG-182 追記③: the two halves read as A and follow A's rules", _tu and all(_tu[k].get("letter") == "A" and _tu[k].get("alias") == "A" and _tu[k].get("bookend") is True for k in ("hold", "release")))
ok("LOG-182: the bubble keeps its own fade past the shell sweep", ".desktop .stage-ui .tut-bub{transition:" in _css182)


# ---- LOG-183 the lesson rewritten on the user's script (教學腳本草稿.md)
_s183 = ("tut_open1", "tut_open2", "tut_s1a", "tut_s1a2", "tut_s1b", "tut_s2a", "tut_s2b", "tut_s2b2", "tut_s2c",
         "tut_sd1", "tut_sd2", "tut_sd3", "tut_sd4", "tut_sd5", "tut_sd6", "tut_sd7", "tut_sd8", "tut_sd9", "tut_sd10", "tut_sd11", "tut_sd12", "tut_sd13", "tut_sd14", "tut_sd15",   # LOG-190 追記⑲＋⑳: the demonstration's new script
         "tut_s9e", "tut_s9g", "tut_s_turn",   # 追記⑲: tut_s9a / s9b / s9d / s9f / s9h retired (tut_s9c went at 追記⑪)
         "tut_s13a", "tut_s13b", "tut_s_v1", "tut_s_v2", "tut_s_v3", "tut_s_v4", "tut_s_v5", "tut_s_v6", "tut_s_v7",   # LOG-190 追記⑬: the closing V1-V6 show replaces tut_s_end / tut_s_end2
         "tut_end", "tut_again", "tut_clip_song", "tut_clip_parts")
ok("LOG-183: every line of the new script is there, bilingual, English free of Chinese",
   all(isinstance(site["ui"].get(k), dict) and site["ui"][k].get("zh") and site["ui"][k].get("en") and not _CJK.search(site["ui"][k]["en"]) for k in _s183),
   str([k for k in _s183 if k not in site["ui"]]))
ok("LOG-183: the superseded lines are gone (no orphan strings)",
   not any(k in site["ui"] for k in ("tut_skip", "tut_s_song", "tut_s_zones", "tut_s_go", "tut_s_yours")))
_used183 = _js182  # os.js, already read above
# what the lesson can actually put on screen: U.<key>, and the keys handed to show / subtitle / once / relay
_shown183 = set(re.findall(r"U\.(tut_[a-z0-9_]+)", _used183)) | set(re.findall(r"(?:show|subtitle|once|sayOn)\('(tut_[a-z0-9_]+)'", _used183)) | set(re.findall(r"sayBefore\([^,']*, '(tut_[a-z0-9_]+)'", _used183))   # LOG-190 追記⑲: sayOn hands its key to show (sayBySeam retired); 追記⑳: sayBefore(lead, key, node) too
for _rl in re.findall(r"(?:relay|spread)\(\[([^\]]*)\]", _used183):   # 追記⑲: spread() puts a leg's keys up in turn
    _shown183 |= set(re.findall(r"'(tut_[a-z0-9_]+)'", _rl))
ok("LOG-183: every tut_* string os.js can show actually exists in site.json",
   all(k in site["ui"] for k in sorted(_shown183)), str(sorted(k for k in _shown183 if k not in site["ui"])))
ok("LOG-183: and every tut_s* string in site.json is one os.js can show (no dead lines)",
   all(k in _shown183 for k in site["ui"] if k.startswith("tut_s")),
   str([k for k in site["ui"] if k.startswith("tut_s") and k not in _shown183]))
ok("LOG-183: the concept clip names nine sections", len(str(site["ui"]["tut_clip_parts"]["en"]).split("|")) == 9)
ok("LOG-183: the lesson runs on the AUDIO clock, not a setTimeout",
   all(t in _used183 for t in ("function makeTut(E, onDone)", "E.now()", "function T() { return t0 == null ? 0 : E.now() - t0; }", "frozen: function () { return !!paused; }")))
ok("LOG-183: the demonstration has its own schedule, and it can be forgotten afterwards",
   all(t in _used183 for t in ("var forceId = null;", "force: function (id)", "tutReset: function ()", "E.force('B1')", "E.force('A2')", "E.force('C1')", "E.force('A1')")))   # LOG-186: E.force('C2') is gone - C2 left the demonstration
ok("LOG-183: the press on A releases the loop even after this pass's decision was taken",
   "isHold(cur.seg) && isHold(nxt.seg) && ctx.currentTime < nxt.start - 0.2" in _used183)
ok("LOG-183: pinning the bubble's width adds the padding back (max-content is a CONTENT size even under border-box)",
   "cs.boxSizing === 'border-box' ? pad : 0" in _used183)
ok("LOG-183: the level and the 'seen' flag live in localStorage now",
   "localStorage.getItem('sec_level')" in _used183 and "localStorage.getItem('tut_seen')" in _used183 and "sessionStorage.getItem('sec_level')" not in _used183)
ok("LOG-183: the head row carries 結束教學 / 再看一次教學, and the in-bubble skip is gone",
   all(t in _used183 for t in ("st-again", "st-end", "U.tut_end", "U.tut_again", "function headBtns()")) and "tut-skip" not in _used183 and "tut-skip" not in _css182)
ok("LOG-183: the wish pool lends its hand to the stage (say) and guards the phone",
   "function say(t, cls)" in _used183 and "function say(text, o) {" in _used183 and "if (PHONE || !text) return null;" in _used183 and "say: say," in _used183)
ok("LOG-183 追記②: os.css has the white frames, the iris, the arrow wave, the clip, the graph and the freeze",
   all(t in _css182 for t in (".tut-frame", ".tut-frame.pulse", ".tut-iris", ".arrows.awave .col::after", ".tut-away", ".tut-clip", ".tut-clip .bar i svg path", ".tut-graph", ".tut-press", "animation-play-state:paused!important", ".wm.wsay")))
ok("LOG-183 追記② (the user: 不要三角形聚光燈、不要黃色高光、不要按鈕中央光暈): the spotlight, its cone, the lit glow and the dimming are gone",
   not any(t in _css182 for t in (".tut-spot", ".cone", ".tut-lit", ".tut-dim", "tutlit")) and not any(t in _js182 for t in ("tut-spot", "tut-lit", "tut-dim", "coneEl", "function focus(target, o)")))
ok("LOG-183 追記② (the user: 高亮框用白色不要用黃色): the frame's default is white and the graph's lit route is white",
   "var(--fc,rgba(255,255,255,.92))" in _css182 and ".tut-graph.lit-b .nb,.desktop .stage-ui .tut-graph.lit-c .nc{box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.92)" in _css182   # LOG-189: the queued route no longer stays lit (its twin blinks / wipes); the node ring is what .lit-* keeps
   and ".tut-graph.lit-b .rb" not in _css182)
ok("LOG-183 追記③ (the user: 網站不准出現無 transition 的瞬間變化): every lesson rule that declares a transition out-specifies the shell sweep (three classes)",
   all(t in _css182 for t in (".desktop .stage-ui .tut-frame{", ".desktop .stage-ui .tut-frame.glide{", ".desktop .stage-ui .tut-iris{", ".desktop .stage-ui .tut-clip .bar i{", ".desktop .stage-ui .tut-graph .rt{", ".desktop .stage-ui .tut-graph .nd{", ".desktop .stage-ui .tut-graph .nd.now{"))
   and not re.search(r"\n\.stage-ui \.tut-(frame|iris|clip|graph)[^{]*\{[^}]*transition:", _css182))
ok("LOG-183 追記③: the rings ride the tiles in fractional pixels, glide to E and take its colour, the clip splits on its own sentence, both halves shuffle together",
   all(t in _js182 for t in (".toFixed(2) + 'px'", "function reframe(f, target, o)", "function dropFrame(f)", "show('tut_s1a2', clipBox); clipSplit();",
                             "show('tut_s2c', E.surface()); E.conceal('zones'); E.cover('A'); E.cover('E'); E.cover('I'); });", "el.getBoundingClientRect();   /* 追記③"))   # LOG-189: the A / I ring race is gone (the tiles move). LOG-190: the two zone rings are gone too - s2c hides A / E / I and the zone marks instead
   and "tut_s_swap_post" not in _js182 and "tut_s_swap_post" not in site["ui"])
ok("LOG-183 追記③: the greeting keeps only the row's box clear, the asides keep the bubble's reserve, and an authored line breaks only where the author broke it",
   "subtitle('tut_open1', { greet: true })" in _js182 and "o.greet ? 8 : null" in _js182 and ".wm.wsay .wq{white-space:pre-line}" in _css182
   and site["ui"]["tut_open2"]["zh"] == "現在開始概念及使用教學。")   # LOG-188: the long two-line opener is gone; the rule (author breaks the line) stays
ok("LOG-183 追記③ (the user: 切塊標籤要中文): the nine section names are Chinese in zh, and the track name is a header to the left of the region",
   len(site["ui"]["tut_clip_parts"]["zh"].split("|")) == 9 and _CJK.search(site["ui"]["tut_clip_parts"]["zh"]) is not None
   and ".desktop .stage-ui .tut-clip .cap{position:absolute;right:100%" in _css182 and "clipEl.style.setProperty('--bt'" in _js182)
ok("LOG-183 追記④ (the user: 一區一個獨立泡泡隨白框一起往右移動): step 2 is a heading plus five zone names, one ring and one sliding bubble walk them",
   "|" not in site["ui"]["tut_s2a"]["zh"] and len(site["ui"]["tut_s2z"]["zh"].split("|")) == 5 and len(site["ui"]["tut_s2z"]["en"].split("|")) == 5
   and "show('tut_s2z', tgt, { piece: k, instant: true, slide: true, again: true });" in _js182 and "reframe(fZ, tgt, { dur: 0.34, pad: tile ? 5 : 8" in _js182   # LOG-190: the first piece slides too
   and "if (o.piece != null) { text = pieces[o.piece] || ''; pieces = [text]; }" in _js182 and "bub.classList.toggle('slide', !!o.slide);" in _js182)
ok("LOG-183 追記②: no other lesson line carries a stray '|'",
   not any("|" in site["ui"][k].get("zh", "") for k in _s183 if k not in ("tut_s2a", "tut_clip_parts", "tut_s_v2", "tut_s_v5", "tut_s_pair")))   # LOG-190 追記⑬: v2 / v5 carry the split marks (V3 / V6); 追記⑯: tut_s_pair marks where the twins come out
ok("LOG-190 追記㉒ (the user: 回到A時有一個泡泡，那個泡泡從顏文字上出現，說完顏文字才消失 / 換好了這句話的泡泡永遠指向用戶拉動的那個方塊): the face that rides home to A stays (KAO_TALK) and 「也可以正常地返回 A。」 hangs above it, then face and bubble go together and A's lines follow on A; the reorder event names the dragged tile and tut_s_moved follows that tile live",
   "KAO_TALK" not in _js182 and "function kaoBox() { return kao ? rectOf(kao.el) : node('a')(); }" in _js182   # 追記㉔: the face no longer goes after its line - it stands, and every line is on it
   and "sayOn('tut_sd7', 'a', { fresh: true, then: function () {" in _js182   # LOG-190 追記㉛: sd8 / sd9 now hang off sd7 actually going up, spaced by bubLen() and "soon(1.7, function () { kaoOff(); bubOut(); });" not in _js182
   and "function reorder(zone, order, silent, moved) {" in _js182 and "emit('reorder', { zone: zone, order: order.slice(), moved: moved || null });" in _js182
   and "reorder(d.zone, o, false, d.g);" in _js182
   and "var mv = i.moved || i.order[0]; show('tut_s_moved', function () { return rectOf(E.btn(mv)) || rowBox(); }, { hold: 6, again: true });" in _js182
   and "show('tut_s_moved', E.btn(i.order[0])" not in _js182)
ok("LOG-190 追記㉑ (the user: A-B, A-C顏文字改在箭頭下方且不要壓到按鈕): outward the face hangs KAO_GAP under the stroke with its box held between A's right edge and the far node's left edge (8 px clear); homeward it still rides 22 px above the spark",
   "var KAO_GAP = 10;" in _js182 and "function underLine(key, sz) {" in _js182
   and "var x0 = a.right + 8 + w / 2, x1 = b.left - 8 - w / 2;" in _js182 and "return { x: x, y: p0.y + (p1.y - p0.y) * u + KAO_GAP + h / 2 };" in _js182
   and "atSeam(ARC_LEAD, function () { bubOut(); kaoRide(key === 'lc' ? KAO_RUN_C : null, kaoOutLegs(key)); });" in _js182 and "hop(kaoDock(kaoSlot), kaoDock(to), false), d: ARC_LEAD, main: true" in _js182   # 追記㉖: one straight segment on the line's own clock; underLine now only places B's dock (the line's end)
   and "if (ride === 'acb') q.x += kaoWH().w / 2 + 8; else q.y -= 22; return q;" in _js182 and "translateY(-22px)'" not in _js182 and "kao.dy" not in _js182)   # 追記㉔: the offset lives in sparkPath itself (above the spark home to A, right of it round to B)
ok("LOG-190 追記⑲＋⑳ (the user: 泡泡放在目前播放的那格上 / 所有ABC滑動泡泡特效改成顏文字跟著滑動後消失 / 泡泡淡出，於B上方淡入 / 出發前瞬間出現，抵達後一秒瞬間消失): every demonstration line hangs on the sounding node (above B), the first on a node fades in; a hand-over fades the line (.qout) and sends a kaomoji along the light - one-way faces only their own way, the pair turning round on landing, never the same twice running - gone 1 s after it lands; the bubble no longer rides",
   "show('tut_s9e', graphBox)" not in _js182 and "show('tut_s9g', graphBox)" not in _js182 and "show('tut_s9a', graphBox)" not in _js182
   and "function rideLine(" not in _js182 and "function rideSpark(" not in _js182 and "function spread(" not in _js182
   and "o = o || {}; o.slide = false; o.align = 'right'; if (which === 'b') o.gap = 32; if (!o.at) o.at = kaoBox;" in _js182   # 追記㉘: the side is read in run (below the face when it stands below A)   # 追記㉕: no .slide on the face - a widening right-aligned bubble mid-transition swept across B   # 追記㉔→㉕: on the face, opening to the left
   and "function bubOut() { if (!bub) return; bub.classList.add('qout'); hide(); bubTgt = null; bubSrc = null; }" in _js182 and "bub.classList.remove('qout');" in _js182
   and "function kaoPick(" not in _js182 and "kaoLast" not in _js182 and "KAO_LR" not in _js182 and "KAO_PAIR = [" not in _js182   # 追記㉗ (the user: 取消隨機顏文字): no picking, no "never twice running" - every line names its face (see the 追記㉗ ok below)
   and "if (!kao.landed && (i > kao.turnI || (i === kao.turnI && k >= 1))) { kao.landed = true; kao.el.textContent = kao.b; }" in _js182 and "kao.stay" not in _js182   # 追記㉔: it lands at the main leg's end and never times out   # 追記㉙: at the turn leg's end (= the main leg's when none is marked)
   and "el.style.setProperty('transition', 'none', 'important');" in _js182 and "irisStep(); sparkStep(); kaoStep();" in _js182 and "unlock(); kaoOff();" in _js182
   and "goOut('lb', 'lr');" in _js182 and "goOut('lc', 'lr');" in _js182 and "sparkAtSeam('acb', 'b', 'cb')" in _js182 and _js182.count("sparkAtSeam('ab', 'a', 'rl'") == 2
   and "var L = face && face.off ? kaoOffLegs() : face ? kaoCarryLegs(ride, land) : kaoHomeLegs(ride, land); at(Math.max(T(), seam - 0.45 - L.lead), function () { if (L.off) bubFree = true; else bubOut(); kaoRide(face || null, L); });" in _js182   # 追記㉙: the pair carries the spark; round 5: the last ride runs off the stage
   and "var q = svgPt(p.getPointAtLength(len * (1 - Math.pow(1 - k, 2.2))));" in _js182 and "k = 1 - Math.pow(1 - k, 2.2);" in _js182.split("function sparkStep() {", 1)[1][:400]   # the kaomoji and the spark on ONE curve - change both together
   and "sayOn('tut_sd1', 'a', { fresh: true });" in _js182 and "sayBefore(ARC_LEAD + 2.4, 'tut_sd2', 'a');" in _js182
   and "soon(3.0, function () { sayOn('tut_sd4', 'b'); }); sayBefore(0.45 + 7.2, 'tut_sd5', 'b'); sayBefore(0.45 + 4.0, 'tut_sd6', 'b');" in _js182   # 追記㉙: sd3 3.0 / sd4 2.6 / sd5 3.2 / sd6 4.0 s (were 3.5 / 5.9 / 1.7 / 1.7)
   and "soon(Math.max(2.0, bubLen('tut_sd7') + 0.55), function () {" in _js182 and "soon(Math.max(1.4, bubLen('tut_sd8') + 0.55), function () { sayOn('tut_sd9', 'a'); });" in _js182   # LOG-190 追記㉛ (使用者: 有一句英文根本還沒顯示完就切下一句了): spaced by the line's own typing length, floored at the old Chinese spacing
   and ".desktop .stage-ui .tut-bub.qout{transition:opacity .25s ease,transform .25s ease}" in _css182 and ".desktop .stage-ui .tut-kao{position:absolute;" in _css182
   and not any("|" in site["ui"][k]["zh"] or "|" in site["ui"][k]["en"] for k in ("tut_sd1", "tut_sd2"))
   and site["ui"]["tut_sd4"]["zh"].startswith("蛤") and "基本範例" in site["ui"]["tut_sd5"]["zh"]   # 追記㉗: the user's rewrite of sd4 / sd5 (我猜 / 地方 retired)
   and "tx.textContent = txt(curKey).replace(/\\|/g, '');" in _js182)
ok("LOG-183 追記④: the rings turn E's colour on the way (early), the arrows wave three times and the tiles wave with them, the tile that sounds fills with its colour",
   "if (o.color && o.early != null) soon(o.early" in _js182   # LOG-189: the early-colour reframe call itself is gone with the ring race; the helper stays
   and "for (var k = 0; k < 3; k++)" in _js182 and ".ds-secs.simple.arrows.awave .col .seg.tile{animation:tuttile" in _css182
   and "t.style.setProperty('--c', isCur ? segColor(cur.seg) :" in _js182 and ".ds-secs.simple .seg.tile.playing{background:color-mix" not in _css182)
ok("LOG-183 追記④ (the user: 先講、再動，兩區交錯): steps 7/8 speak first and the two halves move on different beats, in opposite orders",
   all(t in _js182 for t in ("at(L1 + 56.4, function () { E.move('pre', 'D', 0); });", "at(L1 + 57.0, function () { E.move('post', 'H', 0); });",
                             "at(L1 + 59.2, function () { E.move('post', 'H', 2); });", "at(L1 + 59.8, function () { E.move('pre', 'D', 2); });")))   # LOG-189: the swap demonstration sits after the tick, a pass earlier. LOG-190 追記④ pulled it 1.2 s forward; 追記⑨ gave the full length back (the CUT, not demoStart, is what has to be in the first half)
ok("LOG-183 追記④: the graph has 56 px nodes on a 360 x 190 board, a spark that lights the node it lands on, and two return arcs that blink then wipe",
   all(t in _js182 for t in ("viewBox=\"0 0 360 190\"", "class=\"arc ab\" pathLength=\"1\"", "class=\"arc ac\" pathLength=\"1\"", "<circle class=\"spk\"",
                             "function sparkTo(ride, then)", "function sparkAtSeam(ride, land, dir, face)","sparkAtSeam('ab', 'a', 'rl', KAO_RUN_OFF)", "sparkAtSeam('acb', 'b', 'cb')", "goOut('lc', 'lr')",
                             "function arcPre(key)", "function arcGo(key)", "arcPre('lb')", "arcPre('lc')", "goOut('lb', 'lr')", "arcGoAtSeam(key);", "irisStep(); sparkStep();"))   # LOG-190 追記⑳: goOut = arcGoAtSeam + the kaomoji riding the line   # LOG-186: the C arc is now C -> B ('cb'); the C -> A arc is drawn but never lit. LOG-189: swapped - the spark rides the return arcs, the blink / wipe belongs to the routes' twins
   and all(t in _css182 for t in (".desktop .stage-ui .tut-graph .nd{position:absolute;width:56px;height:56px", ".desktop .stage-ui .tut-graph .spk{", ".desktop .stage-ui .tut-graph .arc{",
                                  "@keyframes tutarcpre", "@keyframes tutarcgo", "stroke-dashoffset:1;")))   # LOG-190: the wipe runs the other way - drawn from A (1 -> 0), no longer erased (0 -> -1)
ok("LOG-183 追記④ (the user: 換你了改用 iris 只留 A 亮，太久沒點出四句催趕): the hand-over closes the iris on A, the press opens it, four nudges in order, 13b points at the second half",
   "irisIn(E.btn('A'), 48)" in _js182 and "unframeAll(); irisOut();" in _js182
   and "var nudges = lang === 'zh' ? ['tut_nudge1', 'tut_nudge2', 'tut_nudge3', 'tut_nudge4'] : ['tut_nudge1', 'tut_nudge2', 'tut_nudge3'];" in _js182   # LOG-190 追記㉚: the film quote is Chinese-only; English goes to the tantrum one line earlier
   and "nudges.forEach(function (k, n) { soon(1.3 + 11 * (n + 1), function () { if (phase === 'hand') show(k, E.btn('A'), { again: true }); }); });" in _js182
   and all(isinstance(site["ui"].get(k), dict) and site["ui"][k].get("zh") and site["ui"][k].get("en") and not _CJK.search(site["ui"][k]["en"]) for k in ("tut_nudge1", "tut_nudge2", "tut_nudge3", "tut_nudge4"))
   and "讓子彈飛" in site["ui"]["tut_nudge4"]["zh"] and "show('tut_s13b', zoneBox('post'), { hold: 14 });" in _js182)
ok("LOG-190 追記30/31 (使用者: 一開始就站在下面、抖動小一點點、丟出去時顏文字站在原地、A 不要回來；4/5 走掉直接開播、1/5 倒帶回按 A): the tantrum, its two endings and the draw between them",   # the name stays cp950-printable: no circled numbers, no kaomoji
   "var KAO_RAGE = ['(#`皿´)', '( ╬ﾟ дﾟ )', '(ﾒ ﾟ皿ﾟ)ﾒ', 'ヽ(#`Д´)ﾉ'];" in _js182
   and "var KAO_WALK = '(ﾟ皿ﾟﾒ)';" in _js182 and "var KAO_REW = [{ f: '( ´ﾟДﾟ`)', d: 2.0 }, { f: '(`へ´≠)', d: 4.0, pant: true }, { f: '<(￣ ﹌ ￣)>', d: 2.0 }, { f: '(´･_･`)', d: 2.0 }];" in _js182
   and "soon(1.3 + 11 * (nudges.length + 1), rageStart);" in _js182
   and all(t in _js182 for t in ("function rageAt() {", "function ragePt() {", "function rageStart(force) {", "function rageThrow() {",
                                 "function rageEggLegs() {", "function rageRewLegs() {", "function rageRewind() {", "function rewSfx() {", "function aBack() {", "function rageEnd() {",
                                 "mode: force || (Math.random() < REW_ODDS ? 'rewind' : 'egg')",   # the draw - forceable by the ?debug handle and the probe
                                 "kaoSlot = 'ab'; kao.home = ragePt; kao.landed = true; kaoStep();",   # 使用者: 一開始就站在下面 - and the shake rides kaoStep's per-frame home(), not CSS (.tut-kao is transition:none!important)
                                 "amp = 1.0 + 5.5 * k * k;",                                            # 使用者: 抖動小一點點
                                 "rage.frozen = rageAt(); rage.thrown = T();",                          # 使用者: 丟出去時顏文字站在原地
                                 "a.classList.add('tut-gone'); aGone = true;",                          # 使用者: A 不要回來
                                 "a.style.transform = 'translate(' + dx.toFixed(0) + 'px,' + (-dy).toFixed(0) + 'px) rotate(540deg) scale(.35)';",
                                 "rage = null; kaoOff();   /* off the left edge, gone */",
                                 "if (rage) { rage.pant = 0; rage.walk = T(); kaoFace(KAO_WALK); }",     # 喘氣 -> 往左走掉
                                 "if (phase === 'hand') show('tut_s_turn', E.btn('A'), { again: true });",  # 倒帶結局: 回到「按 A」等使用者自己按
                                 "aBack(); });   /* 追記⑰",                                              # V6 那句時 A 偷偷回來
                                 "if (rage) rageEnd();"))
   and "REW_ODDS = 0," in _js182 and "tt_rwd" not in _js182   # LOG-190 追記32-② (使用者: 取消 rewind 那條的觸發，先都用原版的): the odds are off and the EE_ key is gone - only the ?debug handle reaches the rewind now
   and ".desktop .ds-secs .seg.tile.tut-gone{visibility:hidden}" in _css182 and ".desktop .stage-ui .tut-rewfx{" in _css182 and "@keyframes tutrew{" in _css182 and "@keyframes tutrewband{" in _css182
   and site["ui"]["tut_s_turn"]["en"] == "Your turn: press A to start.")   # 使用者: your turn, press a to set of 改成 press a to start
ok("LOG-183 追記④ as rewritten by LOG-190 追記⑲: the demonstration opens on A (我們先從 A 開頭開始) and its superseded lines are gone",
   site["ui"]["tut_sd1"]["zh"].startswith("我們先從 A 開頭開始") and "C2 開播時就繼續教學" not in _js182
   and not any(k in site["ui"] for k in ("tut_s9a", "tut_s9b", "tut_s9c", "tut_s9d", "tut_s9f", "tut_s9h")))
ok("LOG-183 追記② (the user: 鼓可以從這句就開始慢慢淡入): the drums get a slow time constant from step 4",
   "function drumLayer(on, tc)" in _js182 and "E.layer(true, 3.0)" in _js182 and "(tc || 0.7)" in _js182)
ok("LOG-183 追記② (the user: 刻度那邊改成陰影留一圈): the iris closes on the tick and opens out again",
   all(t in _js182 for t in ("irisIn(tickRect, 46)", "function irisOut()", "irisRadius()", "--r")))
ok("LOG-183: the big dark shadow under the order row is gone", ".ds-secs.simple::before" not in _css182)
ok("LOG-183 (the user: 圖示絕對不能與舞台按鈕重疊): the icon column steps aside while a .dstage is up",
   ".desktop.dstage-up>.icons{" in _css182 and "HOST.classList.add('dstage-up')" in _used183 and "HOST.classList.remove('dstage-up')" in _used183)
ok("LOG-183: the new lines never spoil either (不劇透)",
   not re.search(r"彩蛋|EE_|easter|secret|隱藏|字母門|letter door|tube|cloud|mask", json.dumps({k: site["ui"][k] for k in _s183}, ensure_ascii=False), re.I))


# ---- LOG-183 追記① (the owner: 教學文字跑到畫面最上方，只看得到一半). `.ds-panel` sits at 8vh and the order row is its first
# thing, so the row's top is 71 px at the owner's 1920x889 (58 at 1280x720) and the menubar owns the first 30 of those.
# There is NO sky above the row: nothing the lesson draws may be anchored there.
ok("LOG-183 追記①: the lesson measures the menubar, the line and the bubble reserve instead of guessing a ratio",
   all(t in _used183 for t in ("function menuBottom()", "function safeTop()", "function lineY()", "function clampY(", "function asideTop(", "TUT_BUB_RESERVE")))
ok("LOG-183 追記①: the author's asides sit BELOW the row, in the band up to the spectrum line",
   "var top = r.bottom + (res == null ? TUT_BUB_RESERVE : res);" in _used183 and "top = Math.min(top, ly - 14 - h)" in _used183
   and "bottom: r.top - 26" not in _used183)
ok("LOG-183 追記①: every bubble is clamped under the menubar", "bub.style.top = clampY(" in _used183)
ok("LOG-183 追記①: the clip and the graph are laid out from the row's own box, and re-laid on a resize",
   all(t in _used183 for t in ("function clipLay()", "function graphLay()", "function relayout()", "clipEl.style.height = Math.round(r.bottom - r.top)")))
ok("LOG-183 追記①: well.say takes a placement callback, so a line can be placed once its height is known",
   "if (typeof o.place === 'function')" in _used183 and "move: function (x, y)" in _used183)
ok("LOG-183 追記①/③: the clip's caption is a track header LEFT of the bar (never above the row) and the section names are inside the blocks",
   ".desktop .stage-ui .tut-clip .cap{position:absolute;right:100%" in _css182
   and ".desktop .stage-ui .tut-clip .bar{position:absolute" in _css182
   and "top:calc(100% + 6px)" not in _css182)


# ---- LOG-184 three audio changes to the simple stage (the user, 2026-09-12)
# (1) the opening loop has two renders: `firstFile` for the pass heard from the top, `file` for every later pass
# (2) a seam stem sounds across B1 -> A (its beat 4 lands ON the seam)
# (3) on the line every ORDER is legal - only the VERSION is still the allow table's call
_ADIR = ROOT / "demos" / "interactive-player"
ok("LOG-184: the hold declares both renders and both files are on disk",
   "firstFile" in _tu["hold"] and (_ADIR / _tu["hold"]["file"]).exists() and (_ADIR / _tu["hold"]["firstFile"]).exists()
   and _tu["hold"]["file"] != _tu["hold"]["firstFile"])
ok("LOG-184: both renders keep the Theme_段落_pickup_tail_info naming and the same grid (one set of bpm / trimStart / durationSec covers both)",
   all(Path(_tu["hold"][k]).name.startswith("ThemeA_A1_0.5_3_NoDrumLoop") for k in ("file", "firstFile"))
   and (_ADIR / _tu["hold"]["file"]).stat().st_size > 0 and (_ADIR / _tu["hold"]["firstFile"]).stat().st_size > 0)
ok("LOG-184: the engine keys the two renders on their own buffers and gates the start on BOTH",
   "function bufKey(seg) { return TH.id + ':' + (seg.bufId || seg.id); }" in _js182
   and "function holdSplit()" in _js182 and "H1.bufId = HOLD.id + '_1st'" in _js182
   and "var urgent = [first]; if (holdSplit() && urgent.indexOf(HOLDL) < 0) urgent.push(HOLDL);" in _js182)
_tr184 = (_segs182.get("transitions") or [])
_lay = _segs182.get("seamLayers") or {}
ok("LOG-184 (as LOG-185 keeps it): the B1 -> A seam still calls the synth stem, now as the TR layer of the pair's rule, and the file is present",
   any(t["from"] == "B1" and t["to"] == "A" and "TR" in t.get("layers", []) for t in _tr184) and (_ADIR / _lay["TR"]["file"]).exists())
ok("LOG-184: the stem's pick-up is one bar at 145, so its beat 4 lands on the seam (seam - 1.6552 s)",
   _lay["TR"]["preBars"] == 1 and _lay["TR"]["bpmIn"] == 145 and abs(_lay["TR"]["preBars"] * (60 / _lay["TR"]["bpmIn"] * 4) - 1.65517) < 1e-3
   and "trimStart" not in _lay["TR"])   # encoded in the same batch as ThemeA_B1_1_1_none.mp3, which carries none either
ok("LOG-184/185: the layers are armed on the audio clock at the decision, through the sections' own output, and taken back with the next section",
   all(t in _js182 for t in ("function ruleFor(", "function drawRule(", "function armStem(", "function dropStem()",
                             "function schedNext(seg) { var r = drawRule(cur.seg, seg); nxt = schedule(seg, cur.end, r); armStem(cur.seg, seg, cur.end, r); return nxt; }",
                             "src.start(at, trim);", "g.connect(master);"))
   and "dropStem();   /* LOG-184: the stem was armed for THIS seam" in _js182
   and not re.search(r"setTimeout\([^)]*armStem", _js182))
ok("LOG-184: every decision path schedules through schedNext (no raw nxt = schedule in decide)",
   _js182.count("nxt = schedule(") == 1 and _js182.count("schedNext(") == 7)   # the one raw call IS schedNext's own body; 1 definition + 6 decision paths
ok("LOG-184/185: the rule lookup is generic on the pair - no B1 or A hard-coded in the engine",
   "t.to === toSeg.id || (L && t.to === L)" in _js182 and "TRANS = (th.transitions || [])" in _js182 and "LAYERS = {}; var SL = th.seamLayers || {};" in _js182
   and not re.search(r"=== '(B1|C1|C2|D1|D2|G1)'", _js182[_js182.index("function ruleFor("):_js182.index("function dropStem()")]))


# ---- LOG-185 / ADR-012 the seam layers: one rule per (from, to) pair, generated from the lab's final checklist
_sids = {sg["id"] for sg in _segs182["segments"]} | {"A", "I"}
_post = ["E2", "F1", "F2", "G1", "G2", "H1", "H2", "I_loop"]
ok("LOG-185: the four seam layers are declared and every file is on disk",
   set(_lay) == {"TR", "DRUM", "HIT1", "G1PRE"} and all((_ADIR / L["file"]).is_file() and (_ADIR / L["file"]).stat().st_size > 0 for L in _lay.values()))
ok("LOG-185: TR / DRUM / HIT1 start one bar @145 before the seam, G1PRE one bar @172 (each carries its own pick-up)",
   all(_lay[k]["preBars"] == 1 and _lay[k]["bpmIn"] == 145 for k in ("TR", "DRUM", "HIT1")) and _lay["G1PRE"]["preBars"] == 1 and _lay["G1PRE"]["bpmIn"] == 172)
ok("LOG-185: the untagged exports carry their measured trimStart (HIT1 = the 25 ms rule, G1PRE = the 27.2 ms cross-correlation), the two Info-tagged stems none",
   abs(_lay["HIT1"]["trimStart"] - 0.025057) < 1e-6 and abs(_lay["G1PRE"]["trimStart"] - 0.027234) < 1e-6 and "trimStart" not in _lay["TR"] and "trimStart" not in _lay["DRUM"])
ok("LOG-185: G1PRE is post-zone only (from E2 on) and the engine honours onlyFrom",
   _lay["G1PRE"].get("onlyFrom") == _post and "if (Ly.onlyFrom && Ly.onlyFrom.indexOf(fromSeg.id) < 0) return;" in _js182)
ok("LOG-185: TR lists its three note onsets (the third IS the seam: 1.636 s = 1 bar @145 - 19 ms attack)",
   _lay["TR"].get("notes") == [0.808, 1.222, 1.636] and abs(_lay["TR"]["notes"][2] - 1.65517) < 0.03)
_rules = _tr184
_pairs = [(t["from"], t["to"]) for t in _rules]
ok("LOG-187: 39 rules (the 22:41 plan, 40 marked rows - B2>A / B2>E1 / D1>A dropped by the user; LOG-188 追記① 「A2-C1不需要transition」 empties A>C1), one per pair, every end a section id / A / I", len(_rules) == 39 and len(set(_pairs)) == 39 and all(f in _sids and t in _sids for f, t in _pairs))
def _vars(t): return [t] if "random" not in t else t["random"]
ok("LOG-185: every layer a rule (or a variant) names exists; every random rule has >= 2 variants and no plain layers of its own",
   all(all(k in _lay for k in v.get("layers", [])) for t in _rules for v in _vars(t))
   and all(len(t["random"]) >= 2 and "layers" not in t and "noPre" not in t and "noTail" not in t for t in _rules if "random" in t))
ok("LOG-185: G1PRE appears only in rules leaving the post zone",
   all(t["from"] in _post for t in _rules for v in _vars(t) if "G1PRE" in v.get("layers", [])))
ok("LOG-185: every mute names a layer the rule stacks and a note the layer has",
   all(all(k in v.get("layers", []) and all(1 <= n <= len(_lay[k].get("notes", [])) for n in ns) for k, ns in v.get("mute", {}).items()) for t in _rules for v in _vars(t)))
_byp = {(t["from"], t["to"]): t for t in _rules}
ok("LOG-185: the user's readings are in the table as he wrote them (spot checks)",
   ("A", "C1") not in _byp   # LOG-188 追記① 「A2-C1不需要transition」: no layer, no flag - C1 enters with its own pick-up like any unmarked pair (the 「A到C1不+1」 reading it replaced is in the plan file)
   and _byp[("A", "C2")]["random"] == [{"layers": []}, {"layers": ["TR"]}, {"layers": ["TR", "DRUM"]}, {"layers": ["TR", "DRUM", "HIT1"]}]   # A>C2 untouched
   and _byp[("A", "D1")]["layers"] == ["TR", "DRUM"] and _byp[("A", "D1")]["mute"] == {"TR": [3]}
   and _byp[("C1", "B1")]["random"] == [{"layers": ["TR"], "mute": {"TR": [3]}, "noPre": True}, {"layers": ["TR"], "noTail": True, "mute": {"TR": [3]}, "noPre": True}]   # LOG-190 追記⑦ 「C1-B1 關pre-entry, 開TR 3」 on top of the plan's 無TAIL(RANDOM)
   and _byp[("F1", "I_loop")]["layers"] == ["HIT1"] and _byp[("F1", "I_loop")].get("noTail") is True
   and _byp[("B1", "A")]["layers"] == ["TR"] and _byp[("C1", "A")]["layers"] == ["TR"] and _byp[("D2", "A")]["layers"] == ["TR"] and all(p not in _byp for p in (("B2", "A"), ("C2", "A"), ("D1", "A")))   # 22:41 plan: X>A = TR only where ticked (「B回A不+1, C回A不+1」 still holds)
   and "random" not in _byp[("B1", "C1")] and _byp[("B1", "C1")]["layers"] == ["TR"] and _byp[("B1", "C1")].get("noTail") is True and not _byp[("B1", "C1")].get("noPre")   # LOG-188 追記① 「B1-C B要pre」: the noPre draw is gone, one variant left = plain TR + noTail
   and _byp[("I_loop", "F1")]["layers"] == ["G1PRE"] and ("G2", "I_loop") not in _byp and _byp[("F2", "I_loop")]["layers"] == ["G1PRE"]   # 0919 plan: +1 unticked on F2/G2 > I_loop and I_loop > F1
   and _byp[("H2", "G1")].get("noPre") is True and _byp[("H2", "G1")]["layers"] == [])
ok("LOG-185: schedule() / fadeOut() read the pair's noPre / noTail off the drawn rule, and the crossfade follows",
   "var pre = r.noPre ? 0 : preSec(seg), post = (cur && !r.noTail) ? postSec(cur.seg) : 0, xf = (cur && pre <= 0 && !post)" in _js182
   and "+ (r.noPre ? preSec(seg) : 0));" in _js182
   and "var r = (nxt && nxt.rule) || {}, post = r.noTail ? 0 : postSec(item.seg), xf = (!post && nxt && (r.noPre ? 0 : preSec(nxt.seg)) <= 0)" in _js182
   and "rule: rule || null };" in _js182)
ok("LOG-185: a random rule is drawn once per seam and the mute window is scheduled on the layer's own gain",
   "o.variant = t.random.indexOf(v); return o;" in _js182 and "var mute = rule.mute && rule.mute[key];" in _js182
   and "g.gain.linearRampToValueAtTime(0, a - 0.002);" in _js182)
ok("LOG-185: the decision lead covers every layer's pick-up",
   "layerList().forEach(function (Ly) { lead = Math.max(lead, (Ly.preBars || 0) * barSec(Ly.bpmIn || 120)); });" in _js182)
ok("LOG-185: the advanced allow table still has no A on the right (hand-backs to A stay the simple stage's)",
   all("A" not in v and "A1" not in v and "A2" not in v for v in _segs182["allow"].values()))
ok("LOG-184: the line takes every order (the ban test steps aside in the simple stage) and the allow table still picks the version",
   "function lineOk(zone, order) {   /* every seam in the zone" in _js182
   and re.search(r"function lineOk\(zone, order\) \{[^\n]*\n      if \(SIMPLE\) return true;", _js182) is not None
   and "function lineVer(fromId, g)" in _js182
   and "return nk === 'I' ? OUTRO : lineVer(seg.id, nk);" in _js182)
ok("LOG-184: the ADVANCED player's allow table is untouched",
   _segs182["allow"]["C2"] == ["C1", "C2", "D1", "D2", "E1", "E2"] and _segs182["allow"]["D2"] == ["B1", "B2", "D1", "D2", "E1", "E2"]
   and "F2" not in _segs182["allow"]["E2"])


# ---- LOG-186 (the user, 2026-09-12 evening): the press on A cuts into A2 mid-pass (first half); the demonstration walks A1 B1 A2 C1 B1 A1
_js186 = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8")
ok("LOG-186: the lesson's press on A enters A2 at once when the pass is in its first half, on the same eight-bar grid",
   "var RELEASE_XF_BEATS = 5;" in _js186 and "function releaseMid(beats) {" in _js186 and "function xfCurves(from)" in _js186   # LOG-190 追記⑥: 2 -> 6 beats; 追記⑦: 5; 追記⑨: the length is the caller's
   and "if ((ctx.currentTime - loopStart) / L >= 0.5) return null;" in _js186   # LOG-190 追記④: the rule moved into midHalf(), the one both callers ask
   and "var loopStart = cur.start + (cur.seg.skipBars ? 0 : (HOLD.openBars || 0) * bar);" in _js186
   and "var at = Math.max(now + 0.05, loopStart), pos = at - loopStart, xf = (beats || RELEASE_XF_BEATS) * (60 / (RELEASE.bpmIn || 120));" in _js186
   and "src.start(at, (RELEASE.trimStart || 0) + pos);" in _js186
   and "cur = { seg: RELEASE, start: loopStart, end: loopStart + L, audioStart: at, src: src, gain: g, rule: null, mid: pos };" in _js186
   and "og.setValueCurveAtTime(cv.dn, at, xf);" in _js186 and "g.gain.setValueCurveAtTime(cv.up, at, xf);" in _js186
   and "dn[i] = from * Math.cos(k); up[i] = Math.pow(Math.sin(k), 1.8);" in _js186   # equal-power cosine out, not a linear dip (the user: 我明明說 crossfading); LOG-190 追記⑥: the incoming side swells (sin^1.8)
   and "var N = 129, dn = new Float32Array(N), up = new Float32Array(N);" in _js186 and "up[i] = Math.sin(k); }" not in _js186
   and "emit('enter', { id: cur.seg.id, group: cur.seg.id, ver: 0, from: lastId, outro: false, mid: pos });" in _js186)
ok("LOG-190 追記⑥ (the user: ABC 範例那邊的 crossfading 太快了，要逐漸轉換變大聲的感覺): the A1 -> A2 crossfade is five beats (2.07 s at 145) for the VISITOR'S PRESS and A2 comes up late on sin^1.8 - one default and one curve, read in the two places that need a length (releaseMid, E.xfSec); 5 beats stays clear of the next decision point (3.56 s before the seam) even from a press at 49.9 % (L/2 = 6.62 s)",
   "var RELEASE_XF_BEATS = 5;" in _js186 and _js186.count("(beats || RELEASE_XF_BEATS) *") == 2   # releaseMid() and E.xfSec()
   and 5 * (60 / 145) + 3.5603 < 13.24138 / 2)
# ---- LOG-190 追記⑦ (the user: A/I 先亮、E 出現亮自己的顏色 A/I 變不亮 / crossfade 在那輪 20-30% 就開始、演示顯現時完全切換 / C1-B1 關 pre-entry 開 TR 3)
_XF7 = 60.9 - 54.0   # 追記⑨: the DEMONSTRATION's blend has its own length; 追記⑩: from the top of the sounding pass to demoStart (6.9 s)
_cut7 = 60.9 - _XF7
_A1_HEARD = 52.96 + 0.77   # 追記⑩: the sounding pass is heard from L1 + 53.73 (probe 190f: the cut at 56.76 entered at pos 3.03)
_A1_IN, _A1_OUT = 52.96, 66.20   # the planned sounding pass (also set again in the 追記④ block below)
_js190 = _js186; _css190 = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")   # the LOG-190 block below re-reads both; same bytes
ok("LOG-190 追記⑦ (the user: 要在切到 ABC 演示前就開始 crossfade，大概在那輪的 20-30% 就開始，直到 ABC 演示顯現出來之後完全切換) as lengthened by 追記⑨: the cut is launched E.xfSec(XF_DEMO_BEATS) = {:.2f} s before demoStart, at L1 + {:.2f} = {:.0f}% of the planned pass (the sounding pass starts ~0.76 s later, so ~23% heard), complete at demoStart; demoStart finds A2 in and sets demoLeg 1".format(_XF7, _cut7, (_cut7 - _A1_IN) / (_A1_OUT - _A1_IN) * 100),
   "xfSec: function (beats) { return (beats || RELEASE_XF_BEATS) * (60 / (RELEASE && RELEASE.bpmIn || 120)); }," in _js190
   and "var XF_FROM = 54.0;" in _js190 and "var XF_DEMO_BEATS = (60.9 - XF_FROM) / E.xfSec(1);" in _js190 and "var XF = E.xfSec(XF_DEMO_BEATS);" in _js190
   and "at(L1 + 60.9 - XF, function () { if (!E.releaseNow(XF_DEMO_BEATS)) E.force('A2'); });" in _js190
   and "at(L1 + 60.9, function () { demoStart(); E.force('B1'); });" in _js190 and "demoStart(); E.force('B1'); if (!E.releaseNow())" not in _js190
   and "if (demoLeg === 0 && E.clock().id === 'A2') demoLeg = 1;" in _js190
   and 0 < _cut7 - _A1_HEARD < 0.5 and _cut7 + _XF7 <= 60.9 + 1e-9)   # 追記⑩: just inside the top of the sounding pass, still complete at demoStart
ok("LOG-190 追記⑦ (the user: 解說 A/I 跟 E 時，A 跟 I 要先亮，E 出現時 E 亮自己的顏色然後 A/I 變不亮): E.lit toggles .lit, which wears .playing's look; s2b lights A and I, s2b2 hands the light to E, 33.4 puts it out",
   "lit: function (g, on) { var t = segBtns[g]; if (t) t.classList.toggle('lit', !!on); }," in _js190
   and ".ds-sec .seg.tile.playing,.ds-sec .seg.tile.lit{background:color-mix(in srgb,var(--c) 14%,transparent);" in _css190
   and "E.lit('A', true); E.lit('I', true);" in _js190.split("at(L1 + 26.6, function () {", 1)[1].split("at(L1 + 29.6", 1)[0]
   and "E.lit('E', true); E.lit('A', false); E.lit('I', false);" in _js190.split("at(L1 + 29.6, function () {", 1)[1].split("at(L1 + 32.0", 1)[0]
   and "at(L1 + 33.4, function () { hide(); E.lit('E', false); });" in _js190)
_segs7 = json.loads((ROOT / "demos" / "interactive-player" / "segments.json").read_text(encoding="utf-8"))
_c1b1 = [t for t in _segs7["themes"][0]["transitions"] if t.get("from") == "C1" and t.get("to") == "B1"]
ok("LOG-190 追記⑦ (the user: C1-B1 關 pre-entry, 開 TR 3): every C1 -> B1 variant carries TR with its third note muted and noPre; the noTail draw stays",
   len(_c1b1) == 1 and all(v.get("layers") == ["TR"] and v.get("mute") == {"TR": [3]} and v.get("noPre") is True for v in (_c1b1[0].get("random") or [_c1b1[0]]))
   and any(v.get("noTail") for v in (_c1b1[0].get("random") or [])) and "關pre-entry, 開TR 3" in _c1b1[0].get("amended", ""))
ok("LOG-186: only the lesson's press asks for the mid-pass cut - release(true) from pressA; free simple play and 結束教學 keep the seam path",
   "release: function (mid) {" in _js186 and "if (mid && releaseMid()) return;" in _js186
   and "E.release(true); trail.log('tut', 'press-A');" in _js186 and _js186.count("E.release(true)") == 1
   and "else if (m === 'simple') eng.release();" in _js186 and "E.tutReset(); E.release(); if (!E.holding()) E.layer(true);" in _js186)
ok("LOG-186: the mid-pass cut takes back a scheduled next turn (and its drums) and ramps the sounding pass out with its own drums",
   "var nat = nxt.start; try { nxt.src.stop(0); } catch (e) {}" in _js186
   and "drumSrcs.forEach(function (d) { try { d.stop(at + xf + 0.02); } catch (e) {} }); drumSrcs = [];" in _js186
   and "try { cur.src.stop(at + xf + 0.02); } catch (e) {}" in _js186)
ok("LOG-186 (the user: C-B無pre) as amended by LOG-190 追記⑦ (the user: C1-B1 關 pre-entry, 開 TR 3): C1 > B2 still skips B's pick-up in every variant; C1 > B1 skips it too and carries TR with its third note muted - both generated by apply_plan.py's AMEND, not hand-edited",
   all(v.get("noPre") is True for v in _byp[("C1", "B2")]["random"]) and _byp[("C1", "B2")].get("amended", "").endswith("「C-B無pre」")
   and all(v.get("noPre") is True and v.get("layers") == ["TR"] and v.get("mute") == {"TR": [3]} for v in _byp[("C1", "B1")]["random"]) and _byp[("C1", "B1")].get("amended", "").endswith("「C1-B1 關pre-entry, 開TR 3」")
   and sum(1 for t in _rules if t["from"][0] == "C" and t["to"][0] == "B") == 3)
ok("LOG-202 (the user added C2>B1 in the lab, 「紀錄最新的然後上線」): C2 > B1 - the path the simple stage takes for D C B - has its own rule: no layer, B enters on the seam (noPre), C2's tail rings",
   _byp[("C2", "B1")].get("noPre") is True and _byp[("C2", "B1")]["layers"] == [] and not _byp[("C2", "B1")].get("noTail") and "random" not in _byp[("C2", "B1")])
ok("LOG-186: the demonstration walks A1 -> B1 -> A2 -> C1 -> B1 -> A1 - C2 is out of it",
   "E.force('C2')" not in _js186 and "i.id === 'C2'" not in _js186
   and "else if (i.id === 'C1') { nodeNow('c'); arcGoLate('lc'); E.force('B1');" in _js186
   and "else if (i.id === 'B1' && demoLeg === 2) { demoLeg = 3; if (!spark) nodeNow('b'); route('c', false); E.force('A1');" in _js186   # 追記⑩ / 追記⑲ append the lines after this
   and "else if (i.id === 'B1' && demoLeg === 1) { nodeNow('b'); arcGoLate('lb'); E.force('A2');" in _js186
   and "if (demoLeg === 3 && i.id === 'A1') { demoLeg = 4; E.tutReset(); E.layer(true, 0.7); if (phase === 'demo') { if (!spark) nodeNow('a'); soon(0.9, handBack); } }" in _js186)   # LOG-189: out legs flash-and-wipe here, back legs are lit by the landing spark. LOG-190 追記②: the drum layer comes back with A1
ok("LOG-186: C -> B has its own arc round the right of the board, ridden by the spark as B enters; the B -> A arc goes twice (追記⑲: the bubble rides each of them)",
   'class="arc acb" pathLength="1" d="M322 130 Q368 105 322 80' in _js186
   and "else if (i.id === 'B1' && demoLeg === 2) sparkAtSeam('acb', 'b', 'cb');" in _js186
   and "else if (i.hold && demoLeg === 3) sparkAtSeam('ab', 'a', 'rl', KAO_RUN_OFF);" in _js186 and "sparkAtSeam('ac'" not in _js186 and "arcPre('c')" not in _js186 and "arcGo('c')" not in _js186)


# ---- LOG-188 the lesson's step 1 remade: merge, re-split, shuffle; longer pauses; bigger bubbles; new opener; updates/sticky/dock hidden
_js188 = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8")
_css188 = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")
ok("LOG-188 (the user: 與上一句／下一句的時間都拉長): 「它是由好幾個段落組成的」 sits 3.6 s after its predecessor and 3.6 s before the next line",
   "at(11.6, function () { clipIn(); show('tut_s1a', clipBox); });" in _js188 and "at(15.2, function () { show('tut_s1a2', clipBox); clipSplit(); });" in _js188
   and "at(18.8, function () { show('tut_s1b', E.surface()); });" in _js188 and "at(19.4 + k * TUT_REVEAL_STEP" in _js188)
ok("LOG-188 (the user: 泡泡吸在一起變回音訊預覽條 → 再度分裂 → 前後段洗牌): the letters merge back into the region, it is cut again, the | lines come alone, both halves shuffle once round",
   "function clipMerge() {" in _js188 and "clipEl.classList.remove('split');" in _js188 and "soon(0.35, function () { LINE.forEach(function (g) { E.cover(g); }); });" in _js188
   and "at(23.0, function () { clipMerge(); show('tut_s1c', clipBox); });" in _js188 and "at(26.4, function () { hide(); clipCut(); });" in _js188 and "function clipCut() {" in _js188 and "E.surface().classList.add('bounce');" in _js188 and "at(28.0, function () { unbounce(); show('tut_s1d', E.surface()); });" in _js188
   and "at(27.3, function () { clipOut(); E.reveal('seps'); });" in _js188 and "LINE.forEach(function (g, k) { E.reveal('tile', g); });" in _js188 and "if (what === 'seps')" in _js188
   and "cover: function (g) { var c = colEls[g]; if (c) c.classList.add('hid'); }" in _js188
   and "[['pre', 'D'], ['post', 'H'], ['pre', 'C'], ['post', 'G'], ['pre', 'B'], ['post', 'F']].forEach(function (m, k) { at(28.8 + k * 0.6, function () { E.move(m[0], m[1], 0); }); });" in _js188 and "at(L1 + 23.0 + k * 0.6, function () {" in _js188 and "at(L1 + 26.6, function () {\n          unframeAll(); show('tut_s2b'" in _js188)   # LOG-189 追記⑤: the halves take turns (D H C G B F)
ok("LOG-188: the two new lines exist, bilingual, and the whole script from step 2 on is pushed by one pass of the opening loop (L1)",
   all(isinstance(site["ui"].get(k), dict) and site["ui"][k].get("zh") and site["ui"][k].get("en") and not _CJK.search(site["ui"][k]["en"]) for k in ("tut_s1c", "tut_s1d"))
   and "線性" in site["ui"]["tut_s1c"]["zh"] and "隨機" in site["ui"]["tut_s1d"]["zh"]
   and "var L1 = 13.2414;" in _js188 and "at(L1 + 21.0, function () { E.reveal('zones'); show('tut_s2a', E.surface()); });" in _js188
   and "at(L1 + 52.8, function () { E.force('A2'); });" not in _js188 and "at(L1 + 60.9, function () { demoStart(); E.force('B1'); });" in _js188 and "at(L1 + 60.9 - XF, function () { if (!E.releaseNow(XF_DEMO_BEATS)) E.force('A2'); });" in _js188   # LOG-189: a pass earlier than LOG-188. LOG-190 追記②: the cut replaces the queued A2. 追記④/⑨: the CUT is what sits inside the pass's first half
   and not re.search(r"\n\s+at\((2[1-9]|[3-9]\d)\.\d+, function \(\) \{ (E\.reveal\('zones'\)|unframeAll|show\('tut_s2|E\.layer|irisIn|zoneFrame|E\.force)", _js188))

# ---- LOG-189 (2026-09-13, the user's 追記④⑵ / ⑤ / ③⑶): step 2 re-choreographed, the step-1 shuffle interleaved with one arrow sweep, the graph's light animations swapped
_js189 = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8")
_css189 = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")
ok("LOG-189 追記⑤ (the user: 交換停止後箭頭從左到右連續亮一次): one sweep of its own after the shuffle, out of nothing and back into it, before step 2",
   "at(32.5, arrowsSweep);" in _js189 and "function arrowsSweep() {" in _js189 and "s.classList.add('asweep'); soon(1.7, function () { s.classList.remove('asweep'); });" in _js189
   and ".ds-secs.simple.asweep .col::after{opacity:0;transition:none;animation:tutsweep .8s ease-out both;animation-delay:calc(var(--i,0) * .09s)}" in _css189
   and "@keyframes tutsweep{0%{opacity:0;" in _css189 and "100%{opacity:0;color:rgba(255,255,255,.62);transform:translateY(-50%) scale(1)}}" in _css189
   and "for (var k = 0; k < 3; k++)" in _js189)   # step 3's three waves are untouched
ok("LOG-189 追記④⑵ (the user: A、I 按鈕本體向 E 滑動、中間按鈕與 | 線先消失、長出 E、滑回時按鈕陸續顯現): the tiles move, the way is cleared ahead of them, E is born from nothing, the row comes back behind them",
   "function slideTo(g, over, tr) {" in _js189 and "t.style.transition = tr || 'transform 1.3s cubic-bezier(.45,.05,.3,1)'; void t.offsetWidth;" in _js189
   and "soon(0.35, function () { slideBy('A', meetX('A', -1)); slideBy('I', meetX('I', 1)); });" in _js189   # LOG-190 追記⑥: to the meeting line (see the 追記⑥ assertion below)
   and "[['B', 'H', 0], ['C', 'G', 0.15], ['E', null, 0.25], ['D', 'F', 0.35]].forEach(function (m) { soon(m[2], function () { E.cover(m[0]); if (m[1]) E.cover(m[1]); }); });" in _js189
   and "e.style.transition = 'none'; e.style.transform = 'scale(0)'; void e.offsetWidth; e.style.transition = 'transform .6s cubic-bezier(.34,1.56,.64,1),opacity .45s ease';" in _js189
   and "E.reveal('tile', 'E'); if (e) { e.style.transform = '';" in _js189
   and "slideHome('A'); slideHome('I');" in _js189 and "[['D', 'F', 0.45], ['C', 'G', 0.75], ['B', 'H', 1.1]].forEach(function (m) { soon(m[2], function () { E.reveal('tile', m[0]); E.reveal('tile', m[1]); }); });" in _js189
   and "soon(1.3, function () { E.reveal('zones'); }); soon(2.0, unbounce);" in _js189
   and "function conceal(what) {" in _js189 and "conceal: conceal," in _js189 and "fA = frame(E.btn('A')); fI = frame(E.btn('I'));" not in _js189 and "reframe(fI, e," not in _js189)
ok("LOG-189 (timeline): s2b 26.6 / s2b2 29.6 / home 32.0, steps 3-6 at 33.8 / 37.8 / 41.8 / 45.8 (shade off 51.4), s2c + swap_pre as one demonstration 51.6-60.6, demo 60.9 (LOG-190 追記⑨ gave back the full LOG-189 length; only the cut at 56.76 has to obey the 50 % rule) - the tail is one pass of the opening loop shorter",
   all(t in _js189 for t in ("at(L1 + 29.6, function () {", "at(L1 + 32.0, function () {", "at(L1 + 33.4, function () { hide(); E.lit('E', false); });",
                             "at(L1 + 33.8, function () { unframeAll(); E.reveal('arrows'); arrowsWave(); show('tut_s_order', E.surface()); });",
                             "at(L1 + 37.8, function () { E.layer(true, 3.0); E.duck(1, 3.0); show('tut_s_hold', E.btn('A')); frame(E.btn('A')); });",   # 追記⑯: the volume comes up with the drums
                             "at(L1 + 41.8, function () { show('tut_s_layer', E.btn('A')); });",
                             "at(L1 + 45.8, function () { unframeAll(); irisIn(tickRect, 46); show('tut_s_tick', tickRect, { side: 'above' }); });",
                             "at(L1 + 51.4, function () { irisOut(); hide(); });",
                             "at(L1 + 51.6, function () { show('tut_s2c', E.surface()); E.conceal('zones'); E.cover('A'); E.cover('E'); E.cover('I'); });",
                             "at(L1 + 54.6, function () { show('tut_s_swap_pre', E.surface()); E.hint(true); });",
                             "at(L1 + 60.6, function () { hide(); E.hint(false); });"))   # LOG-190: same beats, no rings. 追記⑨: back to LOG-189's beats, and the hide no longer restores A / E / I (handBack does)
   and not re.search(r"at\(L1 \+ (?!59\.2\b|59\.8\b|60\.6\b|60\.9\b)(59|6[0-9]|7\d)\.\d", _js189) and "at(L1 + 35.4" not in _js189 and "at(L1 + 44.0" not in _js189)
_js190, _css190 = _js189, _css189
ok("LOG-190 ⑴ (the user: 五區走位跳到「開頭」沒有 transition): the first zone piece glides like the other four - show() never dips a standing bubble without slide there, nor at tut_s2b",
   "show('tut_s2z', tgt, { piece: k, instant: true, slide: true, again: true });" in _js190 and "slide: k > 0" not in _js190
   and "show('tut_s2b', E.surface(), { slide: true });" in _js190)
ok("LOG-190 ⑵ (the user: 取消兩區白框，改藏 A／E／I 與四條 | 線，六顆可動按鈕下方出現 < >): zoneFrame has no caller left; E.hint toggles .hint on the surface; a .hnt (< >) under every movable tile only, shown by .hint and fading with it",
   "zoneFrame('pre')" not in _js190 and "zoneFrame('post')" not in _js190
   and "hint: function (on) { segsEl.classList.toggle('hint', !!on); }" in _js190
   and "if (z.zone) { var hn = document.createElement('span'); hn.className = 'hnt'; hn.setAttribute('aria-hidden', 'true'); hn.innerHTML = '<i>‹</i><i>›</i>'; cols.appendChild(hn); }" in _js190   # LOG-190 追記⑥: one per zone, on .zcols
   and ".ds-secs.simple .zcols .hnt{position:absolute;left:-14px;right:-14px;top:calc(100% + 5px);display:flex;justify-content:space-between;" in _css190   # 追記⑦: flanking the three, below
   and ".ds-secs.simple.hint .zcols .hnt{opacity:1;transform:none}" in _css190 and "@keyframes tuthint{" in _css190 and "@keyframes tuthint2{" in _css190
   and "opacity:0;transform:translateY(-4px);transition:opacity .5s ease,transform .5s ease}" in _css190.split(".ds-secs.simple .zcols .hnt{", 1)[1].split("\n", 1)[0])
ok("LOG-190 追記⑥ (the user: 可以左右切換那個 < > 符號改成三個共用一套，現在每個按鈕一個太多太亂): the < > is appended once per movable zone after the tile loop, anchored on .zcols (position:relative); no .col .hnt rule is left",
   "col.appendChild(hn);" not in _js190 and _js190.count("hn.className = 'hnt';") == 1
   and "        });\n        if (z.zone) { var hn = document.createElement('span'); hn.className = 'hnt';" in _js190
   and ".ds-secs.simple .zcols{position:relative;gap:.9rem}" in _css190 and ".col .hnt" not in _css190)
ok("LOG-190 ⑶ (the user: 演示圖移到畫面正中並放大；去程從左到右擦亮並由白漸變成 B／C 色，到達時節點以該色亮起): graphLay centres on the stage with --gs up to 1.35 (the entrance keeps it); the twins draw 1 -> 0 from A in .95 s turning to --ac; B / C nodes light in --nc; both set from the tiles' --c in graphIn",
   "var s = Math.min(1.35, Math.max(1, (W - 32) / 360)); graphEl.style.setProperty('--gs', s.toFixed(3));" in _js190
   and "graphEl.style.left = Math.round(W / 2 - 180 * s) + 'px';" in _js190 and "clampY(Math.round(H / 2 - 95 * s), Math.round(190 * s))" in _js190 and "var r = rowBox();\n        graphEl.style.left" not in _js190
   and "[['b', '.arc.lb', '.nd.nb'], ['c', '.arc.lc', '.nd.nc']].forEach(function (m) {" in _js190 and "if (a) a.style.setProperty('--ac', c); if (n) n.style.setProperty('--nc', c);" in _js190
   and "transform-origin:0 0;transform:scale(var(--gs,1))}" in _css190.split(".desktop .stage-ui .tut-graph{", 1)[1].split("\n", 1)[0]
   and "@keyframes tutfin{from{opacity:0;transform:translateY(8px) scale(var(--gs,1))}to{opacity:1;transform:scale(var(--gs,1))}}" in _css190
   and ".desktop .stage-ui .tut-graph .arc.go{animation:tutarcgo .8s linear both}" in _css190
   and "@keyframes tutarcgo{0%{opacity:1;stroke-dashoffset:1;stroke:#fff;" in _css190 and "40%{opacity:1;stroke-dashoffset:0;stroke:color-mix(in srgb,#fff 50%,var(--ac,#fff));" in _css190 and "100%{opacity:0;stroke-dashoffset:0;stroke:var(--ac,#fff);" in _css190
   # LOG-190 追記⑥ (the user: 到 80% 的地方開始變藍，但是原本白色以及轉換的中間漸變顏色不變): white through 80 % of the travel (32 % = dashoffset .2), half-tinted on landing (40 %), fully --ac by 52 %
   and "32%{opacity:1;stroke-dashoffset:.2;stroke:#fff;" in _css190 and "52%{opacity:1;stroke-dashoffset:0;stroke:var(--ac,#fff);" in _css190 and "30%{opacity:1;stroke-dashoffset:.25;" not in _css190
   # LOG-190 追記① (the user: A-B 左到右的動畫太慢，B 都亮了箭頭還沒跑完): the line leaves ARC_LEAD = .32 s before the seam and lands as the node lights; the enter only fires it if the decision could not
   and "var ARC_LEAD = 0.32;" in _js190 and "function atSeam(lead, fn) {" in _js190 and "at(Math.max(T(), seam - lead), fn);" in _js190 and "function arcGoAtSeam(key) { atSeam(ARC_LEAD, function () { arcGo(key); }); }" in _js190
   and "function arcGoLate(key) { var a = arcOf(key); if (a && !a.classList.contains('go')) arcGo(key); }" in _js190 and "arcGo('lb')" not in _js190 and "arcGo('lc')" not in _js190)
ok("LOG-190 追記② (the user: 示範畫面出來前就用 A2 crossfade 切過去，不然又等一圈太久): E.releaseNow cuts to A2 at any point of the pass by the press's own crossfade without touching holding; the demonstration cuts at demoStart with B1 already forced, the board comes 0.3 s later and lights A, the seam path stands in only if the cut fails; the drum layer returns with A1",
   "releaseNow: function (beats) { return !!releaseMid(beats); }," in _js190
   and _js190.count("E.releaseNow(XF_DEMO_BEATS)") == 1 and _js190.count("E.releaseNow(") == 1 and _js190.count("E.release(true)") == 1
   and "soon(0.3, graphIn);" in _js190 and "soon(0.9, function () { sayOn('tut_sd1', 'a', { fresh: true }); });" in _js190 and "soon(0.9, graphIn);" not in _js190
   and "if (i.id === 'A2' && demoLeg === 0) { demoLeg = 1; nodeNow('a'); if (!i.mid) soon(2.0, function () { E.force('B1'); }); }" in _js190
   and "if (demoLeg === 1) nodeNow('a');" in _js190 and "E.tutReset(); E.layer(true, 0.7);" in _js190
   and "stroke-dashoffset:-1" not in _css190
   and ".desktop .stage-ui .tut-graph .nd.now{background:var(--nc,rgba(255,255,255,.94));color:#0b0d14;box-shadow:inset 0 0 0 1.5px var(--nc,#fff)," in _css190
   and "viewBox=\"0 0 360 190\"" in _js190 and ".desktop .stage-ui .tut-graph .nd{position:absolute;width:56px;height:56px" in _css190)
# ---- LOG-190 追記④ (the user: A-B 那邊提前進入請做在進度 50% 前，跟「換你試試看」那邊的切換邏輯一樣)
_A1_IN, _A1_OUT = 52.96, 66.20   # the sounding pass of the opening loop, in L1 + x seconds (measured, LOG-189 / LOG-190)
_A1_HALF = (_A1_IN + _A1_OUT) / 2   # 59.58 - midHalf() refuses to cut from here on
_m190d = re.search(r"at\(L1 \+ (\d+(?:\.\d+)?), function \(\) \{ demoStart\(\);", _js190)
_demo_at = float(_m190d.group(1)) if _m190d else -1.0
_cut_at = _demo_at - _XF7 if _m190d else -1.0   # 追記⑨: the CUT, one crossfade ahead of demoStart, is what midHalf() judges - not demoStart
_cut_pct = (_cut_at - _A1_IN) / (_A1_OUT - _A1_IN) * 100 if _m190d else -1.0
_n190d = ("LOG-190 追記④ (the user: A-B 那邊提前進入請做在進度 50% 前，跟「換你試試看」那邊的切換邏輯一樣) as re-read by 追記⑨: the cut - demoStart "
          "L1 + {:.1f} minus one crossfade - falls at L1 + {:.2f} = {:.1f}% of the pass, before the 50% mark at L1 + {:.2f} and after the "
          "tick's shade; midAny is gone and releaseNow / release(true) both reach the one midHalf() rule, written once").format(_demo_at, _cut_at, _cut_pct, _A1_HALF)
ok(_n190d,
   51.6 < _cut_at < _A1_HALF
   and "midAny" not in _js190
   and "function midHalf() {" in _js190 and "if ((ctx.currentTime - loopStart) / L >= 0.5) return null;" in _js190 and _js190.count("/ L >= 0.5") == 1
   and "var loopStart = midHalf(); if (loopStart == null) return false;" in _js190
   and "releaseNow: function (beats) { return !!releaseMid(beats); }," in _js190 and "if (mid && releaseMid()) return;" in _js190)
# ---- LOG-190 追記⑮ (the user: ABC 展示時的 A-B、A-C 灰色箭頭陰影還在)
ok("LOG-190 追記⑮: the demo graph's resting route tracks (.rt rb / .rt rc, grey with arrowheads) draw nothing any more - the outward routes only show as their lit twins",
   ".desktop .stage-ui .tut-graph .rt{fill:none;stroke:none;" in (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8"))
# ---- LOG-190 追記⑬ (the user: 結尾敘述 V6 跟 Shiou Hsu 那邊多一個演示 — 七句＋分裂／穿插；裁示 (a) 先進 I loop 撐時間、舊結尾句刪掉、左側標 V1〜V6 放不下就縮)
_css190 = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")
ok("LOG-190 追記⑬: the old closing lines are gone and the seven new ones are there, bilingual; v2 / v5 carry exactly one split mark each, at V3 / V6",
   not any(k in site["ui"] for k in ("tut_s_end", "tut_s_end2")) and all(isinstance(site["ui"].get("tut_s_v%d" % n), dict) for n in range(1, 8))
   and site["ui"]["tut_s_v2"]["zh"].count("|") == 1 and site["ui"]["tut_s_v2"]["zh"].split("|")[0].endswith("V3") and site["ui"]["tut_s_v2"]["en"].split("|")[0].endswith("V3")
   and site["ui"]["tut_s_v5"]["zh"].count("|") == 1 and site["ui"]["tut_s_v5"]["zh"].split("|")[0].endswith("V6") and site["ui"]["tut_s_v5"]["en"].split("|")[0].endswith("V6")
   and "Shiou Hsu" in site["ui"]["tut_s_v1"]["zh"] and "Shiou Hsu" in site["ui"]["tut_s_v1"]["en"] and "relay([" not in _js190)
ok("LOG-190 追記⑬: the time is bought on the outro LOOP - armed on the last second-half group's second version, re-armed on every pass (a loop lets go after two on its own), I forced at FIN_LEAVE, and 結束教學 mid-show forces I too",
   "if (i.id === 'I_loop') { if (!fin.leave) E.force('I_loop'); finale(); return; }" in _js190
   and "if (po.length && i.group === po[po.length - 1] && fin.seen[i.group] === 2) E.force('I_loop'); }" in _js190
   and "soon(FIN_LEAVE, function () { fin.leave = true; E.force('I'); });" in _js190 and "FIN_LEAVE = 19.0, FIN_V7 = 11.0" in _js190 and "FIN_OUT" not in _js190 and "soon(23.0, function () { subtitle('tut_s_v5'" in _js190 and "soon(24.7, finWeb); soon(25.7, finSweep);" in _js190 and "soon(28.5, function () { subtitle('tut_s_v6'" in _js190   # 追記⑰ (the user: 結尾出 I End 時間太久了，提前一個 loop): I enters at 22.33, the tail pulled forward under it. 追記⑯ (the user: 結束畫面前不用再回到一串，V6 那堆那邊直接收場就好，音樂大概也抓那個長度): I a pass earlier (enters 27.91), the rows stay, no finOut on the clock
   and "if (fin.on && !fin.leave) { fin.leave = true; try { E.force('I'); } catch (e) {} }" in _js190
   and "if (i.outro) { finEnd(); return; }" in _js190 and "hide(); unframeAll(); soon(FIN_V7, function () { subtitle('tut_s_v7', { sky: true }); });" in _js190 and "finOut(); try { E.surface().classList.remove('tut-down'); }" not in _js190)
ok("LOG-190 追記⑬: the author's line learned '|' marks and the sky (subtitle o.marks / o.sky), and the seven lines use them: v1-v6 in the sky, v2 splits to 3 at V3, v5 to 6 at V6",
   "var pieces = raw.split('|'), text = pieces.join(''), marks = [], acc = 0;" in _js190
   and "while (s.marks && s.marks.length && sn >= s.marks[0].n) { var smk = s.marks.shift();" in _js190
   and "return { top: o.sky ? Math.max(safeTop() + 6, (fin.el && fin.spread ? fin.top : rowBox().top) - h0 - 16) : asideTop(w0, h0, res) };" in _js190   # 追記⑯: 16 px over the row, not at the safe top; 追記⑱-②: over the climbed overlay once laid out
   and "subtitle('tut_s_v2', { sky: true, marks: [null, function () { finSplit(3); }] });" in _js190
   and "subtitle('tut_s_v5', { sky: true, marks: [null, function () { finSplit(6); }] }); aBack(); });   /* 追記⑰" in _js190   # 追記㉛: the thrown A fades back in here
   and all("subtitle('tut_s_v%d', { sky: true });" % n in _js190 for n in (1, 3, 4, 6))
   and "function subLen(key) { var s = txt(key).replace(/\\|/g, '');" in _js190)
ok("LOG-190 追記⑬: the version rows are copies in the stage-ui measured from the real row (never the engine's tiles), born on the row they split from, scaled as a whole to fit above the line; shuffle / route / web / sweep / out are all there",
   all(t in _js190 for t in ("function finGeo() {", "fin.el.className = 'tut-vers';", "finRow(r, (r >= 3 ? r - 3 : 0) * g.rowH)", "sc = Math.max(0.35, Math.min(1, avail / need))",
                              "function finShuffle(dur) {", "soon(dur + 0.3, function () { perm = idx.slice(); apply(); });", "function finRoute() {", "function finWeb() {",
                              "for (k = 0; k < line.length - 1; k++) for (r1 = 0; r1 < n; r1++) for (r2 = 0; r2 < n; r2++) finHop(grp, { k: line[k].k, v: line[k].v, r: r1 }, { k: line[k + 1].k, v: line[k + 1].v, r: r2 }, 'vw');",   # 追記⑱-②: place to next place, every version
                              "function finSweep() {", "function finOut() {", "E.surface().classList.add('tut-down');", "'tut-away', 'awave', 'frozen', 'bounce', 'tut-down'"))
   and "if (fin.el) { fin.el.remove(); fin.el = null; fin.svg = null; fin.rows = []; }" in _js190)
ok("LOG-190 追記⑬: os.css - the slide, the overlay, rows, tiles, route arrows, the web and the sweep; every transitioning rule three classes deep (os.css:470)",
   ".ds-secs.simple.tut-down{transform:translateY(var(--fdy,64px));transition:transform .7s" in _css190
   and ".desktop .stage-ui .tut-vers{position:absolute;left:0;top:0;z-index:5;" in _css190 and "transform:scale(var(--vs,1));transition:transform .8s" in _css190
   and ".desktop .stage-ui .tut-vers .vrow{" in _css190 and ".desktop .stage-ui .tut-vers .vl{" in _css190 and ".desktop .stage-ui .tut-vers .vt{" in _css190
   and ".desktop .stage-ui .tut-vers .va.go{animation:tutvgo" in _css190 and "@keyframes tutvgo{" in _css190 and "@keyframes tutvhd{" in _css190
   and ".desktop .stage-ui .tut-vers.sw .vw{animation:tutvsw" in _css190 and ".desktop .stage-ui .tut-vers.sw .vt{animation:tuttile" in _css190 and "@keyframes tutvsw{" in _css190
   and all(ln.startswith(".desktop .stage-ui .tut-vers") for ln in _css190.splitlines() if ".tut-vers" in ln and "transition:" in ln and not ln.lstrip().startswith(("/*", "*", "over", "Three"))))
# ---- LOG-190 追記⑭ (the user: 每段都有兩個的那個地方在解釋時，在懸浮的按鈕後面多一排按鈕，概念類似陰影，不要完全站到前面；按鈕上顯示 X2，2 跟次方那種一樣小、在右下角；解釋完後收起來)
_js190n = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8"); _css190n = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")
ok("LOG-190 追記⑭: every MOVABLE tile (B-H, the zones with a .zone) carries an X2 twin appended after the tile in its own .col (finGeo reads firstChild), the bookends none; E.shade toggles .shade on the surface",
   "if (g !== 'I') { var sh = document.createElement('i'); sh.className = 'shade'; sh.setAttribute('aria-hidden', 'true'); sh.innerHTML = '<span class=\"nm\">' + g + '<sub>2</sub></span>'; col.appendChild(sh); col.classList.add('twin'); }" in _js190n   # 追記⑯-② (the user: A, E 都有 2): all but I
   and _js190n.index("col.appendChild(t);\n          if (g !== 'I') { var sh = document.createElement('i'); sh.className = 'shade';") > 0
   and "shade: function (on) { segsEl.classList.toggle('shade', !!on); }" in _js190n)
ok("LOG-190 追記⑭: the twins show for exactly as long as tut_s_pair stands (subtitle's own length), then go back in; 結束教學 strips .shade with the other lesson classes",
   "var pairLen = subtitle('tut_s_pair', { marks: [null, function () { try { E.shade(true); } catch (e) {} }] });" in _js190n   # 追記⑯: out at the '|' mark, once 「一到兩段。」 has been said
   and site["ui"]["tut_s_pair"]["zh"].count("|") == 1 and site["ui"]["tut_s_pair"]["zh"].split("|")[0].endswith("兩段。") and site["ui"]["tut_s_pair"]["en"].count("|") == 1 and site["ui"]["tut_s_pair"]["en"].split("|")[0].endswith("sections.")
   and "soon(7.0, function () { var g = nextOpen(); show('tut_s13a', E.btn(g)); frame(E.btn(g)); });" in _js190n and "soon(12.0, function () { show('tut_s13b', zoneBox('post'), { hold: 14 }); });" in _js190n   # 追記⑯ (the user: 接續的泡泡太快出現了): 5.5 / 10.0 -> 7.0 / 12.0
   and ".ds-secs.simple.shade .col.twin:not(.hid) .seg.tile{opacity:.42}" in (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")   # 追記⑯: the first-version tiles dim under the twins
   and "soon(pairLen || 7, function () { try { E.shade(false); } catch (e) {} });" in _js190n
   and "'tut-away', 'awave', 'frozen', 'bounce', 'tut-down', 'shade'" in _js190n)
ok("LOG-190 追記⑭: os.css - the twin sits BEHIND (z 0 under the tile's z 1), folded under the tile until .shade slides it 9 px down-right at .62 with a dimmer label and darker fill (the tile is clear glass), the 2 a 9 px subscript; three classes deep (os.css:470); .hid covers it with its tile; its label joins the language swap on both shells",
   ".ds-secs.simple .col .shade{position:absolute;inset:0;z-index:0;" in _css190n and "pointer-events:none;opacity:0;transform:translate(0,0) scale(.94);transition:opacity .5s ease,transform .5s cubic-bezier(.2,.7,.2,1)}" in _css190n
   and "color:rgba(255,255,255,.72);background:color-mix(in srgb,var(--c) 14%,rgba(0,0,0,.5));" in _css190n
   and ".ds-secs.simple.shade .col .shade{opacity:.62;transform:translate(9px,9px) scale(.94)}" in _css190n
   and ".ds-secs.simple .col .shade .nm sub{font-size:9px;line-height:0;vertical-align:sub;" in _css190n
   and ".ds-secs.simple .col.hid .shade{opacity:0}" in _css190n
   and ".ds-sec .seg{position:relative;z-index:1;" in _css190n
   and all(".%s.swap-out .dstage .shade .nm," % sh in _css190n and ".%s.swap .dstage .shade .nm," % sh in _css190n and ".%s.swap-in .dstage .shade .nm," % sh in _css190n for sh in ("desktop", "phone")))
# ---- LOG-190 追記⑯ (the user: 結尾 V6 那邊展示多排時要跟前面一樣的陰影 / 教學模式開始時音樂音量壓小，鼓進來時才轉大 / V3 那個隨機轉換的頻率太瘋狂了，冷靜一點點)
_js190p = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8"); _css190p = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")
ok("LOG-190 追記⑯: every movable copy on the version rows carries the same X2 twin (built from the geometry's pre/post indices, never the bookends), the overlay wears .shade from its first frame; os.css draws it behind its .vt (z -1 in the row's stacking context) with the same look as 追記⑭",
   "if (t.g !== 'I') {   /* 追記⑯-②: every letter but I has a second version */" in _js190p
   and "var sh = document.createElement('i'); sh.className = 'shade'; sh.innerHTML = '<span class=\"nm\">' + t.g + '<sub>2</sub></span>'; sh.style.setProperty('--k', 2 * k + 1); e.appendChild(sh); e.twin = sh; }" in _js190p   # 追記⑱: the tile remembers its twin; ⑱-②: the twin's place on the ladder
   and "requestAnimationFrame(function () { if (fin.el) fin.el.classList.add('in', 'shade'); });" in _js190p
   and ".desktop .stage-ui .tut-vers .vt .shade{position:absolute;inset:0;z-index:-1;" in _css190p
   and ".desktop .stage-ui .tut-vers.shade .vt .shade{opacity:.62;transform:translate(9px,9px) scale(.94)}" in _css190p
   and ".desktop .stage-ui .tut-vers .vt .shade .nm sub{font-size:9px;line-height:0;vertical-align:sub;" in _css190p
   and ".desktop .stage-ui .tut-vers .vrow{position:absolute;left:0;top:0;width:100%;opacity:0;transform:translateY(var(--y,0px));" in _css190p)   # the row's transform is the stacking context the z -1 relies on
ok("LOG-190 追記⑯＋追記⑲ (the user: 一開始要從100%走intro，然後才慢慢降低，大概80%左右): the lesson opens at full level, lets the engine down to TUT_DUCK = 0.8 once A's two opening beats (the user's 'intro', 0.828 s) have played, on a 1.6 s time constant (a gain after the analyser, before the mute - the bars keep their size) and lets it up with the drums at L1 + 37.8 on the drums' own 3 s time constant; 結束教學 restores it whatever the step",
   "TUT_DUCK = 0.8, TUT_DUCK_AT = 0.83, TUT_DUCK_TC = 1.6;" in _js190p and _tu and abs(0.83 - _tu["hold"]["openBars"] * _bar) < 0.01 and "try { E.duck(1, 0.02); } catch (e) {}" in _js190p and "E.duck(TUT_DUCK, 0.02)" not in _js190p
   and "at(TUT_DUCK_AT, function () { E.duck(TUT_DUCK, TUT_DUCK_TC); });" in _js190p
   and "duckG = ctx.createGain(); duckG.gain.value = duckLvl; analyser.connect(duckG); duckG.connect(mgain); mgain.connect(ctx.destination);" in _js190p
   and "duck: function (lvl, tc) { duckLvl = lvl == null ? 1 : lvl; if (duckG && ctx) { duckG.gain.cancelScheduledValues(ctx.currentTime); duckG.gain.setTargetAtTime(Math.max(0.0001, duckLvl), ctx.currentTime, tc || 0.5); } }," in _js190p
   and "at(L1 + 37.8, function () { E.layer(true, 3.0); E.duck(1, 3.0); show('tut_s_hold', E.btn('A')); frame(E.btn('A')); });" in _js190p
   and "if (!E.holding()) E.layer(true); E.duck(1, 0.5); } catch (e) {}" in _js190p)
ok("LOG-190 追記⑯: the V3 shuffle is calmer - .55-1.0 s between swaps (was .26-.56), so every .5 s glide lands before the next; the come-home at dur + .3 is unchanged",
   "tt += 0.55 + Math.random() * 0.45; }" in _js190p and "var tt = 0.1 + Math.random() * 0.5;" in _js190p and "0.26 + Math.random() * 0.3" not in _js190p)
# ---- LOG-190 追記⑱ (the user: 展示 V3 時隨機左右切換時 1&2 維持現狀，要畫線圖時將 2 從陰影直接也鋪成按鈕，讓路線更多選擇性，然後以此構造鋪成 V6 後展示更龐大複雜的線路可能性)
_js190r = (ROOT / "assets" / "js" / "os.js").read_text(encoding="utf-8"); _css190r = (ROOT / "assets" / "css" / "os.css").read_text(encoding="utf-8")
ok("LOG-190 追記⑱ / ⑱-② (the user: A1 後面 A2 才 B1，不是排到下面): the twins spread as the fourth line opens (17.5, .5 s before the route; spread BEFORE the line is placed) - never during the shuffle - onto the same line, each one step after its tile (finX: every twin before letter k pushes it a step right; the | lines with them), the overlay re-fits around the row's centre (finFit)",
   "soon(17.5, function () { finSpread(); subtitle('tut_s_v4', { sky: true }); });" in _js190r and "soon(11.2, function () { finShuffle(4.8); });" in _js190r and _js190r.index("finShuffle(4.8)") < _js190r.index("finSpread(); subtitle('tut_s_v4'")
   and "FIN_ROW_GAP = 18, FIN_ROW_GAP_S = 12, FIN_ROWS = 6, FIN_BYE = 12.8, FIN_LEAVE = 19.0" in _js190r and "FIN_SUB_GAP" not in _js190r and "finBH" not in _js190r
   and "function finX(k, v) { var g = fin.geo, t = g.tiles[k]; if (!fin.spread) return t.x; var x = t.x + g.tb[k] * g.step; return v === 2 ? x + g.step : x; }" in _js190r
   and "var gapS = tiles.length > 2 ? Math.max(6, tiles[2].x - tiles[1].x - tiles[1].w) : 12, step = (tiles[0] ? tiles[0].w : 58) + gapS, tb = [], twins = 0, sepsS = [];" in _js190r
   and "function finW() { var g = fin.geo; return g.width + (fin.spread ? g.twins * g.step : 0); }" in _js190r
   and "fin.spread = true; fin.gen = (fin.gen || 0) + 1; g.rowH = g.h + FIN_ROW_GAP_S;" in _js190r   # LOG-191: the layout generation - finShuffle's pending writes carry the x values of the layout they were made against, and must go quiet once the chart is laid out again and "fin.el.style.setProperty('--sub', g.gapS + 'px'); fin.el.classList.add('spread');" in _js190r
   and "fin.rows.forEach(function (row) { row.tiles.forEach(function (e, k) { e.style.left = finX(k, 1) + 'px'; }); row.seps.forEach(function (b, i) { b.style.left = finSepX(i) + 'px'; }); });\n        finFit(fin.rows.length);" in _js190r
   and "e.style.left = finX(k, 1) + 'px';" in _js190r and "b.style.left = finSepX(k) + 'px';" in _js190r and "e.style.setProperty('--k', 2 * k);" in _js190r and "sh.style.setProperty('--k', 2 * k + 1); e.appendChild(sh); e.twin = sh; }" in _js190r
   and "g = fin.geo = finGeo(); if (!g.tiles.length) return; fin.spread = false; fin.tm = 0;" in _js190r)
ok("LOG-190 追記⑱-② (the user: V6 之後按鈕變超小，讓按鈕排列可以延展成可以在桌面上合理安排的距離): once laid out the overlay is centred on the row's centre at its new width, climbs to 16 px under the sky line (finTopMin - the sky line is then placed 16 px over the overlay's top), may run down over the wave to the now-playing block or the stage's foot (finFloor), rows 12 apart, and only what still does not fit is scaled; before the spread nothing changes (on the row, above the line)",
   "var g = fin.geo, W = finW(), left = g.left + (g.width - W) / 2, top = g.top, need = n * g.rowH - FIN_ROW_GAP, avail = lineY() - 16 - top, sc;" in _js190r
   and "var fl = finFloor(left, W), tm = fin.tm, full = FIN_ROWS * g.rowH - FIN_ROW_GAP_S;" in _js190r and "if (!fin.tm) fin.tm = finTopMin();" in _js190r and "fin.spread = false; fin.tm = 0;" in _js190r and "FIN_ROWS = 6, FIN_BYE = 12.8, FIN_LEAVE" in _js190r   # 追記⑱-③ (the user: V6 那邊按鈕置中，不然字幕被推到超級上面): the six-version block is reserved and centred from the spread
   and "need = n * g.rowH - FIN_ROW_GAP_S; sc = Math.max(0.35, Math.min(1, (fl - tm) / full));" in _js190r
   and "top = Math.max(tm, Math.min(g.top + (fl - g.top) / 2 - full * sc / 2, fl - full * sc));" in _js190r
   and "try { E.surface().classList.add('tut-away'); } catch (e) {}\n          demoLights(true);   /* 追記⑱-④" in _js190r   # 追記⑱-③ (the user: V6 那邊要像展示 ABC 那邊一樣的畫面暗下來) / ⑱-④ (從最後展示 V3 就要有了): dim from the V3 split
   and "HOST.classList.remove('tut-demo'); if (head) head.classList.remove('tut-demo');   /* 追記⑱-③" in _js190r
   and "FIN_ROWS = 6, FIN_BYE = 12.8, FIN_LEAVE" in _js190r and "soon(FIN_V7, function () { subtitle('tut_s_v7', { sky: true }); }); soon(FIN_BYE, finExit);" in _js190r   # 追記⑱-④ (the user: 結束時按鈕沒有退出 transition 就直接消失): the chart fades and the dim lifts ahead of onBye
   and "if (fin.el) fin.el.classList.remove('in');\n        HOST.classList.remove('tut-demo'); if (head) head.classList.remove('tut-demo');" in _js190r
   and "function finBack(grp, a, b, cls) {" in _js190r and "if (line[k].v !== 1 || line[k].k === 0) continue; for (j = 0; j < line.length; j++) { if (line[j].k !== line[k].k - 1) continue;" in _js190r   # 追記⑱-④ (沒有看到可以繞回先前段落的箭頭): every X1 (and I) back to the previous letter's X1 / X2 on every version
   and "hd.setAttribute('points', '0,0 9,-4.5 9,4.5');" in _js190r and ".desktop .stage-ui .tut-vers .vb{fill:none;stroke:rgba(255,214,150,.15);stroke-width:1.2;stroke-linecap:round;stroke-dasharray:3 3}" in _css190r and ".desktop .stage-ui .tut-vers.sw .vb{animation:tutvsw" in _css190r
   and "fin.top = top; fin.el.style.left = left + 'px'; fin.el.style.top = top + 'px'; fin.el.style.width = W + 'px';" in _js190r
   and "fin.el.style.left = g.left + 'px'; fin.el.style.top = g.top + 'px'; fin.el.style.width = g.width + 'px';   /* 追記⑱-②: born in place" in _js190r   # left / top glide now: the first computed style must already be the row's
   and "if (hr && hr.width > 0 && hr.bottom > top && hr.top < top + need * sc && cx + vw / 2 > hr.left - 16) cx = Math.max(16 + vw / 2, hr.left - 16 - vw / 2);" in _js190r   # never under the brand title
   and "var hero = document.querySelector('.hero-text h1') || document.querySelector('.hero-text'), hr = hero ? hero.getBoundingClientRect() : null" in _js190r   # the h1: the section is zero-width
   and "var sk = null; subs.forEach(function (s) { if (s.sky && !s.out && s.h && s.h.el) sk = s; }); var h0 = sk ? sk.h.el.offsetHeight : 48; return safeTop() + 6 + h0 + 16;" in _js190r
   and "var np = document.getElementById('np-desktop'), r = np ? rectOf(np) : null, fl = HOST.clientHeight - 24;" in _js190r and "if (r && r.top > 0 && r.left < left + W && r.right > left) fl = Math.min(fl, r.top - 12);" in _js190r
   and "top: o.sky ? Math.max(safeTop() + 6, (fin.el && fin.spread ? fin.top : rowBox().top) - h0 - 16) : asideTop(w0, h0, res)" in _js190r
   and "function finLay() { if (!fin.el || !fin.geo) return; var rb = rowBox(), zr = rectOf(zoneBox('pre')) || rb; fin.geo.left = rb.left; fin.geo.top = zr.top; finFit(fin.rows.length); }" in _js190r
   and "fin.rows.forEach(function (w) { w.classList.add('in'); w.style.setProperty('--y', (w.r * g.rowH) + 'px'); });" in _js190r)
ok("LOG-190 追記⑱-② (the user: V6 箭頭也要指向那個按鈕可以接去的片段): the played line as places (finLine: A1 A2 B1 B2 … H2 I; I has no 2) - the route walks it end to end, every place on a version drawn at random, one hop every .25 s, the twin it lands on lit and cleared after; the web joins every place to the NEXT place on every version and nothing else (X1 to every X2, X2 to every next X1, H2 to every I)",
   "function finLine() { var g = fin.geo, out = [], k, i, vs; for (k = 0; k < g.tiles.length; k++) { vs = finVers(k); for (i = 0; i < vs.length; i++) out.push({ k: k, v: vs[i] }); } return out; }" in _js190r
   and "function finVers(k) { return fin.spread && fin.geo.tiles[k].g !== 'I' ? [1, 2] : [1]; }" in _js190r
   and "function finNode(row, k, v) { return v === 2 && row.tiles[k].twin ? row.tiles[k].twin : row.tiles[k]; }" in _js190r
   and "path = finLine().map(function (p) { return { k: p.k, v: p.v, r: Math.floor(Math.random() * n) }; }), HOP = 0.25, k;" in _js190r
   and "soon(k * HOP + 0.42, function () { if (fin.el) finNode(fin.rows[path[k + 1].r], path[k + 1].k, path[k + 1].v).classList.add('on'); });" in _js190r
   and "soon((path.length - 1) * HOP + 1.6, function () { grp.classList.add('off');" in _js190r and "t.classList.remove('on'); if (t.twin) t.twin.classList.remove('on'); }); });" in _js190r
   and "for (k = 0; k < line.length - 1; k++) for (r1 = 0; r1 < n; r1++) for (r2 = 0; r2 < n; r2++) finHop(grp, { k: line[k].k, v: line[k].v, r: r1 }, { k: line[k + 1].k, v: line[k + 1].v, r: r2 }, 'vw');" in _js190r
   and "x1 = finX(a.k, a.v) + ta.w, y1 = finY(a.r), x2 = finX(b.k, b.v), y2 = finY(b.r), bend = Math.max(6, Math.min(40, Math.abs(y2 - y1) * 0.35, (x2 - x1) * 0.5));" in _js190r
   and "function finY(r) { var g = fin.geo; return r * g.rowH + g.h / 2; }" in _js190r)
ok("LOG-190 追記⑱ / ⑱-②: os.css - .spread slides the twin one gap to the RIGHT of its tile (translate 100% + --sub, full opacity, tile glass), .shade.on is the route's landing look, the sweep rings the twins too (tuttwin) on a 17-place ladder at .08 s a step, the | lines glide with the tiles, the overlay's left / top / width glide; three classes deep",
   ".desktop .stage-ui .tut-vers.spread .vt .shade{opacity:1;transform:translate(calc(100% + var(--sub,12px)),0) scale(1);color:var(--fg);" in _css190r
   and _css190r.index(".desktop .stage-ui .tut-vers.shade .vt .shade{opacity:.62;") < _css190r.index(".desktop .stage-ui .tut-vers.spread .vt .shade{")   # equal weight: the later rule wins
   and ".desktop .stage-ui .tut-vers .vt .shade.on,.desktop .stage-ui .tut-vers .vt.on{box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.95)," in _css190r   # 追記㉓: the landing look is a white frame (below)
   and ".desktop .stage-ui .tut-vers.sw .vt{animation:tuttile .55s ease-out both;animation-delay:calc(var(--k,0) * .08s)}" in _css190r
   and ".desktop .stage-ui .tut-vers.sw.spread .vt .shade{animation:tuttwin .55s ease-out both;animation-delay:calc(var(--k,0) * .08s)}" in _css190r and "@keyframes tuttwin{" in _css190r
   and ".desktop .stage-ui .tut-vers.sw .vw{animation:tutvsw .6s ease-out both;animation-delay:calc(var(--k,0) * .08s + .035s)}" in _css190r
   and "transition:transform .8s cubic-bezier(.2,.7,.2,1),opacity .5s ease,left .8s cubic-bezier(.2,.7,.2,1),top .8s cubic-bezier(.2,.7,.2,1),width .8s cubic-bezier(.2,.7,.2,1)}" in _css190r
   and ".desktop .stage-ui .tut-vers .vrow{position:absolute;left:0;top:0;width:100%;opacity:0;transform:translateY(var(--y,0px));transition:transform .8s cubic-bezier(.2,.7,.2,1),opacity .5s ease}" in _css190r
   and ".desktop .stage-ui .tut-vers .vs{" in _css190r and "box-shadow:0 0 6px rgba(255,255,255,.18);transition:left .5s cubic-bezier(.2,.7,.2,1)}" in _css190r)
# ---- LOG-190 追記㉓ (the user: 結尾串聯 V6 的可能性箭頭不要壓到按鈕本身 / V1-V6 的不同版本的按鈕顏色要稍微不一樣，漸層，從 V3 開始顯示時就要 / 隨機線路的亮燈方式用亮框)
ok("LOG-190 追記㉓ ①: the way-back arrows (finBack) run in the GUTTERS only - to the previous letter's X₂ an S down the one column between them (a straight stroke on the same row), to its X₁ out into the 12 px channel beside a's row, left under the X₂ and up the column before it (finPath, rounded corners, r 5); the old bow across the tiles (drop / bend) is gone; no pathLength, so the 3 3 dashes are real",
   "function finBack(grp, a, b, cls) {" in _js190r and "function finPath(pts, rad) {" in _js190r
   and "var c1 = (x1 + finX(a.k - 1, 2) + tl.w) / 2, c0 = x2 + g.gapS / 2, n = fin.rows.length, d;" in _js190r
   and "var below = b.r > a.r || (b.r === a.r && a.r < n - 1), cy = below ? y1 + g.h / 2 + FIN_ROW_GAP_S / 2 : y1 - g.h / 2 - FIN_ROW_GAP_S / 2;" in _js190r
   and "d = finPath([[x1, y1], [c1, y1], [c1, cy], [c0, cy], [c0, y2], [x2, y2]], 5);" in _js190r
   and "p.setAttribute('d', d); p.setAttribute('class', cls);   /* no pathLength" in _js190r
   and "drop = g.h * 0.55" not in _js190r and "hd.setAttribute('points', '0,0 9,-4.5 9,4.5');" in _js190r)
ok("LOG-190 追記㉓ ②: every version row has its own shade of each letter's colour - finTint(c, r): FIN_HUE 7° round the hue and FIN_LIGHT .03 lighter per row, V1 exact - written at the row's birth (so V2 / V3 differ from the split), and the arrows (finHop / finBack) take the LANDING row's shade",
   "var FIN_HUE = 7, FIN_LIGHT = 0.03;" in _js190r and "function finTint(c, r) {" in _js190r and "if (!r || !/^#[0-9a-f]{6}$/i.test(c)) return c;" in _js190r
   and "e.style.setProperty('--c', finTint(t.c, r));" in _js190r and _js190r.count("var ac = finTint(tb.c, b.r);") == 2
   and "p.style.setProperty('--ac', ac); hd.style.setProperty('--ac', ac); p.style.setProperty('--k', a.k);" in _js190r
   and "p.style.setProperty('--ac', ac); hd.style.setProperty('--ac', ac); p.style.setProperty('--k', 2 * a.k);" in _js190r
   and "p.style.setProperty('--ac', tb.c)" not in _js190r)
ok("LOG-190 追記㉓ ③: a place the route lands on wears a white frame only (.vt.on / .shade.on: white ring + white glow, no background, no colour) - the sweep's own ring (tuttile) - so a colourless tile stays colourless when pointed at",
   ".desktop .stage-ui .tut-vers .vt .shade.on,.desktop .stage-ui .tut-vers .vt.on{box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.95),inset 0 1px 0 rgba(255,255,255,.34),0 0 14px rgba(255,255,255,.40),0 6px 16px rgba(0,0,0,.30)}" in _css190r
   and ".tut-vers .vt.on{background:" not in _css190r and ".tut-vers .vt .shade.on{background:" not in _css190r)
# ---- LOG-190 追記㉔ (the user: 顏文字那邊改成全程都有顏文字(不消失)，但是移動時就換一個隨機的顏文字，泡泡的字都附在顏文字上且不遮擋abc的圖示)
ok("LOG-190 追記㉔ ①: the kaomoji is the demonstration's narrator - it stands at A's lower left as the board comes in (kaoStand), takes a new face at every hand-over (kaoRide = kaoMake + legs), lives at a dock beside the sounding node (kaoDock: A lower left / B upper left / C lower right, KAO_PAD clear) and goes only with the board (handBack) or the lesson (end)",
   "KAO_LEAD =" not in _js190r and "KAO_SETTLE" not in _js190r and "function kaoStand(which) { if (!kaoMake({ a: KAO_LINE.tut_sd1, b: KAO_LINE.tut_sd1 })) return; kaoSlot = which; kao.home = kaoDock(which); kao.landed = true; kaoStep(); }" in _js190r   # 追記㉗: standing in sd1's face
   and "soon(0.5, function () { kaoStand('a'); });" in _js190r and "function kaoDock(which, sz) {" in _js190r
   and "if (which === 'a') return { x: (n.left + n.right) / 2, y: n.top - KAO_A_GAP - s.h / 2 };" in _js190r and "if (which === 'ab') return { x: (n.left + n.right) / 2, y: n.bottom + KAO_A_GAP + s.h / 2 };" in _js190r and "var KAO_A_GAP = 8, kaoSlot = 'a';" in _js190r and "var n = node(which === 'ab' || which === 'ar' ? 'a' : which)(), s = sz || kaoWH(); if (!n) return null;" in _js190r   # 追記㉙: 'ar' = A's upper right, a stop, not a place it lives   # 追記㉘ (the user: A上面太上面了，位移的時候壓到按鈕沒關係，停下來不要壓到就好 / 回到A搬運的那個顏文字讓他移到A下面): 8 px off A, a second place below it   # 追記㉕→㉖ (the user: 位置不要跑來跑去 / 移動採直線): above A / left of B (the line's end) / left of C at its top edge
   and "if (which === 'c') return { x: n.left - 8 - s.w / 2, y: n.top + s.h / 2 + 2 };" in _js190r and "return underLine('lb', s)(1);" in _js190r
   and "else if (kao.home) { try { p = kao.home(); } catch (err) { } }" in _js190r and "phase = 'hand'; hide(); graphOut(); kaoOff();" in _js190r and "unlock(); kaoOff();" in _js190r
   and _js190r.count("kaoOff()") == 8)   # 追記㉚: + rageEnd (the tantrum's face goes with it, however it ends)   # round 5: + kaoStep (the last ride runs off the stage)   # kaoMake (the old face goes as the new one comes), kaoOff itself, handBack, end - nowhere else: nothing on a timer takes it
ok("LOG-190 追記㉔ ②: a ride is three legs back to back - dock -> the light's start (KAO_LEAD), the light's own run (main: under the line on ARC_LEAD, or beside the spark on 0.45 s and its easing), the far node round into its dock (KAO_SETTLE) - the pair turns round as the main leg ends, and a line that arrives mid-ride waits for the face to settle",
   "function kaoOutLegs(key) {" in _js190r and "function kaoHomeLegs(ride, land) {" in _js190r and "poly([dA" not in _js190r and "corner(u0" not in _js190r and "corner(dB" not in _js190r and "ptAt(" not in _js190r   # 追記㉖ (poly([0, 2, 4, 0] at ~1669 is the boot screen's own, unrelated) (the user: 所有顏文字移動採直線，不要再像現在一樣有多次轉彎): no polylines, no elbows
   and "function hop(p0, p1, eased) {" in _js190r
   and "return { legs: [{ at: hop(kaoDock(kaoSlot), kaoDock(to), false), d: ARC_LEAD, main: true }], home: kaoDock(to), slot: to, lead: 0 };" in _js190r
   and "var to = land === 'a' ? 'ab' : land;" in _js190r and "return { legs: [{ at: hop(kaoDock(from), kaoDock(to), true), d: 0.45, main: true }], home: kaoDock(to), slot: to, lead: 0 };" in _js190r   # 追記㉘: home lands below A; out sets off from wherever it stands
   and "kao.legs = legs.legs; kao.home = legs.home; kaoSlot = legs.slot; kao.moving = true;" in _js190r
   and "var L = face && face.off ? kaoOffLegs() : face ? kaoCarryLegs(ride, land) : kaoHomeLegs(ride, land); at(Math.max(T(), seam - 0.45 - L.lead), function () { if (L.off) bubFree = true; else bubOut(); kaoRide(face || null, L); });" in _js190r
   and "if (ride === 'acb') q.x += kaoWH().w / 2 + 8; else q.y -= 22; return q;" in _js190r
   and "if (kao && kao.moving) kao.then.push(run); else run();" in _js190r
   and "if (i === L.length - 1 && k >= 1) { kao.legs = []; kao.moving = false; var th = kao.then; kao.then = []; th.forEach(function (fn) { try { fn(); } catch (err) { } }); }" in _js190r)
# ---- LOG-190 追記㉙ (the user: 顏文字B-A時動作改成「顏文字移動回A時貼緊我們transition的那個光點的下面(像是顏文字搬著光點把他丟過去)，然後停止時先站在A的正左邊變換完這個顏文字的特殊轉換動作後，才進到下一個表情並退到現在該有的地方(用原本你用的那個轉彎繞過去的方式，僅限這個)」)
ok("LOG-190 追記㉙: the first ride home (the pair) carries the spark - under it on its own rail, easing and 0.45 s (underSpark) - peels off to A's upper right over the rail's last 30 % (carryIn; kaoDock 'ar': KAO_A_GAP off A's right edge, its middle at A's top - the user: 停在右上方，看起來像是站在那裏把光點往A裡面丟) and turns round the instant the spark drops into A (the main leg marked turn; kaoStep turns at turnI, = mainI when nothing is marked), (round 4, the user: 最後一拍時就變(／・ω・)／，看起來像是搬起來然後跑 - a beat before that it turns up (／・ω・)／ beside B and walks to the light's start, KAO_PICK) stands .4 s, then takes the one L the user kept (elbow: down A's right side, along under A leftward) .35 s to below A (ab), where sd7 goes up in (ﾟ∀。) the instant it arrives; the other rides home are untouched (kaoHomeLegs, one straight segment)",
   "function underSpark(ride) {" in _js190r and "return { x: c.x, y: r.y + 2 + kaoWH().h / 2 };" in _js190r and "r = svgPt({ x: q.x, y: q.y + 6 });" in _js190r
   and "function elbow(p0, p1) {" in _js190r and "function kaoCarryLegs(ride, land) {" in _js190r
   and "if (which === 'ar') return { x: n.right + KAO_A_GAP + s.w / 2, y: n.top };" in _js190r
   and "return { legs: [{ at: hop(kaoDock('b'), u0, false), d: KAO_PICK }, { at: carryIn(ride, ar), d: 0.45, main: true, turn: true }, { at: hop(ar, ar, false), d: 0.4 }, { at: elbow(ar, kaoDock(to)), d: 0.35 }], home: kaoDock(to), slot: to, lead: KAO_PICK };" in _js190r and "var KAO_PICK = 60 / 145;" in _js190r and "function carryIn(ride, stop) {" in _js190r and "w = w * w * (3 - 2 * w);" in _js190r
   and "if (kao.legs[i].main) kao.mainI = i; if (kao.legs[i].turn) kao.turnI = i;" in _js190r and "if (kao.turnI < 0) kao.turnI = kao.mainI;" in _js190r and "turnI: -1," in _js190r
   and _js190r.count("kaoCarryLegs(") == 2 and _js190r.count("elbow(") == 2 and _js190r.count("underSpark(") == 3 and _js190r.count("carryIn(") == 2 and _js190r.count("kaoDock('ar')") == 1
   and "function kaoOffLegs() {" in _js190r and "var KAO_RUN_C = { a: 'ε≡ﾍ( ´∀`)ﾉ', b: KAO_LINE.tut_sd12 };" in _js190r and "var KAO_RUN_OFF = { a: 'ﾚ(ﾟ∀ﾟ;)ﾍ=З=З=З', b: 'ﾚ(ﾟ∀ﾟ;)ﾍ=З=З=З', off: true };" in _js190r
   and "if (kao.off && i === L.length - 1 && k >= 1) { bubOut(); kaoOff(); return; }" in _js190r and "if (!bubFree) left = Math.max(10, Math.min(Math.max(10, W - bw - 10), left));" in _js190r and "bubGap = o.gap || 12; bubFree = false;" in _js190r
   and "kao.off = !!legs.off;" in _js190r and _js190r.count("kaoOffLegs(") == 2)   # round 5 (the user: 移動的顏文字不可以以靜止的狀態存在 / 跑出左邊的邊界消失): the running faces only on the runs; the last ride off the stage, line and all   # defined once, used once each - the L and the carry belong to this one ride only
ok("LOG-190 追記㉔ ③: every demonstration line hangs on the face and opens away from the board - A's below and to the LEFT (align 'right': the bubble's right end at the face, so it never reaches C), B's above, C's below and to the right; a bubble that fades on a hand-over lets go of its anchor",
   "bubAlign === 'right' ? cx + 28 - bw : cx - 28" in _js190r and "bubAlign = o.align === 'right' ? 'right' : 'left';" in _js190r
   and "o.side = which === 'c' || kaoSlot === 'ab' ? 'below' : 'above'; kaoFace(KAO_LINE[key]); show(key, o.at, o);" in _js190r   # 追記㉘: below the face under A too   # 追記㉕: all open to the left; A's and B's above the face, C's below; B's lifted 32 px clear of A's top (20 was 4.7 px into it)
   and "bubGap = o.gap || 12;" in _js190r and "r.top - bubGap - bh" in _js190r
   and "at: kaoBox, side: 'above'" not in _js190r and "hide(); bubTgt = null; bubSrc = null; }" in _js190r)
# ---- LOG-190 追記㉗ (the user: 取消隨機顏文字，改成我在每個台詞後面備註使用的顏文字跟內容修改備註 - ABC演示_顏文字與台詞清單.md)
_KAO_LINE = {"tut_sd1": "(ゝ∀･)", "tut_sd2": "(*ﾟ∀ﾟ*)", "tut_sd3": "(ゝ∀･)b", "tut_sd4": "(*´･д･)?", "tut_sd5": "(´・Å・`)", "tut_sd6": "(ﾟ∀ﾟ)", "tut_sd7": "(ﾟ∀。)", "tut_sd8": "(ﾟ∀。)", "tut_sd9": "(ﾟ∀。)", "tut_sd10": "(ﾟ∀。)",
             "tut_sd11": "(ﾟ∀。)", "tut_sd12": "(ﾟ∀。)", "tut_sd13": "(ﾟ∀。)", "tut_sd14": "(*ﾟ∀ﾟ*)", "tut_sd15": "(σ`∀´)σ", "tut_s9e": "(ﾟ∀。)", "tut_s9g": "(ﾟ∀。)"}   # the user's list, line by line
_kl = re.search(r"var KAO_LINE = \{ (.*?) \};", _js190r)
_kl_js = dict(re.findall(r"(tut_\w+): '([^']+)'", _kl.group(1))) if _kl else {}
ok("LOG-190 追記㉗: no random face - each of the 17 demonstration lines wears the face the user wrote after it (KAO_LINE, exactly the list), put on as the line goes up (kaoFace before show); a ride keeps its face (kaoRide(null, …)) except the first B -> A, which the user gave the pair - (／・ω・)／ out, ＼(・ω・＼) on landing (KAO_PAIR_RL); the narrator stands in sd1's face; sd11 follows sd10 sooner (3.9 s before the seam, the user: 這邊早點接下一句); A's place is as low as the rides allow (kaoAUp over the four riding faces)",
   _kl_js == _KAO_LINE and all(k in site["ui"] for k in _KAO_LINE)
   and "var run = function () { if (phase === 'demo') { o.side = which === 'c' || kaoSlot === 'ab' ? 'below' : 'above'; kaoFace(KAO_LINE[key]); show(key, o.at, o); if (o.then) o.then(); } };" in _js190r
   and "function kaoFace(t) { if (kao && t && kao.el.textContent !== t) kao.el.textContent = t; }" in _js190r
   and "var KAO_PAIR_RL = { a: '(／・ω・)／', b: '＼(・ω・＼)' };" in _js190r and _js190r.count("sparkAtSeam('ab', 'a', 'rl', KAO_PAIR_RL)") == 1 and _js190r.count("sparkAtSeam('ab', 'a', 'rl', KAO_RUN_OFF);") == 1
   and "function kaoMake(f) {" in _js190r and "var t = kao ? kao.el.textContent : KAO_LINE.tut_sd1; kaoOff(); if (!head) return null;" in _js190r and "if (!f) f = { a: t, b: t };" in _js190r
   and "Math.random" not in _js190r.split("var KAO_LINE", 1)[1][:9000] and "mode: force || (Math.random() < REW_ODDS ? 'rewind' : 'egg')" in _js190r   # 追記㉛: the only draw in the lesson is which ending the tantrum gets - never which face a line wears
   and "sayBefore(ARC_LEAD + 3.6, 'tut_sd11', 'a');" in _js190r and "sayBefore(ARC_LEAD + 2.4, 'tut_sd11', 'a');" not in _js190r
   and "function kaoAUp" not in _js190r and "function kaoSize" not in _js190r and "kaoAUp()" not in _js190r   # 追記㉘: the ride-clearance height is gone - a ride may cross A, the standing face sits 8 px off it
   and site["ui"]["tut_sd4"]["en"].startswith("Huh?") and "ur right" in site["ui"]["tut_sd5"]["en"] and not _CJK.search(site["ui"]["tut_sd4"]["en"] + site["ui"]["tut_sd5"]["en"]))   # LOG-190 追記㉚: the author's own English (文法部分有一個 "ur" 這個留著)
# ---- LOG-190 追記⑫-② (the user: LOOP 出去接 END 聽到兩個 kick → 對得很準，就按照你這樣做)
_iloop = next((s for s in _tu_all_segs if s.get("id") == "I_loop"), None) if (_tu_all_segs := [s for t in json.loads((ROOT / "demos" / "interactive-player" / "segments.json").read_text(encoding="utf-8"))["themes"] for s in t.get("segments", [])]) else None
ok("LOG-190 追記⑫-②: I_loop starts leadSec = 0.02909 s late (its kick is at sample 0, every other file's 29.09 ms after the bar line) and schedule() adds it to the source's start only - the grid stays",
   _iloop is not None and abs(_iloop.get("leadSec", 0) - 0.02909) < 1e-6
   and "src.start(entry - pre + (seg.leadSec || 0), (seg.trimStart || 0)" in _js190
   and "return { seg: seg, start: entry, end: entry + logicalSec(seg), audioStart: entry - pre," in _js190)
# ---- LOG-190 追記⑪ (the user: A-B A-C 中間不要有預覽的灰箭頭 / B 回 A 時多一句「即使是回到開頭…」，結束這句話時剛好回 A)
ok("LOG-190 追記⑪ as superseded by 追記⑲: tut_s9h (and sayBySeam, its only caller) is retired - 「也可以正常地返回 A。」 says it as A2 enters; show() still types a line over o.dur (追記⑲'s o.from went with 追記⑳'s split of tut_sd1)",
   "function sayBySeam(" not in _js190 and "'tut_s9h'" not in _js190 and "show('tut_s9c'" not in _js190
   and "per: o.dur ? o.dur / Math.max(1, text.length) : rate(text.length, 3.0, 0.032)" in _js190 and "o.from" not in _js190)
# ---- LOG-190 追記⑩ (the user: 演示完 C-B 之後多一句「這個就是…branching 技巧」)
ok("LOG-190 追記⑩ (＋追記⑲: under B, after 很好 / tut_s9e): after C -> B the lesson names what it showed - tut_s9g goes up 6.5 s into the B1 that came from C, only while the demonstration is still on, bilingual, and says branching",
   "soon(8.0, function () { sayOn('tut_s9g', 'b'); });" in _js190 and _js190.count("'tut_s9g'") == 1
   and "kaoFace(KAO_LINE[key]); show(key, o.at, o); if (o.then) o.then(); } };" in _js190   # 追記㉔: on the face; 追記㉗: in the line's own face; 追記㉛: o.then = the line actually went up (a line held by a ride starts its successor from there)
   and isinstance(site["ui"].get("tut_s9g"), dict) and "branching" in site["ui"]["tut_s9g"]["zh"]
   and "branching" in site["ui"]["tut_s9g"]["en"] and not _CJK.search(site["ui"]["tut_s9g"]["en"]))
# ---- LOG-190 追記⑨ (the user: 提早並延長 crossfading 讓他聽起來不要像是瞬間轉換 / 解釋完前段後段可以隨機排列之後就可以直接隱藏)
_XF9 = 60.9 - 54.0              # 追記⑩: 6.9 s - the demonstration's blend, from the top of the pass to demoStart
_CUT_POS9 = 54.0 - 53.73        # 追記⑩: where the cut enters the pass (0.27 s in; the pass is heard from L1 + 53.73)
_L9 = 13.24138                  # one pass of the opening loop
ok("LOG-190 追記⑨ (the user: 提早並延長 crossfading 讓他聽起來不要像是瞬間轉換): the blend's LENGTH is the caller's - releaseMid(beats) / E.releaseNow(beats) / E.xfSec(beats) all fall back to RELEASE_XF_BEATS (5, the visitor's press) and the lesson passes XF_DEMO_BEATS = 10 ({:.2f} s). It still lands clear: entering at pos {:.2f} s leaves {:.2f} s of the pass, the crossfade ends at +{:.2f} s and A2's decision point at +{:.2f} s".format(_XF9, _CUT_POS9, _L9 - _CUT_POS9, _XF9, _L9 - _CUT_POS9 - 3.5603),
   "function releaseMid(beats) {" in _js190
   and "xf = (beats || RELEASE_XF_BEATS) * (60 / (RELEASE.bpmIn || 120));" in _js190
   and "releaseNow: function (beats) { return !!releaseMid(beats); }," in _js190
   and "xfSec: function (beats) { return (beats || RELEASE_XF_BEATS) * (60 / (RELEASE && RELEASE.bpmIn || 120)); }," in _js190
   and "var RELEASE_XF_BEATS = 5;" in _js190 and "var XF_FROM = 54.0;" in _js190 and "var XF_DEMO_BEATS = (60.9 - XF_FROM) / E.xfSec(1);" in _js190 and "var XF = E.xfSec(XF_DEMO_BEATS);" in _js190
   and _XF9 + 3.5603 < _L9 - _CUT_POS9)
ok("LOG-190 追記⑨ (the user: 解釋完前段後段可以隨機排列之後就可以直接隱藏，不用先重新顯現出全部的按鈕後再一起隱藏): the swap demonstration's hide only hides - A / E / I and the zone marks stay behind the curtain that demoStart is about to draw, and are put back in handBack(), just before the row is let out for 「換你了」",
   "at(L1 + 60.6, function () { hide(); E.hint(false); });" in _js190
   and not re.search(r"at\(L1 \+ 60\.6, function \(\) \{[^\n]*E\.reveal", _js190)
   and "        E.reveal('tile', 'A'); E.reveal('tile', 'E'); E.reveal('tile', 'I'); E.reveal('zones');\n        demoLights(false);" in _js190
   and "phase = 'hand'; hide(); graphOut(); kaoOff();" in _js190 and "phase = 'hand'; hide(); graphOut(); demoLights(false);" not in _js190)   # 追記㉔: the narrator goes with the board
# ---- LOG-190 追記⑥ (the user: 開場第二句跟上一句短暫重疊 / A 跟 I 縮到中間碰在一起、E 出現把兩顆擠開)
ok("LOG-190 追記⑥ (the user: 現在開始概念及使用教學 跟上一句會有短暫的重疊): narration fades in .7 s (.wm.wsay.out), subtitle() sends the standing line out as the next arrives, and tut_open2 waits until 5.6 s",
   ".wm.wsay.out{transition:opacity .7s ease}" in _css190 and ".wm.out{opacity:0!important;transition:opacity 1.5s ease}" in _css190
   and "subs.forEach(function (s) { if (!s.out) { s.out = 1; s.h.out(); } });" in _js190
   and "at(5.6, function () { subtitle('tut_open2', { greet: true }); });" in _js190 and "at(5.0, function () { subtitle('tut_open2'" not in _js190
   and "at(1.0, function () { subtitle('tut_open1', { greet: true }); });" in _js190)
ok("LOG-190 追記⑥ (the user: A 跟 I 縮進去那邊是直接縮到兩個在中間碰在一起，然後 E 出現之後把這兩個擠開的感覺): s2b glides A and I to E's centre line half a tile apart (meetX), s2b2 grows E and pushes them onto D / F with E's own .6 s bounce, s2b home is untouched",
   "function meetX(g, sign) {" in _js190 and "var w = t.offsetWidth || 58;" in _js190 and "return ((e.left + e.width / 2) + sign * (w + MEET_GAP) / 2) - (a.left + a.width / 2);" in _js190 and "var MEET_GAP = 14;" in _js190   # 追記⑧: a 14 px breath between them
   and "function slideBy(g, x, tr) {" in _js190 and "t.style.transform = 'translateX(' + x.toFixed(2) + 'px)';" in _js190
   and "var PUSH = 'transform .6s cubic-bezier(.34,1.56,.64,1)';" in _js190
   and "slideTo('A', 'D', PUSH); slideTo('I', 'F', PUSH);" in _js190.split("at(L1 + 29.6, function () {", 1)[1].split("at(L1 + 32.0", 1)[0]
   and "slideHome('A'); slideHome('I');" in _js190)
# ---- LOG-190 追記⑥ (the user: 「接上了」這句綁在接續 A 後面播放的段落，而不是綁在 B 上面)
ok("LOG-190 追記⑥ (the user: 接上了 這句綁在接續 A 後面播放的段落，而不是綁在 B 上面): tut_s_seam fires on the run's first non-bookend (ver 1) entry - whatever actually follows the opening - and `first` is re-pointed there for steps 12 + 13; it is no longer the unshuffled line's B",
   "if (i.ver === 1 && !(first && first.seen)) { first = { group: i.group, id: i.id, seen: true }; show('tut_s_seam', lineRect, { side: 'above' }); return; }" in _js190
   and "if (first && i.id === first.id) { show('tut_s_seam'" not in _js190
   and "if (first && i.group === first.group && i.ver === 2) {" in _js190
   and "group: isBookend(cur.seg) ? cur.seg.id : cur.seg.group, ver: isBookend(cur.seg) ? 0 : verIdx(cur.seg) + 1" in _js190)
ok("LOG-189 追記③⑶ (the user: 演示圖去程回程光動畫對調): the routes have lit twins that blink at the decision and wipe out from A at the seam; the spark rides the return arcs' bare rails home and lights the node it lands on",
   all(t in _js189 for t in ('class="arc lb" pathLength="1" d="M99.6 100 L256.5 73.6', 'class="arc lc" pathLength="1" d="M99.6 110 L256.5 136.4',
                             'class="rail ab" d="M286 40 Q180 2 76 76"', 'class="rail ac" d="M286 170 Q180 208 76 134"', 'class="rail acb" d="M322 130 Q368 105 322 80"',
                             "var p = graphEl ? graphEl.querySelector('.rail.' + ride) : null", "q = spark.p.getPointAtLength(spark.len * k)",
                             "sparkTo(ride, function () { nodeNow(land); });",   # LOG-190 追記⑪: no .dim preview arc any more
                             "atSeam(2.8, function () { route('b', true); arcPre('lb'); });",
                             "else if (i.id === 'C1') { route('c', true); arcPre('lc'); goOut('lc', 'lr'); }",
                             "else if (i.id === 'A2' && demoLeg === 1) sparkAtSeam('ab', 'a', 'rl', KAO_PAIR_RL);",   # 追記㉗: the first ride home in the pair
                             "else if (i.id === 'A2' && demoLeg === 1) { demoLeg = 2; if (!spark) nodeNow('a'); route('b', false);"))
   and "GN[which]" not in _js189 and "arcPre('b')" not in _js189 and "arcGo('cb')" not in _js189
   and ".arc.dim" not in _css189 and "arcDim" not in _js189 and ".desktop .stage-ui .tut-graph .rail{fill:none;stroke:none" in _css189   # LOG-190 追記⑪: the grey preview arc is gone
   and "transition:opacity .4s ease}" in _css189.split(".desktop .stage-ui .tut-graph .arc{", 1)[1].split("\n", 1)[0])
ok("LOG-188 追記① (the user: 直接切割並以彈回特效變回字母按鈕／變成音訊條時也要彈入): .cut parts the blocks without names, .bounce overshoots the tiles, .whole pops the bar",
   ".desktop .stage-ui .tut-clip.split .bar i,.desktop .stage-ui .tut-clip.cut .bar i{margin:0 3px;" in _css188 and ".tut-clip.cut .bar i span" not in _css188
   and ".ds-secs.simple.bounce .seg.tile{transition:opacity .45s ease,transform .6s cubic-bezier(.34,1.56,.64,1)," in _css188 and ".desktop .stage-ui .tut-clip.whole .bar{animation:tutpop" in _css188)
ok("LOG-188 (the user: 泡泡的字體都放大一點): the bubble is 14 px now, and a little wider to match",
   "font:14px/1.55 var(--font)" in _css188 and "font:12.5px/1.55 var(--font)" not in _css188 and ".tut-bub{position:absolute;left:0;top:0;width:max-content;max-width:340px;" in _css188)
ok("LOG-188 (the user: 教學途中隱藏最近更新／便條／dock): os.js wears .tut-on for the lesson, os.css takes the three away on it and brings them back on .tut-back",
   "HOST.classList.add('tut-on');" in _js188 and "HOST.classList.remove('tut-on'); HOST.classList.add('tut-back');" in _js188
   and ".desktop.tut-on>.updates{transform:translateX(-140px);opacity:0;pointer-events:none}" in _css188
   and ".desktop.tut-on>.dock{transform:translate(-50%,120px);opacity:0;pointer-events:none}" in _css188
   and ".desktop.tut-on>#sticky{opacity:0;transform:translate(24px,-40px) rotate(-14deg);pointer-events:none}" in _css188
   and ".desktop.tut-back>.updates,.desktop.tut-back>.dock{transition:" in _css188)


# ---- report
_demo191 = (ROOT / "demos" / "interactive-player" / "demo.js").read_text(encoding="utf-8")
ok("LOG-191 (使用者: 最小化或失去焦點時再重新打開會有畫面不同步或是卡住…一個切換之後整個螢幕都跟不上): one place knows the page went away, the lesson's backlog comes back in TIME order, and nothing waits on a wall clock",
   "var VIS = (function () {" in _js182
   and all(t in _js182 for t in (
       "document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') hide(); else show(); });",
       "window.addEventListener('pageshow', function () { if (document.visibilityState !== 'hidden') show(); });",
       "window.addEventListener('pagehide', hide);",
       "on: function (o) { subs.push(o); return function () { var i = subs.indexOf(o); if (i >= 0) subs.splice(i, 1); }; },",
       # the lesson: due steps in time order, with a guard against a step that schedules something already due
       "for (var guard = 0; guard < 600; guard++) {",
       "for (i = 0; i < q.length; i++) if (q[i].t <= t && (bi < 0 || q[i].t < q[bi].t)) bi = i;",
       "visOff = VIS.on({", "if (visOff) { visOff(); visOff = null; }",
       # LOG-191-② (使用者: 還是會慢…背景時還是會計算座標): the lesson keeps stepping on a timer while away, and lands synchronously on the way back
       "awayT = setInterval(function () { try { step(); } catch (e) {} }, 250);",
       "if (awayT) { clearInterval(awayT); awayT = 0; }",
       "      function step() {", "raf = alive ? requestAnimationFrame(pump) : 0;", "        step();",
       "VIS.on({ show: function () { if (raf) cancelAnimationFrame(raf); raf = 0; draw(); } });",
       # the transport: one tick straight away, because a hidden tab clamps its 25 ms poll to a second
       "VIS.on({ show: function () { try { if (!dead && running && ctx && cur) tick(); } catch (e) {} } });",
       # the boot terminal: whole lines while hidden, not one clamped second a character
       "if (document.hidden) { span.textContent = text; cur.remove();",
       "setTimeout(cb, reduced || document.hidden ? 0 : 350);",
       # the closing chart: shuffle writes carry the layout they were made against
       "fin.spread = true; fin.gen = (fin.gen || 0) + 1;",
       "function apply() { if ((fin.gen || 0) !== gen) return;"))
   and _js182.count("VIS.on(") == 8   # the lesson, the transport, the wave/waterfall loop, the piano stage, the dock panels, the wishing well, the pillar, and (LOG-204) the play-line captions
   and "q.splice(i, 1); i--; try { fn(); }" not in _js182   # the old insertion-order drain is gone
   and "byeTimer = setTimeout" not in _demo191 and "byeAt = cur.end - 2 * barSec(OUTRO.bpmOut);" in _demo191
   and "if (byeAt !== null && now >= byeAt) { byeAt = null; document.body.classList.add('bye'); }" in _demo191)

fails = [r for r in results if not r[0]]
for okk, name, msg in results:
    print(("PASS  " if okk else "FAIL  ") + name + (f"  — {msg}" if msg and not okk else ""))
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
