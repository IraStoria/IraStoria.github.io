#!/usr/bin/env python3
"""
IraStoria site builder — zero third-party dependencies (ADR-001).

Reads   content/site.json, content/works.json, content/articles/*.md,
        demos/*/demo.json, templates/*.html
Writes  zh/**, en/**, index.html, 404.html, sitemap.xml

FAIL-CLOSED (ADR-002 / R2): any bilingual field missing a language, or any
article without `reviewed: true` in BOTH languages, aborts the whole build.
Nothing is written until every check passes.  R4: files > 10 MB are reported.

Usage:  python build.py            build
        python build.py --check    validate only, write nothing
"""
import hashlib
import html
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
TEMPLATES = ROOT / "templates"
DEMOS = ROOT / "demos"
MAX_MEDIA_BYTES = 10 * 1024 * 1024
LANGS = ["zh", "en"]
HTML_LANG = {"zh": "zh-Hant", "en": "en"}
TYPES = ["music", "game", "tool", "demo"]
OUTPUT_DIRS = LANGS  # directories build.py owns and may wipe


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- helpers
def esc(s):
    return html.escape(str(s), quote=True)


def read_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BuildError(f"{p.relative_to(ROOT)}: invalid JSON — {e}")


def tpl(name):
    p = TEMPLATES / f"{name}.html"
    if not p.exists():
        raise BuildError(f"missing template {p.relative_to(ROOT)}")
    return p.read_text(encoding="utf-8")


_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(template, ctx):
    """Replace {{key}} with ctx[key]; unknown keys are a hard error (no silent holes)."""
    missing = set()

    def sub(m):
        k = m.group(1)
        if k not in ctx:
            missing.add(k)
            return ""
        return str(ctx[k])

    out = _PLACEHOLDER.sub(sub, template)
    if missing:
        raise BuildError(f"template placeholder(s) without value: {sorted(missing)}")
    return out


def bilingual(obj, path):
    """Return obj if it is a {zh:..., en:...} pair with non-empty values; else raise."""
    if not isinstance(obj, dict):
        raise BuildError(f"{path}: expected bilingual object {{zh, en}}, got {type(obj).__name__}")
    for lang in LANGS:
        v = obj.get(lang)
        ok = (isinstance(v, str) and v.strip()) or (
            isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v))
        if not ok:
            raise BuildError(f"{path}: missing or empty '{lang}' text (all content must be bilingual, ADR-002)")
    extra = set(obj) - set(LANGS)
    if extra:
        raise BuildError(f"{path}: unexpected language key(s) {sorted(extra)}")
    return obj


# ---------------------------------------------------------------- loading + validation
def load_site():
    site = read_json(CONTENT / "site.json")
    for key in ("author", "tagline", "hero_intro", "about_body"):
        bilingual(site.get(key), f"site.json:{key}")
    for k, v in site["nav"].items():
        bilingual(v, f"site.json:nav.{k}")
    for k, v in site["ui"].items():
        bilingual(v, f"site.json:ui.{k}")
    for k in ("site_name", "base_url", "default_language"):
        if not site.get(k):
            raise BuildError(f"site.json: '{k}' is required")
    c = site.get("contact", {})
    if not c.get("email_user") or not c.get("email_domain"):
        raise BuildError("site.json: contact.email_user / email_domain required")
    return site


