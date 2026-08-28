#!/usr/bin/env python3
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
        if u.startswith(("http", "mailto:", "#")) or u == "#":
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

# ---- report
fails = [r for r in results if not r[0]]
for okk, name, msg in results:
    print(("PASS  " if okk else "FAIL  ") + name + (f"  — {msg}" if msg and not okk else ""))
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
