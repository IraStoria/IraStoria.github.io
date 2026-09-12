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
dj = ROOT / "demos" / "transition" / "demo.json"
orig = dj.read_text(encoding="utf-8")
try:
    d = json.loads(orig); d["concept_level_checked"] = False
    dj.write_text(json.dumps(d), encoding="utf-8")
    expect_refused("demo without concept check refused", lambda: B.load_demos(works), "concept_level_checked")
    d = json.loads(orig); d["title"].pop("en")
    dj.write_text(json.dumps(d), encoding="utf-8")
    expect_refused("demo missing en title refused", lambda: B.load_demos(works), "missing or empty 'en'")
finally:
    dj.write_text(orig, encoding="utf-8")

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
         "tut_s9a", "tut_s9b", "tut_s9c", "tut_s9d", "tut_s9e", "tut_s_turn",
         "tut_s13a", "tut_s13b", "tut_s_end2", "tut_end", "tut_again", "tut_clip_song", "tut_clip_parts")
ok("LOG-183: every line of the new script is there, bilingual, English free of Chinese",
   all(isinstance(site["ui"].get(k), dict) and site["ui"][k].get("zh") and site["ui"][k].get("en") and not _CJK.search(site["ui"][k]["en"]) for k in _s183),
   str([k for k in _s183 if k not in site["ui"]]))
ok("LOG-183: the superseded lines are gone (no orphan strings)",
   not any(k in site["ui"] for k in ("tut_skip", "tut_s_song", "tut_s_zones", "tut_s_go", "tut_s_yours")))
_used183 = _js182  # os.js, already read above
# what the lesson can actually put on screen: U.<key>, and the keys handed to show / subtitle / once / relay
_shown183 = set(re.findall(r"U\.(tut_[a-z0-9_]+)", _used183)) | set(re.findall(r"(?:show|subtitle|once)\('(tut_[a-z0-9_]+)'", _used183))
for _rl in re.findall(r"relay\(\[([^\]]*)\]", _used183):
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
   "var(--fc,rgba(255,255,255,.92))" in _css182 and ".tut-graph.lit-b .rb,.desktop .stage-ui .tut-graph.lit-c .rc{stroke:rgba(255,255,255,.95)" in _css182)
ok("LOG-183 追記③ (the user: 網站不准出現無 transition 的瞬間變化): every lesson rule that declares a transition out-specifies the shell sweep (three classes)",
   all(t in _css182 for t in (".desktop .stage-ui .tut-frame{", ".desktop .stage-ui .tut-frame.glide{", ".desktop .stage-ui .tut-iris{", ".desktop .stage-ui .tut-clip .bar i{", ".desktop .stage-ui .tut-graph .rt{", ".desktop .stage-ui .tut-graph .nd{", ".desktop .stage-ui .tut-graph .nd.now{"))
   and not re.search(r"\n\.stage-ui \.tut-(frame|iris|clip|graph)[^{]*\{[^}]*transition:", _css182))
ok("LOG-183 追記③: the rings ride the tiles in fractional pixels, glide to E and take its colour, the clip splits on its own sentence, both halves shuffle together",
   all(t in _js182 for t in (".toFixed(2) + 'px'", "function reframe(f, target, o)", "function dropFrame(f)", "reframe(fA, e, { dur: 0.36, color: ec", "show('tut_s1a2', clipBox); clipSplit();",
                             "zoneFrame('pre'); zoneFrame('post'); show('tut_s_swap_pre', E.surface()); });", "el.getBoundingClientRect();   /* 追記③"))
   and "tut_s_swap_post" not in _js182 and "tut_s_swap_post" not in site["ui"])
ok("LOG-183 追記③: the greeting keeps only the row's box clear, the asides keep the bubble's reserve, and an authored line breaks only where the author broke it",
   "subtitle('tut_open1', { greet: true })" in _js182 and "o.greet ? 8 : null" in _js182 and ".wm.wsay .wq{white-space:pre-line}" in _css182
   and "\n" in site["ui"]["tut_open2"]["zh"] and "\n" in site["ui"]["tut_open2"]["en"])