def load_works():
    works = read_json(CONTENT / "works.json")
    if not isinstance(works, list):
        raise BuildError("works.json must be a list")
    ids = set()
    for i, w in enumerate(works):
        p = f"works.json[{i}]"
        wid = w.get("id")
        if not wid or not re.fullmatch(r"[a-z0-9-]+", wid):
            raise BuildError(f"{p}: 'id' must be kebab-case [a-z0-9-]")
        if wid in ids:
            raise BuildError(f"{p}: duplicate id '{wid}'")
        ids.add(wid)
        if w.get("type") not in TYPES:
            raise BuildError(f"{p} ({wid}): 'type' must be one of {TYPES}")
        if not isinstance(w.get("year"), int):
            raise BuildError(f"{p} ({wid}): 'year' must be an integer")
        bilingual(w.get("title"), f"{p} ({wid}).title")
        bilingual(w.get("desc"), f"{p} ({wid}).desc")
        media = w.get("media") or {}
        if not isinstance(media, dict):
            raise BuildError(f"{p} ({wid}): 'media' must be an object")
        allowed = {"youtube", "soundcloud", "local", "demo"}
        bad = set(media) - allowed
        if bad:
            raise BuildError(f"{p} ({wid}): unknown media key(s) {sorted(bad)}; allowed {sorted(allowed)}")
        if "local" in media:
            lp = ROOT / media["local"]
            if not lp.exists():
                raise BuildError(f"{p} ({wid}): local media not found: {media['local']}")
            if lp.suffix.lower() not in (".mp3", ".ogg"):
                raise BuildError(f"{p} ({wid}): local media must be mp3/ogg (ADR-005)")
            if lp.stat().st_size > MAX_MEDIA_BYTES:
                raise BuildError(f"{p} ({wid}): local media exceeds 10 MB (ADR-005)")
        if "demo" in media:
            dp = ROOT / media["demo"]
            if not (dp / "index.html").exists():
                raise BuildError(f"{p} ({wid}): demo folder missing index.html: {media['demo']}")
            if not (dp / "demo.json").exists():
                raise BuildError(f"{p} ({wid}): demo folder missing demo.json (ADR-004 contract): {media['demo']}")
        if w.get("platform") not in ("desktop", "all"):
            raise BuildError(f"{p} ({wid}): 'platform' must be 'desktop' or 'all' (R3)")
        for j, l in enumerate(w.get("links", [])):
            bilingual(l.get("label"), f"{p} ({wid}).links[{j}].label")
            if not l.get("url"):
                raise BuildError(f"{p} ({wid}).links[{j}]: url required")
    return works


def load_demos(works):
    """Validate every demos/*/demo.json against the ADR-004 contract."""
    demos = {}
    if not DEMOS.exists():
        return demos
    referenced = {w["media"]["demo"].rstrip("/") for w in works if "demo" in (w.get("media") or {})}
    for d in sorted(DEMOS.iterdir()):
        if not d.is_dir():
            continue
        rel = f"demos/{d.name}"
        meta_p = d / "demo.json"
        if not meta_p.exists():
            raise BuildError(f"{rel}: demo.json missing (ADR-004 contract)")
        meta = read_json(meta_p)
        bilingual(meta.get("title"), f"{rel}/demo.json:title")
        bilingual(meta.get("desc"), f"{rel}/demo.json:desc")
        if meta.get("platform") not in ("desktop", "all"):
            raise BuildError(f"{rel}/demo.json: 'platform' must be 'desktop' or 'all' (R3)")
        if meta.get("concept_level_checked") is not True:
            raise BuildError(f"{rel}/demo.json: 'concept_level_checked' must be true (ADR-004 / V4 self-check)")
        if not (d / "index.html").exists():
            raise BuildError(f"{rel}: index.html missing")
        if rel not in referenced:
            print(f"  note: {rel} is not referenced by any works.json entry (listed on demos page only)")
        demos[rel] = meta
    return demos


_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def parse_frontmatter(text, path):
    m = _FM.match(text)
    if not m:
        raise BuildError(f"{path}: frontmatter block (--- ... ---) required")
    meta = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            raise BuildError(f"{path}: bad frontmatter line: {line!r}")
        k, v = line.split(":", 1)
        v = v.strip()
        if v.lower() in ("true", "false"):
            v = v.lower() == "true"
        elif len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        meta[k.strip()] = v
    return meta, m.group(2)


