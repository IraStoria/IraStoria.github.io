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
ok("contact services: four, bilingual", len(site["contact"]["services"]) == 4 and all(not _CJK.search(s["label"]["en"] + s["desc"]["en"]) for s in site["contact"]["services"]))
expect_refused("backend.url with a trailing slash refused", lambda: B.backend_url({"backend": {"url": "https://pool.example.workers.dev/"}}), "trailing")
expect_refused("backend.url without a scheme refused", lambda: B.backend_url({"backend": {"url": "pool.example.workers.dev"}}), "http(s)")
ok("backend.url empty is fine (not wired)", B.backend_url({"backend": {"url": ""}}) == "")
ok("worker contract + script + deploy guide present", all((ROOT / "worker" / f).exists() for f in ("API.md", "worker.js", "DEPLOY.md", "test_worker.mjs")))
ok("worker.js carries no secret", not re.search(r"(ghp_|gho_)[A-Za-z0-9]{20,}|client_secret\s*[:=]\s*['\"][^'\"]{8,}", (ROOT / "worker" / "worker.js").read_text(encoding="utf-8")))


# ---- report
fails = [r for r in results if not r[0]]
for okk, name, msg in results:
    print(("PASS  " if okk else "FAIL  ") + name + (f"  — {msg}" if msg and not okk else ""))
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