ok("LOG-183 追記③ (the user: 切塊標籤要中文): the nine section names are Chinese in zh, and the track name is a header to the left of the region",
   len(site["ui"]["tut_clip_parts"]["zh"].split("|")) == 9 and _CJK.search(site["ui"]["tut_clip_parts"]["zh"]) is not None
   and ".desktop .stage-ui .tut-clip .cap{position:absolute;right:100%" in _css182 and "clipEl.style.setProperty('--bt'" in _js182)
ok("LOG-183 追記④ (the user: 一區一個獨立泡泡隨白框一起往右移動): step 2 is a heading plus five zone names, one ring and one sliding bubble walk them",
   "|" not in site["ui"]["tut_s2a"]["zh"] and len(site["ui"]["tut_s2z"]["zh"].split("|")) == 5 and len(site["ui"]["tut_s2z"]["en"].split("|")) == 5
   and "show('tut_s2z', tgt, { piece: k, instant: true, slide: k > 0, again: true });" in _js182 and "reframe(fZ, tgt, { dur: 0.34, pad: tile ? 5 : 8" in _js182
   and "if (o.piece != null) { text = pieces[o.piece] || ''; pieces = [text]; }" in _js182 and "bub.classList.toggle('slide', !!o.slide);" in _js182)
ok("LOG-183 追記②: no other lesson line carries a stray '|'",
   not any("|" in site["ui"][k].get("zh", "") for k in _s183 if k not in ("tut_s2a", "tut_clip_parts")))
ok("LOG-183 追記④: the rings turn E's colour on the way (early), the arrows wave three times and the tiles wave with them, the tile that sounds fills with its colour",
   "reframe(fA, e, { dur: 0.36, color: ec, early: 0.1" in _js182 and "if (o.color && o.early != null) soon(o.early" in _js182
   and "for (var k = 0; k < 3; k++)" in _js182 and ".ds-secs.simple.arrows.awave .col .seg.tile{animation:tuttile" in _css182
   and "t.style.setProperty('--c', isCur ? segColor(cur.seg) :" in _js182 and ".ds-secs.simple .seg.tile.playing{background:color-mix" not in _css182)
ok("LOG-183 追記④ (the user: 先講、再動，兩區交錯): steps 7/8 speak first and the two halves move on different beats, in opposite orders",
   all(t in _js182 for t in ("at(68.4, function () { E.move('pre', 'D', 0); });", "at(69.0, function () { E.move('post', 'H', 0); });",
                             "at(71.4, function () { E.move('post', 'H', 2); });", "at(72.0, function () { E.move('pre', 'D', 2); });")))
ok("LOG-183 追記④: the graph has 56 px nodes on a 360 x 190 board, a spark that lights the node it lands on, and two return arcs that blink then wipe",
   all(t in _js182 for t in ("viewBox=\"0 0 360 190\"", "class=\"arc ab\" pathLength=\"1\"", "class=\"arc ac\" pathLength=\"1\"", "<circle class=\"spk\"",
                             "function sparkTo(which, then)", "function sparkAtSeam(which)", "sparkAtSeam('b')", "sparkAtSeam('c')", "show('tut_s9d', node('c'))",
                             "function arcPre(which)", "function arcGo(which)", "arcPre('b')", "arcPre('cb')", "arcGo('b')", "arcGo('cb')", "irisStep(); sparkStep();"))   # LOG-186: the C arc is now C -> B ('cb'); the C -> A arc is drawn but never lit
   and all(t in _css182 for t in (".desktop .stage-ui .tut-graph .nd{position:absolute;width:56px;height:56px", ".desktop .stage-ui .tut-graph .spk{", ".desktop .stage-ui .tut-graph .arc{",
                                  "@keyframes tutarcpre", "@keyframes tutarcgo", "stroke-dashoffset:-1")))