def md_to_html(md):
    """Deliberately tiny Markdown: headings, paragraphs, lists, bold/italic/code/links."""
    out, para, in_list = [], [], False

    def inline(s):
        s = esc(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
        return s

    def flush():
        nonlocal para
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para = []

    for line in md.splitlines():
        if in_list and not line.lstrip().startswith("- "):
            out.append("</ul>")
            in_list = False
        h = re.match(r"^(#{1,6})\s+(.*)", line)
        if h:
            flush()
            n = len(h.group(1)) + 1
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
        elif line.lstrip().startswith("- "):
            flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line.lstrip()[2:])}</li>")
        elif not line.strip():
            flush()
        else:
            para.append(line.strip())
    flush()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def load_articles():
    """Articles: content/articles/<slug>.zh.md + <slug>.en.md, both reviewed: true."""
    adir = CONTENT / "articles"
    if not adir.exists():
        return []
    by_slug = {}
    for p in sorted(adir.glob("*.md")):
        m = re.fullmatch(r"([a-z0-9-]+)\.(zh|en)\.md", p.name)
        if not m:
            raise BuildError(f"articles/{p.name}: name must be <slug>.zh.md / <slug>.en.md")
        slug, lang = m.groups()
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"), f"articles/{p.name}")
        if meta.get("reviewed") is not True:
            raise BuildError(f"articles/{p.name}: 'reviewed: true' required before publishing (ADR-002 審稿制)")
        for k in ("title", "date"):
            if not meta.get(k):
                raise BuildError(f"articles/{p.name}: frontmatter '{k}' required")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(meta["date"])):
            raise BuildError(f"articles/{p.name}: date must be YYYY-MM-DD")
        by_slug.setdefault(slug, {})[lang] = {"meta": meta, "html": md_to_html(body)}
    articles = []
    for slug, langs in by_slug.items():
        for lang in LANGS:
            if lang not in langs:
                raise BuildError(f"articles/{slug}: missing {slug}.{lang}.md — every article must exist in both languages (ADR-002)")
        articles.append({"slug": slug, **langs})
    articles.sort(key=lambda a: a["zh"]["meta"]["date"], reverse=True)
    return articles


def scan_big_files():
    big = []
    for p in ROOT.rglob("*"):
        if ".git" in p.parts or not p.is_file():
            continue
        if p.stat().st_size > MAX_MEDIA_BYTES:
            big.append((p.relative_to(ROOT), p.stat().st_size))
    return big


# ---------------------------------------------------------------- rendering
def asset_versions():
    """Short content hashes for cache-busting (?v=) — GitHub Pages serves max-age=600."""
    def h(rel):
        return hashlib.sha1((ROOT / rel).read_bytes()).hexdigest()[:8]
    return {"v_style": h("assets/css/style.css"), "v_main": h("assets/js/main.js"),
            "v_os_css": h("assets/css/os.css"), "v_os_js": h("assets/js/os.js")}


def t(site, key, lang):
    return site["ui"][key][lang]


def contact_block(site, lang):
    c = site["contact"]
    items = [
        f'<li>{esc(t(site, "email_label", lang))}: '
        f'<a data-email href="#" data-u="{esc(c["email_user"])}" data-d="{esc(c["email_domain"])}">'
        f'{esc(t(site, "email_hint", lang))}</a></li>'
    ]
    for l in c.get("links", []):
        items.append(f'<li><a href="{esc(l["url"])}" rel="me noopener">{esc(l["label"])}</a></li>')
    return '<ul class="contact-list">' + "".join(items) + "</ul>"


def media_block(w, root):
    m = w.get("media") or {}
    if "youtube" in m:
        return (f'<iframe class="media" src="https://www.youtube-nocookie.com/embed/{esc(m["youtube"])}" '
                f'title="{esc(w["title"]["en"])}" loading="lazy" allow="encrypted-media; picture-in-picture" allowfullscreen></iframe>')
    if "soundcloud" in m:
        return (f'<iframe class="media" src="https://w.soundcloud.com/player/?url={esc(m["soundcloud"])}&amp;color=%23e0b04a" '
                f'title="{esc(w["title"]["en"])}" loading="lazy"></iframe>')
    if "local" in m:
        return f'<audio class="media" controls preload="none" src="{root}{esc(m["local"])}"></audio>'
    return ""


def platform_label(site, platform, lang):
    return t(site, "platform_desktop" if platform == "desktop" else "platform_all", lang)


def card(site, w, lang, root):
    m = w.get("media") or {}
    actions = []
    if "demo" in m:
        actions.append(f'<a class="btn" href="{root}{esc(m["demo"])}">{esc(t(site, "open_demo", lang))}</a>')
    for l in w.get("links", []):
        actions.append(f'<a href="{esc(l["url"])}" rel="noopener">{esc(l["label"][lang])}</a>')
    return render(tpl("card"), {
        "type": w["type"],
        "type_label": esc(t(site, f"type_{w['type']}", lang)),
        "year": w["year"],
        "title": esc(w["title"][lang]),
        "desc": esc(w["desc"][lang]),
        "media_block": media_block(w, root),
        "platform_note": f'<span class="platform">{esc(platform_label(site, w["platform"], lang))}</span>' if "demo" in m else "",
        "action_links": " ".join(actions),
    })


