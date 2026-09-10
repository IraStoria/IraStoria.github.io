// © 2026 IraStoria. Submissions worker for irastoria.github.io — see API.md
//
// One Cloudflare Worker + one KV namespace (binding `POOL`) serving both the
// bug-report wall and the wish pool. Zero dependencies, ES-module syntax.
// Privacy: raw IPs are never stored or logged; only the first 16 hex of a
// SHA-256 digest (`iph`) is kept, and rate-limit keys use that digest too.

// ---------------------------------------------------------------------------
// Constants (mirror API.md)
// ---------------------------------------------------------------------------
const CATS = ['transcription', 'design', 'code', 'feature', 'interactive', 'other'];
const STATUSES = ['wishing', 'considering', 'building', 'done', 'declined'];
const BUG_STATUSES = ['new', 'open', 'watch', 'fixed', 'declined'];   // LOG-168: a report's verdict - 待審 / 在逃 / 保釋觀察中 / 已伏法 / 不受理
const LANGS = ['zh', 'en'];
const LIMIT = {
  bugBody: 32 * 1024,   // bytes, bug (with trail)
  wishBody: 8 * 1024,   // bytes, wish
  nick: 24,
  wishText: 600,
  bugText: 2000,
  trailItems: 200,
  trailItem: 200,
  reply: 2000,
  link: 200,
  email: 120,       // LOG-165: the wisher's optional address
};
// Rate limits: [max hits, window seconds]
const RATE = {
  submit: [5, 600],
  vote: [30, 600],
  wishes: [60, 60],
  bugs: [60, 60],
  mine: [30, 60],
  totp: [5, 600],
  unsub: [10, 600],
};
const MINE_MAX = 10;             // ids per POST /mine (the site keeps at most 10 of the sender's own)
const TOKEN_TTL_S = 12 * 3600;   // admin pass validity
const PRE_TTL_S = 300;           // pre-token (between GitHub and TOTP) validity
const STATE_TTL_S = 600;         // OAuth state validity
const VOTE_TTL_S = 86400;        // one vote per IP per wish per day
// TOTP (RFC 6238): HMAC-SHA1, 30 s step, 6 digits, accept ±1 step.
const TOTP_STEP_S = 30;
const TOTP_DIGITS = 6;
const TOTP_WINDOW = 1;
// Bark push notifications
const BARK_DEFAULT_SERVER = 'https://api.day.app';
const BARK_GROUP = 'irastoria-pool';
// Wish-update mail (LOG-165): an HTTP mail API shaped like Resend's - POST {from,to,subject,text} with a Bearer key.
// Silent unless MAIL_API_KEY and MAIL_FROM are set. The address is optional, never public, dropped by the unsubscribe link.
const MAIL_DEFAULT_API = 'https://api.resend.com/emails';
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ST_LABEL = {
  zh: { wishing: '許願中', considering: '考慮中', building: '施工中', done: '已實現', declined: '婉謝' },
  en: { wishing: 'Wished', considering: 'Considering', building: 'In progress', done: 'Granted', declined: 'Declined' },
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const enc = new TextEncoder();

function b64url(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function b64urlDecode(str) {
  const pad = str.length % 4 === 0 ? '' : '='.repeat(4 - (str.length % 4));
  const s = (str + pad).replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function b64urlText(text) { return b64url(enc.encode(text)); }
function b64urlToText(str) { return new TextDecoder().decode(b64urlDecode(str)); }

async function sha256hex(text) {
  const d = await crypto.subtle.digest('SHA-256', enc.encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
async function hmacKey(secret) {
  return crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}
async function hmacB64url(secret, text) {
  const sig = await crypto.subtle.sign('HMAC', await hmacKey(secret), enc.encode(text));
  return b64url(new Uint8Array(sig));
}
async function hmacVerify(secret, text, sigB64url) {
  let sig;
  try { sig = b64urlDecode(sigB64url); } catch { return false; }
  return crypto.subtle.verify('HMAC', await hmacKey(secret), sig, enc.encode(text));
}
function randHex(n) {
  const a = new Uint8Array(n);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// ---------------------------------------------------------------------------
// TOTP (RFC 6238 over HOTP RFC 4226), by hand on Web Crypto
// ---------------------------------------------------------------------------
const B32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
// RFC 4648 base32 decode: case-insensitive, '=' padding and spaces ignored.
// Returns null on any other character.
function base32Decode(str) {
  const s = String(str || '').toUpperCase().replace(/[=\s]/g, '');
  const out = [];
  let bits = 0, acc = 0;
  for (const ch of s) {
    const v = B32_ALPHABET.indexOf(ch);
    if (v < 0) return null;
    acc = (acc << 5) | v;
    bits += 5;
    if (bits >= 8) { bits -= 8; out.push((acc >>> bits) & 0xff); }
  }
  return out.length ? new Uint8Array(out) : null;
}
async function hotp(keyBytes, counter, digits) {
  const key = await crypto.subtle.importKey('raw', keyBytes, { name: 'HMAC', hash: 'SHA-1' }, false, ['sign']);
  const msg = new Uint8Array(8);
  // 64-bit big-endian counter (counter is a safe integer here).
  let c = counter;
  for (let i = 7; i >= 0; i--) { msg[i] = c % 256; c = Math.floor(c / 256); }
  const h = new Uint8Array(await crypto.subtle.sign('HMAC', key, msg));
  const off = h[h.length - 1] & 0x0f;
  const bin = ((h[off] & 0x7f) << 24) | (h[off + 1] << 16) | (h[off + 2] << 8) | h[off + 3];
  return String(bin % 10 ** digits).padStart(digits, '0');
}
// True when `code` matches the current step or its ±TOTP_WINDOW neighbours.
async function totpVerify(keyBytes, code) {
  const want = String(code);
  const step = Math.floor(Date.now() / 1000 / TOTP_STEP_S);
  for (let d = -TOTP_WINDOW; d <= TOTP_WINDOW; d++) {
    const c = step + d;
    if (c < 0) continue;
    if ((await hotp(keyBytes, c, TOTP_DIGITS)) === want) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Bark push (https://github.com/Finb/Bark). No-op without BARK_KEY; never
// throws; runs in ctx.waitUntil so the response is not delayed.
// ---------------------------------------------------------------------------
function bark(env, ctx, title, body, level) {
  try {
    if (!env || !env.BARK_KEY) return;
    const server = String(env.BARK_SERVER || BARK_DEFAULT_SERVER).replace(/\/+$/, '');
    const payload = { device_key: env.BARK_KEY, title, body, group: BARK_GROUP, isArchive: '1' };
    if (level) payload.level = level;
    const p = fetch(server + '/push', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(payload),
    })
      .then((r) => { try { if (r && r.body && r.body.cancel) r.body.cancel(); } catch {} })
      .catch(() => {});
    if (ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(p);
  } catch {
    // never let a notification break a response
  }
}
// One mail through the HTTP mail API, in the background; never lets a failure touch the response.
function mail(env, ctx, to, subject, text) {
  try {
    if (!env || !env.MAIL_API_KEY || !env.MAIL_FROM || !to) return;
    const p = fetch(String(env.MAIL_API || MAIL_DEFAULT_API), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8', Authorization: `Bearer ${env.MAIL_API_KEY}` },
      body: JSON.stringify({ from: env.MAIL_FROM, to: [to], subject, text }),
    })
      .then((r) => { try { if (r && r.body && r.body.cancel) r.body.cancel(); } catch {} })
      .catch(() => {});
    if (ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(p);
  } catch {
    // never let a notification break a response
  }
}
// What changed for the wisher: approval, a status, a (new) reply. A link edit, an unapproval or a re-save is not news.
function wishChange(before, it) {
  const c = [];
  if (before.approved !== true && it.approved === true && it.pub !== false) c.push('approved');   // LOG-180: no 'on the wall' line for a private wish
  if (before.status !== it.status) c.push('status');
  if ((it.reply || '') && before.reply !== it.reply) c.push('reply');
  return c;
}
// The mail, in the wish's language, with a one-click unsubscribe link signed with TOKEN_SECRET (no KV, no session).
async function wishMail(env, self, it, change) {
  const zh = it.lang !== 'en', L = ST_LABEL[zh ? 'zh' : 'en'];
  const t = await hmacB64url(env.TOKEN_SECRET, `unsub:${it.id}`);
  const unsub = `${self}/unsub?id=${encodeURIComponent(it.id)}&t=${t}`;
  const site = String(env.SITE_URL || '').replace(/\/(zh|en)\/?$/, '/') + (zh ? 'zh/' : 'en/');
  const chars = Array.from(it.text), quote = chars.length > 80 ? chars.slice(0, 80).join('') + '…' : it.text;
  const granted = change.includes('status') && it.status === 'done';
  const lines = [];
  if (zh) {
    lines.push(`${it.nick} 你好，`, '', `你在 IraStoria 許願池許的願望「${quote}」有新進展：`);
    if (change.includes('approved')) lines.push('・已放上牆（站主放行了）');
    if (change.includes('status')) lines.push(`・狀態：${L[it.status] || it.status}`);
    if (change.includes('reply')) lines.push(`・站主回覆：「${it.reply}」`);
    lines.push('', `去看看：${site}`, '', `這封信只因為你許願時留了 email 才寄出；email 不會公開。不想再收到：${unsub}`);
    return { subject: `許願池：你的願望有新進展${granted ? '（已實現）' : ''}`, text: lines.join('\n') };
  }
  lines.push(`Hi ${it.nick},`, '', `Your wish at the IraStoria wishing well - "${quote}" - has news:`);
  if (change.includes('approved')) lines.push('- it is on the wall now');
  if (change.includes('status')) lines.push(`- status: ${L[it.status] || it.status}`);
  if (change.includes('reply')) lines.push(`- reply: "${it.reply}"`);
  lines.push('', `See it: ${site}`, '', `You get this only because you left an email with the wish; it is never shown. To stop: ${unsub}`);
  return { subject: `Wishing well: news on your wish${granted ? ' (granted)' : ''}`, text: lines.join('\n') };
}
// "<country>/<city>" from Cloudflare's request.cf, '?' when unknown.
function geo(request) {
  const cf = (request && request.cf) || {};
  const s = (v) => (isStr(v) && v ? v : '?');
  return `${s(cf.country)}/${s(cf.city)}`;
}
// Length in code points (CJK/emoji count as one), used for all text limits.
const clen = (s) => Array.from(s).length;
const isStr = (v) => typeof v === 'string';
const isPlainObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

function json(status, body, extra) {
  const h = new Headers(extra || {});
  h.set('Content-Type', 'application/json; charset=utf-8');
  return new Response(JSON.stringify(body), { status, headers: h });
}
const fail = (status, code, extra) => json(status, { ok: false, error: code }, extra);

// ---------------------------------------------------------------------------
// CORS
// ---------------------------------------------------------------------------
function allowedOrigins(env) {
  return String(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim().replace(/\/+$/, '')).filter(Boolean);
}
function corsHeaders(request, env) {
  const origin = request.headers.get('Origin');
  const h = { Vary: 'Origin' };
  if (origin && allowedOrigins(env).includes(origin.replace(/\/+$/, ''))) {
    h['Access-Control-Allow-Origin'] = origin;
    h['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS';
    h['Access-Control-Allow-Headers'] = 'Content-Type, Authorization';
    h['Access-Control-Max-Age'] = '86400';
  }
  return h;
}
function originAllowed(request, env) {
  const origin = request.headers.get('Origin');
  return !!origin && allowedOrigins(env).includes(origin.replace(/\/+$/, ''));
}

// ---------------------------------------------------------------------------
// Client identity (hashed) and rate limiting
// ---------------------------------------------------------------------------
async function clientHash(request) {
  const ip = request.headers.get('CF-Connecting-IP') || request.headers.get('X-Forwarded-For') || '0.0.0.0';
  return (await sha256hex(ip.split(',')[0].trim())).slice(0, 16);
}
// Fixed-window counter in KV: rl:<route>:<iph> -> { n, reset }.
// Returns true when the caller is over the limit.
async function rateLimited(env, route, iph) {
  const [max, windowS] = RATE[route];
  const key = `rl:${route}:${iph}`;
  const now = Math.floor(Date.now() / 1000);
  let rec = null;
  try { rec = JSON.parse((await env.POOL.get(key)) || 'null'); } catch { rec = null; }
  if (!rec || typeof rec.n !== 'number' || typeof rec.reset !== 'number' || rec.reset <= now) {
    rec = { n: 0, reset: now + windowS };
  }
  if (rec.n >= max) return true;
  rec.n += 1;
  // KV requires expirationTtl >= 60s.
  await env.POOL.put(key, JSON.stringify(rec), { expirationTtl: Math.max(60, rec.reset - now) });
  return false;
}

// ---------------------------------------------------------------------------
// Body reading (size checked via Content-Length AND the actual bytes read)
// ---------------------------------------------------------------------------
async function readJson(request, maxBytes) {
  const cl = parseInt(request.headers.get('Content-Length') || '', 10);
  if (Number.isFinite(cl) && cl > maxBytes) return { err: fail(413, 'size') };
  let text;
  try { text = await request.text(); } catch { return { err: fail(400, 'invalid') }; }
  const bytes = enc.encode(text).length;
  if (bytes > maxBytes) return { err: fail(413, 'size') };
  let data;
  try { data = JSON.parse(text); } catch { return { err: fail(400, 'invalid') }; }
  if (!isPlainObj(data)) return { err: fail(400, 'invalid') };
  return { data, bytes };
}

// ---------------------------------------------------------------------------
// KV access
// ---------------------------------------------------------------------------
async function listAll(env, prefix) {
  const names = [];
  let cursor;
  do {
    const r = await env.POOL.list({ prefix, cursor });
    for (const k of r.keys) names.push(k.name);
    cursor = r.list_complete ? undefined : r.cursor;
  } while (cursor);
  return names;
}
async function getJson(env, key) {
  const raw = await env.POOL.get(key);
  if (raw == null) return null;
  try { return JSON.parse(raw); } catch { return null; }
}
async function loadAll(env, prefix) {
  const names = await listAll(env, prefix);
  const items = [];
  for (const name of names) {
    const it = await getJson(env, name);
    if (it) items.push(it);
  }
  items.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  return items;
}
// Locate an item by id: try wish first, then bug. Returns { key, item } or null.
async function findById(env, id, types) {
  for (const t of types) {
    const key = `${t}:${id}`;
    const item = await getJson(env, key);
    if (item) return { key, item };
  }
  return null;
}
function publicWish(w) {
  return {
    id: w.id, ts: w.ts, lang: w.lang, nick: w.nick, cat: w.cat, text: w.text,
    status: w.status, votes: w.votes, reply: w.reply, replyLang: w.replyLang, link: w.link,
  };
}
// The public face of a report the owner has switched on (LOG-169): nick, text, verdict, date - never the trail, meta or iph.
function publicBug(b) { return { id: b.id, ts: b.ts, lang: b.lang, nick: b.nick, text: b.text, status: b.status || 'new' }; }
async function rebuildPubBugs(env) {
  const all = await loadAll(env, 'bug:');
  const pub = { ts: Date.now(), items: all.filter((b) => b.approved === true).map(publicBug) };
  await env.POOL.put('pub:bugs', JSON.stringify(pub));
  return pub;
}
// Rebuild the cached public list (pub:wishes) from every approved wish.
async function rebuildPub(env) {
  const all = await loadAll(env, 'wish:');
  const pub = { ts: Date.now(), items: all.filter((w) => w.approved === true && w.pub !== false).map(publicWish) };   // LOG-180: private wishes never reach the wall
  await env.POOL.put('pub:wishes', JSON.stringify(pub));
  return pub;
}

// ---------------------------------------------------------------------------
// Admin pass: base64url(payload).base64url(HMAC-SHA256(payload))
// Pre-token (same scheme, payload.pre === 1, 5 min): issued after GitHub when
// TOTP_SECRET is set; only good for POST /auth/totp, never for /admin/*.
// ---------------------------------------------------------------------------
async function signPayload(env, obj) {
  const payload = b64urlText(JSON.stringify(obj));
  return `${payload}.${await hmacB64url(env.TOKEN_SECRET, payload)}`;
}
async function signToken(env, login) {
  return signPayload(env, { sub: login, exp: Math.floor(Date.now() / 1000) + TOKEN_TTL_S });
}
async function signPre(env, login) {
  return signPayload(env, { sub: login, exp: Math.floor(Date.now() / 1000) + PRE_TTL_S, pre: 1 });
}
// Shared checks: signature, shape, expiry, owner. Callers decide about `pre`.
async function readSigned(env, token) {
  if (!isStr(token) || !env.TOKEN_SECRET) return null;
  const parts = token.split('.');
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  if (!(await hmacVerify(env.TOKEN_SECRET, parts[0], parts[1]))) return null;
  let payload;
  try { payload = JSON.parse(b64urlToText(parts[0])); } catch { return null; }
  if (!isPlainObj(payload) || !isStr(payload.sub) || typeof payload.exp !== 'number') return null;
  if (payload.exp <= Math.floor(Date.now() / 1000)) return null;
  if (payload.sub.toLowerCase() !== String(env.OWNER_LOGIN || '').toLowerCase()) return null;
  return payload;
}
async function verifyToken(env, token) {
  const payload = await readSigned(env, token);
  if (!payload || payload.pre !== undefined) return null;   // a pre-token is never an admin pass
  return payload;
}
async function verifyPre(env, pre) {
  const payload = await readSigned(env, pre);
  if (!payload || payload.pre !== 1) return null;
  return payload;
}
async function requireAdmin(request, env) {
  const m = /^Bearer\s+(.+)$/i.exec(request.headers.get('Authorization') || '');
  return m ? verifyToken(env, m[1].trim()) : null;
}

// OAuth state: base64url("<ts>.<nonce>").base64url(HMAC) — stateless, 10 min.
async function makeState(env) {
  const body = b64urlText(`${Math.floor(Date.now() / 1000)}.${randHex(8)}`);
  return `${body}.${await hmacB64url(env.TOKEN_SECRET, body)}`;
}
async function checkState(env, state) {
  if (!isStr(state)) return false;
  const parts = state.split('.');
  if (parts.length !== 2) return false;
  if (!(await hmacVerify(env.TOKEN_SECRET, parts[0], parts[1]))) return false;
  let ts;
  try { ts = parseInt(b64urlToText(parts[0]).split('.')[0], 10); } catch { return false; }
  const age = Math.floor(Date.now() / 1000) - ts;
  return Number.isFinite(age) && age >= 0 && age <= STATE_TTL_S;
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

// POST /submit — validate and store a wish or a bug report.
async function handleSubmit(request, env, ctx, iph) {
  if (await rateLimited(env, 'submit', iph)) return fail(429, 'rate');
  const r = await readJson(request, LIMIT.bugBody);
  if (r.err) return r.err;
  const { data, bytes } = r;

  if (data.type !== 'wish' && data.type !== 'bug') return fail(400, 'invalid');
  if (!LANGS.includes(data.lang)) return fail(400, 'invalid');
  if (!isStr(data.text) || !data.text.trim()) return fail(400, 'invalid');
  const text = data.text.trim();
  const nick = isStr(data.nick) ? data.nick.trim() : '';
  if (clen(nick) > LIMIT.nick) return fail(400, 'invalid');

  const ts = Date.now();
  const id = `${ts}-${randHex(4)}`;

  if (data.type === 'wish') {
    if (bytes > LIMIT.wishBody) return fail(413, 'size');
    if (!nick) return fail(400, 'invalid');
    if (!CATS.includes(data.cat)) return fail(400, 'invalid');
    if (clen(text) > LIMIT.wishText) return fail(400, 'invalid');
    const email = isStr(data.email) ? data.email.trim().toLowerCase() : '';   // LOG-165: optional; never public
    if (data.public !== undefined && typeof data.public !== 'boolean') return fail(400, 'invalid');
    const pub = data.public !== false;   // LOG-180: a private wish is for the owner only - never on the wall, whatever `approved` says
    if (email && (clen(email) > LIMIT.email || !EMAIL_RE.test(email))) return fail(400, 'invalid');
    const item = {
      id, type: 'wish', ts, lang: data.lang, nick, cat: data.cat, text,
      approved: false, status: 'wishing', votes: 0, reply: '', replyLang: '', link: '', email, iph, pub,
    };
    await env.POOL.put(`wish:${id}`, JSON.stringify(item));
    notifySubmit(env, ctx, '許願池 · 新願望', nick, text);
    return json(200, { ok: true, id });
  }

  // bug
  if (clen(text) > LIMIT.bugText) return fail(400, 'invalid');
  let trail = [];
  if (data.trail !== undefined && data.trail !== null) {
    if (!Array.isArray(data.trail) || data.trail.length > LIMIT.trailItems) return fail(400, 'invalid');
    for (const t of data.trail) {
      if (isStr(t)) { if (clen(t) > LIMIT.trailItem) return fail(400, 'invalid'); trail.push(t); }
      else if (isPlainObj(t)) { const s = JSON.stringify(t); if (clen(s) > LIMIT.trailItem) return fail(400, 'invalid'); trail.push(t); }
      else return fail(400, 'invalid');
    }
  }
  const m = isPlainObj(data.meta) ? data.meta : {};
  const str = (v, n) => (isStr(v) ? Array.from(v).slice(0, n).join('') : '');
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.round(v) : 0);
  const meta = { shell: str(m.shell, 32), ua: str(m.ua, 400), vw: num(m.vw), vh: num(m.vh), ver: str(m.ver, 64), page: str(m.page, 300) };
  const item = { id, type: 'bug', ts, lang: data.lang, nick, text, trail, meta, read: false, status: 'new', approved: false, iph };   // approved (LOG-169): the owner's 顯示 switch - off until turned on
  await env.POOL.put(`bug:${id}`, JSON.stringify(item));
  notifySubmit(env, ctx, '恥辱柱 · 新回報', nick, text);
  return json(200, { ok: true, id });
}
// Bark on accepted submissions unless BARK_ON_SUBMIT is "0".
function notifySubmit(env, ctx, title, nick, text) {
  if (String(env.BARK_ON_SUBMIT ?? '') === '0') return;
  bark(env, ctx, title, `${nick || '匿名'}：${text.slice(0, 80)}`, 'active');
}

// GET /wishes — cached public list (approved only, no iph).
async function handleWishes(env, iph) {
  if (await rateLimited(env, 'wishes', iph)) return fail(429, 'rate');
  let pub = await getJson(env, 'pub:wishes');
  if (!pub || !Array.isArray(pub.items)) pub = await rebuildPub(env);
  return json(200, { ok: true, ts: pub.ts, items: pub.items }, { 'Cache-Control': 'public, max-age=60' });
}

// GET /bugs — the reports the owner has switched on (LOG-169), public fields only.
async function handleBugs(env, iph) {
  if (await rateLimited(env, 'bugs', iph)) return fail(429, 'rate');
  let pub = await getJson(env, 'pub:bugs');
  if (!pub || !Array.isArray(pub.items)) pub = await rebuildPubBugs(env);
  return json(200, { ok: true, ts: pub.ts, items: pub.items }, { 'Cache-Control': 'public, max-age=60' });
}

// POST /mine — the sender asks after their own wishes: for each id, `pending` (still waiting),
// `public` (approved; it is in GET /wishes now) or `gone` (declined and removed). Ids are random
// and known only to the browser that submitted them; nothing else about a wish leaves here.
async function handleMine(request, env, iph) {
  if (await rateLimited(env, 'mine', iph)) return fail(429, 'rate');
  const r = await readJson(request, LIMIT.wishBody);
  if (r.err) return r.err;
  const ids = r.data.ids;
  if (!Array.isArray(ids) || ids.length > MINE_MAX || !ids.every((x) => isStr(x) && x.length > 0 && x.length <= 64)) return fail(400, 'invalid');
  const states = {};
  for (const id of ids) {
    const w = await getJson(env, `wish:${id}`);
    states[id] = !w ? 'gone' : (w.approved === true && w.pub !== false) ? 'public' : 'pending';   // LOG-180: a private wish stays `pending` for its sender (there is no public card to take over)
  }
  return json(200, { ok: true, states });
}

// POST /vote — +1 on an approved wish, once per IP per day.
async function handleVote(request, env, iph) {
  if (await rateLimited(env, 'vote', iph)) return fail(429, 'rate');
  const r = await readJson(request, LIMIT.wishBody);
  if (r.err) return r.err;
  const id = r.data.id;
  if (!isStr(id) || !/^[0-9]+-[0-9a-f]+$/.test(id)) return fail(400, 'invalid');
  const found = await findById(env, id, ['wish']);
  if (!found || found.item.approved !== true) return fail(404, 'notfound');
  const vkey = `v:${id}:${iph}`;
  if (await env.POOL.get(vkey)) return json(200, { ok: true, votes: found.item.votes, dup: true });
  found.item.votes = (found.item.votes || 0) + 1;
  await env.POOL.put(found.key, JSON.stringify(found.item));
  await env.POOL.put(vkey, '1', { expirationTtl: VOTE_TTL_S });
  await rebuildPub(env);
  return json(200, { ok: true, votes: found.item.votes });
}

// GET /auth/start — redirect to GitHub with a signed state.
async function handleAuthStart(env) {
  if (!env.GITHUB_CLIENT_ID || !env.TOKEN_SECRET) return fail(500, 'config');
  const u = new URL('https://github.com/login/oauth/authorize');
  u.searchParams.set('client_id', env.GITHUB_CLIENT_ID);
  u.searchParams.set('state', await makeState(env));
  u.searchParams.set('scope', 'read:user');
  return Response.redirect(u.toString(), 302);
}

// Sign-in notifications (Bark, level timeSensitive).
function notifyLoginOk(request, env, ctx, login) {
  bark(env, ctx, '許願池 · 登入成功', `${login} · ${geo(request)} · ${new Date().toISOString()}`, 'timeSensitive');
}
function notifyLoginDenied(request, env, ctx, reason, login) {
  bark(env, ctx, '許願池 · 登入被拒', `${reason}${login ? ' · ' + login : ''} · ${geo(request)} · ${new Date().toISOString()}`, 'timeSensitive');
}

// GET /auth/callback — verify state, exchange code, check login, then either
// issue the pass (#wp=) or, when TOTP_SECRET is set, a 5-minute pre-token
// (#wp2=) that must be traded for the pass at POST /auth/totp.
async function handleAuthCallback(request, url, env, ctx) {
  if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET || !env.TOKEN_SECRET || !env.SITE_URL) return fail(500, 'config');
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  if (!(await checkState(env, state))) return fail(400, 'state');
  if (!code) return fail(400, 'invalid');
  const denied = (reason, login) => {
    notifyLoginDenied(request, env, ctx, reason, login);
    return Response.redirect(env.SITE_URL + '#wp=denied', 302);
  };
  try {
    const tr = await fetch('https://github.com/login/oauth/access_token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'User-Agent': 'irastoria-pool-worker' },
      body: JSON.stringify({ client_id: env.GITHUB_CLIENT_ID, client_secret: env.GITHUB_CLIENT_SECRET, code }),
    });
    const tj = await tr.json();
    if (!tj || !tj.access_token) return denied('github');
    const ur = await fetch('https://api.github.com/user', {
      headers: { Authorization: `Bearer ${tj.access_token}`, Accept: 'application/vnd.github+json', 'User-Agent': 'irastoria-pool-worker' },
    });
    const uj = await ur.json();
    const login = uj && isStr(uj.login) ? uj.login : '';
    if (!login || login.toLowerCase() !== String(env.OWNER_LOGIN || '').toLowerCase()) return denied('owner', login);
    if (env.TOTP_SECRET) {
      const pre = await signPre(env, login);
      return Response.redirect(env.SITE_URL + '#wp2=' + pre, 302);
    }
    const token = await signToken(env, login);
    notifyLoginOk(request, env, ctx, login);
    return Response.redirect(env.SITE_URL + '#wp=' + token, 302);
  } catch {
    return denied('github');
  }
}

// POST /auth/totp — body { pre, code }: trade a valid pre-token plus the
// current authenticator code for the 12 h pass.
async function handleAuthTotp(request, env, ctx, iph) {
  if (await rateLimited(env, 'totp', iph)) return fail(429, 'rate');
  if (!env.TOKEN_SECRET || !env.TOTP_SECRET) return fail(500, 'config');
  const r = await readJson(request, LIMIT.wishBody);
  if (r.err) return r.err;
  const payload = await verifyPre(env, r.data.pre);
  if (!payload) {
    notifyLoginDenied(request, env, ctx, 'pre');
    return fail(401, 'pre');
  }
  const key = base32Decode(env.TOTP_SECRET);
  if (!key) return fail(500, 'config');
  const code = isStr(r.data.code) || typeof r.data.code === 'number' ? String(r.data.code).trim() : '';
  const okCode = new RegExp(`^[0-9]{${TOTP_DIGITS}}$`).test(code) && (await totpVerify(key, code));
  if (!okCode) {
    notifyLoginDenied(request, env, ctx, 'code', payload.sub);
    return fail(401, 'code');
  }
  const token = await signToken(env, payload.sub);
  notifyLoginOk(request, env, ctx, payload.sub);
  return json(200, { ok: true, token });
}

// GET /admin/list?type=wish|bug — every item, including unapproved.
async function handleAdminList(url, env) {
  const type = url.searchParams.get('type');
  if (type !== 'wish' && type !== 'bug') return fail(400, 'invalid');
  return json(200, { ok: true, items: await loadAll(env, `${type}:`) });
}

// POST /admin/update — patch only the given fields; rebuild pub if wish; mail the wisher when the change is news for them (LOG-165).
async function handleAdminUpdate(request, env, ctx) {
  const r = await readJson(request, LIMIT.wishBody);
  if (r.err) return r.err;
  const d = r.data;
  if (!isStr(d.id)) return fail(400, 'invalid');
  const found = await findById(env, d.id, ['wish', 'bug']);
  if (!found) return fail(404, 'notfound');
  const it = found.item;
  const before = { approved: it.approved, status: it.status, reply: it.reply || '' };
  if (d.approved !== undefined) { if (typeof d.approved !== 'boolean') return fail(400, 'invalid'); it.approved = d.approved; }
  if (d.status !== undefined) { if (!(it.type === 'bug' ? BUG_STATUSES : STATUSES).includes(d.status)) return fail(400, 'invalid'); it.status = d.status; }   // LOG-168: each type has its own set
  if (d.reply !== undefined) { if (!isStr(d.reply) || clen(d.reply) > LIMIT.reply) return fail(400, 'invalid'); it.reply = d.reply; }
  if (d.replyLang !== undefined) { if (!(d.replyLang === '' || LANGS.includes(d.replyLang))) return fail(400, 'invalid'); it.replyLang = d.replyLang; }
  if (d.link !== undefined) { if (!isStr(d.link) || clen(d.link) > LIMIT.link) return fail(400, 'invalid'); it.link = d.link.trim(); }
  if (d.read !== undefined) { if (typeof d.read !== 'boolean') return fail(400, 'invalid'); it.read = d.read; }
  await env.POOL.put(found.key, JSON.stringify(it));
  if (it.type === 'wish') {
    await rebuildPub(env);
    const change = wishChange(before, it);
    if (it.email && change.length && env.MAIL_API_KEY && env.MAIL_FROM && env.TOKEN_SECRET) {
      const m = await wishMail(env, new URL(request.url).origin, it, change);
      mail(env, ctx, it.email, m.subject, m.text);
    }
  } else await rebuildPubBugs(env);   // LOG-169: the 顯示 switch or the verdict changed
  return json(200, { ok: true, item: it });
}

// GET /unsub?id&t — the wisher's one-click opt-out (the link in every mail): drops the email, keeps the wish. No session, no KV lookup beyond the wish.
async function handleUnsub(url, env, iph) {
  if (await rateLimited(env, 'unsub', iph)) return fail(429, 'rate');
  const id = url.searchParams.get('id') || '', t = url.searchParams.get('t') || '';
  const page = (zh, good) => new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${zh ? '許願池' : 'Wishing well'}</title>` +
    `<body style="font:16px/1.7 system-ui,sans-serif;padding:2rem;max-width:36rem;color:#222">${good
      ? (zh ? '好，這則願望之後不再寄信給你。願望本身還在池裡。' : 'Done. No more mail about this wish; the wish itself stays in the well.')
      : (zh ? '這條連結不對或已失效。' : 'This link is not valid.')}</body>`,
    { status: good ? 200 : 400, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
  if (!/^[0-9]+-[0-9a-f]+$/.test(id) || !env.TOKEN_SECRET || !(await hmacVerify(env.TOKEN_SECRET, `unsub:${id}`, t))) return page(true, false);
  const found = await findById(env, id, ['wish']);
  if (!found) return page(true, false);
  if (found.item.email) { found.item.email = ''; await env.POOL.put(found.key, JSON.stringify(found.item)); }
  return page(found.item.lang !== 'en', true);
}

// POST /admin/delete — remove an item; rebuild pub if wish.
async function handleAdminDelete(request, env) {
  const r = await readJson(request, LIMIT.wishBody);
  if (r.err) return r.err;
  if (!isStr(r.data.id)) return fail(400, 'invalid');
  const found = await findById(env, r.data.id, ['wish', 'bug']);
  if (!found) return fail(404, 'notfound');
  await env.POOL.delete(found.key);
  if (found.item.type === 'wish') await rebuildPub(env); else await rebuildPubBugs(env);
  return json(200, { ok: true });
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
export default {
  async fetch(request, env, ctx) {
    const cors = corsHeaders(request, env);
    const withCors = (res) => {
      const h = new Headers(res.headers);
      for (const [k, v] of Object.entries(cors)) h.set(k, v);
      return new Response(res.body, { status: res.status, headers: h });
    };

    try {
      const url = new URL(request.url);
      const path = url.pathname.replace(/\/+$/, '') || '/';
      const method = request.method.toUpperCase();

      if (method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
      if (!env.POOL) return withCors(fail(500, 'config'));

      // Health — no rate limit.
      if (path === '/' || path === '/health') {
        if (method !== 'GET') return withCors(fail(405, 'method'));
        return withCors(json(200, { ok: true, service: 'pool', ts: Date.now() }));
      }

      // Every POST must come from an allowed Origin.
      if (method === 'POST' && !originAllowed(request, env)) return withCors(fail(403, 'origin'));
      if (method !== 'GET' && method !== 'POST') return withCors(fail(405, 'method'));

      const iph = await clientHash(request);

      // Public routes
      if (path === '/submit') return withCors(method === 'POST' ? await handleSubmit(request, env, ctx, iph) : fail(405, 'method'));
      if (path === '/wishes') return withCors(method === 'GET' ? await handleWishes(env, iph) : fail(405, 'method'));
      if (path === '/bugs') return withCors(method === 'GET' ? await handleBugs(env, iph) : fail(405, 'method'));
      if (path === '/vote') return withCors(method === 'POST' ? await handleVote(request, env, iph) : fail(405, 'method'));
      if (path === '/mine') return withCors(method === 'POST' ? await handleMine(request, env, iph) : fail(405, 'method'));
      if (path === '/unsub') return method === 'GET' ? handleUnsub(url, env, iph) : withCors(fail(405, 'method'));   // a browser navigation from the mail; no CORS needed

      // OAuth (browser navigations; no CORS needed)
      if (path === '/auth/start') return method === 'GET' ? handleAuthStart(env) : fail(405, 'method');
      if (path === '/auth/callback') return method === 'GET' ? handleAuthCallback(request, url, env, ctx) : fail(405, 'method');
      // TOTP second factor (XHR from the site; CORS + Origin check apply)
      if (path === '/auth/totp') return withCors(method === 'POST' ? await handleAuthTotp(request, env, ctx, iph) : fail(405, 'method'));

      // Admin routes — Bearer pass required.
      if (path.startsWith('/admin/')) {
        if (!(await requireAdmin(request, env))) return withCors(fail(401, 'auth'));
        if (path === '/admin/list') return withCors(method === 'GET' ? await handleAdminList(url, env) : fail(405, 'method'));
        if (path === '/admin/update') return withCors(method === 'POST' ? await handleAdminUpdate(request, env, ctx) : fail(405, 'method'));
        if (path === '/admin/delete') return withCors(method === 'POST' ? await handleAdminDelete(request, env) : fail(405, 'method'));
      }

      return withCors(fail(404, 'notfound'));
    } catch (e) {
      // Never echo internals; never log request data.
      return withCors(fail(500, 'internal'));
    }
  },
};