ok("LOG-183 追記④ (the user: 換你了改用 iris 只留 A 亮，太久沒點出四句催趕): the hand-over closes the iris on A, the press opens it, four nudges in order, 13b points at the second half",
   "irisIn(E.btn('A'), 48)" in _js182 and "unframeAll(); irisOut();" in _js182
   and "['tut_nudge1', 'tut_nudge2', 'tut_nudge3', 'tut_nudge4'].forEach" in _js182 and "if (phase === 'hand') show(k, E.btn('A'), { again: true });" in _js182
   and all(isinstance(site["ui"].get(k), dict) and site["ui"][k].get("zh") and site["ui"][k].get("en") and not _CJK.search(site["ui"][k]["en"]) for k in ("tut_nudge1", "tut_nudge2", "tut_nudge3", "tut_nudge4"))
   and "讓子彈飛" in site["ui"]["tut_nudge4"]["zh"] and "show('tut_s13b', zoneBox('post'), { hold: 14 });" in _js182)
ok("LOG-183 追記④: the demonstration's narration was rewritten with a lead-in (先從開頭 A 出發／注意聽)",
   "注意聽" in site["ui"]["tut_s9a"]["zh"] and "Listen" in site["ui"]["tut_s9a"]["en"] and "C2 開播時就繼續教學" not in _js182)
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
ok("LOG-185: 43 rules (44 from the plan, C2>A emptied by the user's 「B回A不+1, C回A不+1」), one per pair, every end a section id / A / I", len(_rules) == 43 and len(set(_pairs)) == 43 and all(f in _sids and t in _sids for f, t in _pairs))
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
   _byp[("A", "C1")]["random"] == [{"layers": []}, {"layers": ["TR"]}, {"layers": ["TR", "DRUM"]}, {"layers": ["TR", "DRUM"], "noPre": True}]   # 「A到C1不+1」: the two HIT1 variants collapsed into their drum-only twins
   and _byp[("A", "D1")]["layers"] == ["TR", "DRUM"] and _byp[("A", "D1")]["mute"] == {"TR": [3]}
   and _byp[("C1", "B1")]["random"] == [{"layers": [], "noPre": True}, {"layers": [], "noTail": True, "noPre": True}]   # 「C-B無pre」 (LOG-186)
   and _byp[("F1", "I_loop")]["layers"] == ["HIT1"] and _byp[("F1", "I_loop")].get("noTail") is True
   and _byp[("B1", "A")]["layers"] == ["TR"] and _byp[("B2", "A")]["layers"] == ["TR"] and _byp[("C1", "A")]["layers"] == ["TR", "DRUM"] and ("C2", "A") not in _byp   # 「B回A不+1, C回A不+1」
   and _byp[("I_loop", "F1")]["layers"] == ["HIT1", "G1PRE"] and _byp[("H2", "G1")].get("noPre") is True and _byp[("H2", "G1")]["layers"] == [])
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
   "var RELEASE_XF_BEATS = 2;" in _js186 and "function releaseMid()" in _js186 and "function xfCurves(from)" in _js186
   and "if ((now - loopStart) / L >= 0.5) return false;" in _js186
   and "var loopStart = cur.start + (cur.seg.skipBars ? 0 : (HOLD.openBars || 0) * bar);" in _js186
   and "var at = Math.max(now + 0.05, loopStart), pos = at - loopStart, xf = RELEASE_XF_BEATS * (60 / (RELEASE.bpmIn || 120));" in _js186
   and "src.start(at, (RELEASE.trimStart || 0) + pos);" in _js186
   and "cur = { seg: RELEASE, start: loopStart, end: loopStart + L, audioStart: at, src: src, gain: g, rule: null, mid: pos };" in _js186
   and "og.setValueCurveAtTime(cv.dn, at, xf);" in _js186 and "g.gain.setValueCurveAtTime(cv.up, at, xf);" in _js186
   and "dn[i] = from * Math.cos(k); up[i] = Math.sin(k);" in _js186   # equal-power, not a linear dip (the user: 我明明說 crossfading)
   and "emit('enter', { id: cur.seg.id, group: cur.seg.id, ver: 0, from: lastId, outro: false, mid: pos });" in _js186)