def demo_card(site, rel, meta, lang, root):
    return render(tpl("card"), {
        "type": "demo", "type_label": esc(t(site, "type_demo", lang)),
        "year": meta.get("year", ""), "title": esc(meta["title"][lang]), "desc": esc(meta["desc"][lang]),
        "media_block": "",
        "platform_note": f'<span class="platform">{esc(platform_label(site, meta["platform"], lang))}</span>',
        "action_links": f'<a class="btn" href="{root}{rel}/">{esc(t(site, "open_demo", lang))}</a>',
    })


def page(site, lang, page_path, page_key, title, desc, content, depth):
    root = "../" * depth
    ctx = {
        "html_lang": HTML_LANG[lang], "lang": lang, "other_lang": "en" if lang == "zh" else "zh",
        "site_name": esc(site["site_name"]), "base_url": site["base_url"],
        "page_title": esc(title), "meta_desc": esc(desc), "page_path": page_path, "root": root,
        "lang_switch": esc(t(site, "lang_switch", lang)),
        "footer": esc(t(site, "footer", lang).replace("{year}", str(date.today().year))),
        "content": content,
    }
    ctx.update(asset_versions())
    for k, v in site["nav"].items():
        ctx[f"nav_{k}"] = esc(v[lang])
        ctx[f"nav_active_{k}"] = 'aria-current="page"' if k == page_key else ""
    return render(tpl("base"), ctx)


def build_pages(site, works, demos, articles):
    """Return {relative_output_path: html} — pure, writes nothing."""
    out = {}
    for lang in LANGS:
        def L(key):
            return esc(t(site, key, lang))
        base_ctx = {"lang": lang, "root": "../", "author": esc(site["author"][lang])}

        # home = desktop OS shell (client renders apps from embedded JSON; sub-pages remain as deep links)
        def loc(w):
            return {"id": w["id"], "type": w["type"], "year": w["year"], "featured": bool(w.get("featured")),
                    "title": w["title"][lang], "desc": w["desc"][lang], "media": w.get("media") or {},
                    "platform": w["platform"], "links": [{"label": l["label"][lang], "url": l["url"]} for l in w.get("links", [])]}
        data = {
            "lang": lang, "author": site["author"][lang], "tagline": site["tagline"][lang], "hero_intro": site["hero_intro"][lang],
            "about": site["about_body"][lang], "contact": site["contact"],
            "ui": {k: v[lang] for k, v in site["ui"].items()},
            "works": [loc(w) for w in works],
            "demos": [{"path": rel, "title": m["title"][lang], "desc": m["desc"][lang], "platform": m["platform"], "year": m.get("year", "")} for rel, m in demos.items()],
            "articles": [{"slug": a["slug"], "title": a[lang]["meta"]["title"], "date": a[lang]["meta"]["date"]} for a in articles],
        }
        site_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        home_ctx = {"html_lang": HTML_LANG[lang], "lang": lang, "other_lang": "en" if lang == "zh" else "zh",
                    "site_name": esc(site["site_name"]), "base_url": site["base_url"], "tagline": esc(site["tagline"][lang]),
                    "meta_desc": esc(site["hero_intro"][lang]), "author": esc(site["author"][lang]), "hero_intro": esc(site["hero_intro"][lang]),
                    "lang_switch": L("lang_switch"), "sticky": L("sticky"), "site_data": site_json}
        home_ctx.update(asset_versions())
        for k in ("boot_enter", "boot_hint", "os_name", "app_works", "app_demos", "app_articles", "app_about", "app_player", "app_terminal", "desk_hint", "ph_unlock", "ph_lock_line", "player_now"):
            home_ctx["ui_" + k] = L(k)
        out[f"{lang}/index.html"] = render(tpl("desktop"), home_ctx)

        # works
        present_types = [ty for ty in TYPES if any(w["type"] == ty for w in works)]
        wk = render(tpl("works"), {**base_ctx, "root": "../../", "ui_all_works": L("all_works"), "ui_filter_all": L("filter_all"),
            "filter_buttons": "\n".join(f'    <button data-filter="{ty}">{L("type_" + ty)}</button>' for ty in present_types),
            "work_cards": "\n".join(card(site, w, lang, "../../") for w in works)})
        out[f"{lang}/works/index.html"] = page(site, lang, "works/", "works", site["nav"]["works"][lang], site["tagline"][lang], wk, 2)

        # demos
        dm = render(tpl("demos"), {"ui_demos_title": L("demos_title"), "ui_demos_intro": L("demos_intro"),
            "demo_cards": "\n".join(demo_card(site, rel, meta, lang, "../../") for rel, meta in demos.items())})
        out[f"{lang}/demos/index.html"] = page(site, lang, "demos/", "demos", site["nav"]["demos"][lang], site["ui"]["demos_intro"][lang], dm, 2)

        # articles list + pages
        if articles:
            items = "".join(f'<li><a href="{esc(a["slug"])}/">{esc(a[lang]["meta"]["title"])}</a> '
                            f'<span class="article-meta">{esc(a[lang]["meta"]["date"])}</span></li>' for a in articles)
            lst = f'<ul class="article-list">{items}</ul>'
        else:
            lst = f'<p class="empty">{L("articles_empty")}</p>'
        ar = render(tpl("articles"), {"ui_articles_title": L("articles_title"), "article_list": lst})
        out[f"{lang}/articles/index.html"] = page(site, lang, "articles/", "articles", site["nav"]["articles"][lang], site["tagline"][lang], ar, 2)
        for a in articles:
            body = render(tpl("article"), {"article_title": esc(a[lang]["meta"]["title"]),
                                           "article_date": esc(a[lang]["meta"]["date"]), "article_body": a[lang]["html"]})
            out[f"{lang}/articles/{a['slug']}/index.html"] = page(
                site, lang, f"articles/{a['slug']}/", "articles", a[lang]["meta"]["title"],
                a[lang]["meta"].get("summary", a[lang]["meta"]["title"]), body, 3)

        # about
        ab = render(tpl("about"), {"ui_about_title": L("about_title"), "ui_contact_title": L("contact_title"),
            "about_paragraphs": "\n".join(f"<p>{esc(p)}</p>" for p in site["about_body"][lang]),
            "contact_block": contact_block(site, lang)})
        out[f"{lang}/about/index.html"] = page(site, lang, "about/", "about", site["nav"]["about"][lang], site["hero_intro"][lang], ab, 2)

    # root redirect + 404 + sitemap
    redirect_ctx = {"site_name": esc(site["site_name"]), "base_url": site["base_url"],
                    "default_language": site["default_language"], "prefix": ""}
    out["index.html"] = render(tpl("redirect"), redirect_ctx)
    out["404.html"] = render(tpl("redirect"), {**redirect_ctx, "prefix": "/"})
    urls = sorted({p[:-len("index.html")] for p in out if p.endswith("index.html") and p != "index.html"})
    out["sitemap.xml"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                          + "".join(f"  <url><loc>{site['base_url']}/{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    return out


# ---------------------------------------------------------------- main
def main(argv):
    check_only = "--check" in argv
    print("build.py — validating content (fail-closed)")
    try:
        site = load_site()
        works = load_works()
        demos = load_demos(works)
        articles = load_articles()
        big = scan_big_files()
        pages = build_pages(site, works, demos, articles)
    except BuildError as e:
        print(f"\nBUILD REFUSED: {e}\nNothing was written.", file=sys.stderr)
        return 1
    print(f"  works: {len(works)}   demos: {len(demos)}   articles: {len(articles)}   pages: {len(pages)}")
    for rel, size in big:
        print(f"  WARNING (R4): {rel} is {size / 1048576:.1f} MB > 10 MB — must not be pushed")
    if check_only:
        print("check passed (nothing written)")
        return 0
    for d in OUTPUT_DIRS:
        shutil.rmtree(ROOT / d, ignore_errors=True)
    for rel, content in pages.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {len(pages)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