ok("LOG-186: only the lesson's press asks for the mid-pass cut - release(true) from pressA; free simple play and 結束教學 keep the seam path",
   "release: function (mid) {" in _js186 and "if (mid && releaseMid()) return;" in _js186
   and "E.release(true); trail.log('tut', 'press-A');" in _js186 and _js186.count("E.release(true)") == 1
   and "else if (m === 'simple') eng.release();" in _js186 and "E.tutReset(); E.release(); if (!E.holding()) E.layer(true);" in _js186)
ok("LOG-186: the mid-pass cut takes back a scheduled next turn (and its drums) and ramps the sounding pass out with its own drums",
   "var nat = nxt.start; try { nxt.src.stop(0); } catch (e) {}" in _js186
   and "drumSrcs.forEach(function (d) { try { d.stop(at + xf + 0.02); } catch (e) {} }); drumSrcs = [];" in _js186
   and "try { cur.src.stop(at + xf + 0.02); } catch (e) {}" in _js186)
ok("LOG-186 (the user: C-B無pre): every C -> B rule skips B's pick-up - all variants noPre, generated by apply_plan.py's AMEND, not hand-edited",
   all(all(v.get("noPre") is True for v in t["random"]) and t.get("amended", "").endswith("「C-B無pre」") for t in _rules if t["from"][0] == "C" and t["to"][0] == "B")
   and sum(1 for t in _rules if t["from"][0] == "C" and t["to"][0] == "B") == 2)
ok("LOG-186: the demonstration walks A1 -> B1 -> A2 -> C1 -> B1 -> A1 - C2 is out of it",
   "E.force('C2')" not in _js186 and "i.id === 'C2'" not in _js186
   and "else if (i.id === 'C1') { if (!spark) nodeNow('c'); E.force('B1'); }" in _js186
   and "else if (i.id === 'B1' && demoLeg === 2) { demoLeg = 3; nodeNow('b'); arcGo('cb'); route('c', false); show('tut_s9e', graphBox); E.force('A1'); }" in _js186
   and "else if (i.id === 'B1' && demoLeg === 1) { if (!spark) nodeNow('b'); E.force('A2'); }" in _js186
   and "if (demoLeg === 3 && i.id === 'A1') { demoLeg = 4; E.tutReset(); if (phase === 'demo') { nodeNow('a'); arcGo('b'); soon(0.9, handBack); } }" in _js186)
ok("LOG-186: C -> B has its own arc round the right of the board - blinked at the decision with the new line under C, wiped as B enters; the B -> A arc goes twice",
   'class="arc acb" pathLength="1" d="M322 130 Q368 105 322 80' in _js186
   and "else if (i.id === 'B1' && demoLeg === 2) { arcPre('cb'); show('tut_s9f', node('c')); }" in _js186
   and "else if (i.hold && demoLeg === 3) arcPre('b');" in _js186 and "arcPre('c')" not in _js186 and "arcGo('c')" not in _js186
   and isinstance(site["ui"].get("tut_s9f"), dict) and "C也是可以到B" in site["ui"]["tut_s9f"]["zh"]
   and site["ui"]["tut_s9f"]["en"] and not _CJK.search(site["ui"]["tut_s9f"]["en"]))


# ---- report
fails = [r for r in results if not r[0]]
for okk, name, msg in results:
    print(("PASS  " if okk else "FAIL  ") + name + (f"  — {msg}" if msg and not okk else ""))
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
