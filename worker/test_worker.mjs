// © 2026 IraStoria. Self-test for worker.js — run: node worker/test_worker.mjs
// Node 20+, zero dependencies. Fakes the KV binding in memory and drives the
// worker through worker.fetch(new Request(...), env, ctx).

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createHmac } from 'node:crypto';

const here = dirname(fileURLToPath(import.meta.url));

// Import worker.js. Without a package.json the .js file may be treated as
// CommonJS on older Node, so fall back to importing the source via a data: URL.
async function loadWorker() {
  try {
    return (await import('./worker.js')).default;
  } catch {
    const src = readFileSync(join(here, 'worker.js'), 'utf8');
    return (await import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'))).default;
  }
}
const worker = await loadWorker();

// ---------------------------------------------------------------------------
// In-memory KV with prefix listing and expirationTtl semantics
// ---------------------------------------------------------------------------
function makeKV() {
  const store = new Map(); // key -> { value, exp } (exp in ms, or 0)
  const alive = (k) => {
    const r = store.get(k);
    if (!r) return null;
    if (r.exp && r.exp <= Date.now()) { store.delete(k); return null; }
    return r;
  };
  return {
    _store: store,
    async get(key, type) {
      const r = alive(key);
      if (!r) return null;
      return type === 'json' ? JSON.parse(r.value) : r.value;
    },
    async put(key, value, opts = {}) {
      if (opts.expirationTtl !== undefined && opts.expirationTtl < 60) throw new Error('KV: expirationTtl must be >= 60');
      store.set(key, { value: String(value), exp: opts.expirationTtl ? Date.now() + opts.expirationTtl * 1000 : 0 });
    },
    async delete(key) { store.delete(key); },
    async list({ prefix = '', cursor } = {}) {
      const keys = [...store.keys()].filter((k) => k.startsWith(prefix) && alive(k)).sort().map((name) => ({ name }));
      return { keys, list_complete: true, cursor: undefined };
    },
  };
}

const ORIGIN = 'https://irastoria.github.io';
function makeEnv() {
  return {
    POOL: makeKV(),
    ALLOWED_ORIGINS: 'https://irastoria.github.io, http://127.0.0.1:8766',
    OWNER_LOGIN: 'IraStoria',
    SITE_URL: 'https://irastoria.github.io/zh/',
    GITHUB_CLIENT_ID: 'Iv1.testclientid',
    GITHUB_CLIENT_SECRET: 'gh-secret-not-real',
    TOKEN_SECRET: 'unit-test-secret-0123456789abcdef0123456789abcdef',
  };
}
const ctx = { waitUntil() {}, passThroughOnException() {} };
const BASE = 'https://pool.example.workers.dev';

// Request helper: opts { method, body(object|string), origin, ip, headers }
async function call(env, path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  if (opts.origin !== null) headers.set('Origin', opts.origin ?? ORIGIN);
  headers.set('CF-Connecting-IP', opts.ip || '203.0.113.7');
  let body;
  if (opts.body !== undefined) {
    body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
    headers.set('Content-Type', 'application/json');
    if (opts.contentLength !== undefined) headers.set('Content-Length', String(opts.contentLength));
  }
  const req = new Request(BASE + path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body, redirect: 'manual' });
  const res = await worker.fetch(req, env, ctx);
  let data = null;
  const text = await res.text();
  try { data = JSON.parse(text); } catch { data = text; }
  return { status: res.status, headers: res.headers, data };
}

// Sign an admin pass the same way the worker does (base64url(payload).base64url(HMAC)).
const b64u = (buf) => Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
function signToken(secret, payload) {
  const p = b64u(JSON.stringify(payload));
  return `${p}.${b64u(createHmac('sha256', secret).update(p).digest())}`;
}
const bearer = (env, sub = 'IraStoria', exp = Math.floor(Date.now() / 1000) + 3600) => ({ Authorization: `Bearer ${signToken(env.TOKEN_SECRET, { sub, exp })}` });

// Independent TOTP reference (RFC 4226/6238, SHA-1) used to drive the worker.
const B32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
function b32decode(s) {
  const clean = s.toUpperCase().replace(/[=\s]/g, '');
  const out = []; let bits = 0, acc = 0;
  for (const ch of clean) { acc = (acc << 5) | B32.indexOf(ch); bits += 5; if (bits >= 8) { bits -= 8; out.push((acc >>> bits) & 0xff); } }
  return Buffer.from(out);
}
function hotpRef(key, counter, digits = 6) {
  const msg = Buffer.alloc(8); msg.writeBigUInt64BE(BigInt(counter));
  const h = createHmac('sha1', key).update(msg).digest();
  const off = h[h.length - 1] & 0x0f;
  const bin = ((h[off] & 0x7f) << 24) | (h[off + 1] << 16) | (h[off + 2] << 8) | h[off + 3];
  return String(bin % 10 ** digits).padStart(digits, '0');
}
const totpRef = (secretB32, tSec, digits = 6) => hotpRef(b32decode(secretB32), Math.floor(tSec / 30), digits);
// RFC 6238 SHA-1 test secret: ASCII "12345678901234567890".
const RFC_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';
// Run fn with Date.now() pinned to `ms`.
async function atTime(ms, fn) {
  const real = Date.now;
  Date.now = () => ms;
  try { return await fn(); } finally { Date.now = real; }
}
// Mock global fetch: GitHub OAuth answers with `login`; every other URL is
// recorded in `calls` and answered by `other` (default: 200 {code:200}).
function mockFetch({ login = 'irastoria', other } = {}) {
  const real = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.includes('login/oauth/access_token')) return new Response(JSON.stringify({ access_token: 'gho_test' }), { headers: { 'Content-Type': 'application/json' } });
    if (u.includes('api.github.com/user')) return new Response(JSON.stringify({ login }), { headers: { 'Content-Type': 'application/json' } });
    calls.push({ url: u, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    if (other) return other(u, init);
    return new Response(JSON.stringify({ code: 200 }), { headers: { 'Content-Type': 'application/json' } });
  };
  return { calls, restore() { globalThis.fetch = real; } };
}
// Drive /auth/start + /auth/callback (GitHub mocked) and return the redirect Location.
async function loginRedirect(env) {
  const state = new URL((await call(env, '/auth/start', { origin: null })).headers.get('location')).searchParams.get('state');
  return (await call(env, `/auth/callback?code=code123&state=${state}`, { origin: null })).headers.get('location');
}
const decodePayload = (token) => JSON.parse(Buffer.from(token.split('.')[0].replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString());

const WISH = { type: 'wish', lang: 'zh', nick: '小星', cat: 'feature', text: '希望有夜間模式' };
const BUG = { type: 'bug', lang: 'en', nick: '', text: 'Dock bubble stuck', trail: ['t+0 click #dock', 't+1 open finder', { k: 'resize', vw: 390 }], meta: { shell: 'mobile', ua: 'Mozilla/5.0 test', vw: 390, vh: 844, ver: 'abc123', page: '/zh/' } };

// ---------------------------------------------------------------------------
// Tiny test runner
// ---------------------------------------------------------------------------
let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log(`  ok   ${name}`); }
  catch (e) { failed++; console.log(`  FAIL ${name}\n       ${e && e.message ? e.message : e}`); }
}
function eq(a, b, msg) {
  const A = JSON.stringify(a), B = JSON.stringify(b);
  if (A !== B) throw new Error(`${msg || 'expected'}: got ${A}, want ${B}`);
}
const ok = (v, msg) => { if (!v) throw new Error(msg || 'assertion failed'); };

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
console.log('worker self-test');

await test('health: GET /health and GET / return ok/service/ts', async () => {
  const env = makeEnv();
  for (const p of ['/health', '/']) {
    const r = await call(env, p, { origin: null });
    eq(r.status, 200); eq(r.data.ok, true); eq(r.data.service, 'pool'); ok(typeof r.data.ts === 'number');
  }
});

await test('OPTIONS preflight: 204 + CORS only for allowed origin', async () => {
  const env = makeEnv();
  const a = await call(env, '/submit', { method: 'OPTIONS' });
  eq(a.status, 204); eq(a.headers.get('access-control-allow-origin'), ORIGIN);
  ok(/POST/.test(a.headers.get('access-control-allow-methods')));
  ok(/Authorization/.test(a.headers.get('access-control-allow-headers')));
  const b = await call(env, '/submit', { method: 'OPTIONS', origin: 'https://evil.example' });
  eq(b.status, 204); eq(b.headers.get('access-control-allow-origin'), null);
  const c = await call(env, '/health', { origin: 'http://127.0.0.1:8766' });
  eq(c.headers.get('access-control-allow-origin'), 'http://127.0.0.1:8766', 'second listed origin');
});

await test('submit wish: happy path stores approved:false, status wishing, iph only', async () => {
  const env = makeEnv();
  const r = await call(env, '/submit', { body: WISH });
  eq(r.status, 200); eq(r.data.ok, true); ok(/^\d+-[0-9a-f]+$/.test(r.data.id), 'id shape');
  eq(r.headers.get('access-control-allow-origin'), ORIGIN);
  const stored = await env.POOL.get(`wish:${r.data.id}`, 'json');
  eq(stored.type, 'wish'); eq(stored.approved, false); eq(stored.status, 'wishing'); eq(stored.votes, 0);
  eq(stored.nick, '小星'); eq(stored.cat, 'feature'); eq(stored.reply, ''); eq(stored.link, '');
  ok(/^[0-9a-f]{16}$/.test(stored.iph), 'iph is 16 hex'); ok(!JSON.stringify(stored).includes('203.0.113.7'), 'raw IP never stored');
});

await test('submit rejects: bad origin 403, missing origin 403', async () => {
  const env = makeEnv();
  const a = await call(env, '/submit', { body: WISH, origin: 'https://evil.example' });
  eq(a.status, 403); eq(a.data.error, 'origin');
  const b = await call(env, '/submit', { body: WISH, origin: null });
  eq(b.status, 403); eq(b.data.error, 'origin');
});

await test('submit rejects: missing nick, bad cat, bad lang, bad type, empty text, long text', async () => {
  const env = makeEnv();
  const cases = [
    { ...WISH, nick: '' }, { ...WISH, nick: '   ' }, { ...WISH, cat: 'music' }, { ...WISH, lang: 'jp' },
    { ...WISH, type: 'poem' }, { ...WISH, text: '  ' }, { ...WISH, text: 'x'.repeat(601) },
    { ...WISH, nick: 'n'.repeat(25) }, { ...BUG, text: '' }, { ...BUG, trail: 'nope' },
    { ...BUG, trail: new Array(201).fill('a') }, { ...BUG, trail: ['x'.repeat(201)] },
  ];
  for (const [i, c] of cases.entries()) {
    const r = await call(env, '/submit', { body: c, ip: `10.0.0.${i}` });
    eq(r.status, 400, `case ${i}`); eq(r.data.error, 'invalid', `case ${i}`);
  }
});

await test('submit rejects oversize: 413 via Content-Length, via real body, and wish > 8 KB', async () => {
  const env = makeEnv();
  const a = await call(env, '/submit', { body: WISH, contentLength: 40000 });
  eq(a.status, 413); eq(a.data.error, 'size');
  const big = { ...BUG, text: 'b'.repeat(1000), trail: new Array(200).fill('t'.repeat(199)) }; // > 32 KB
  const b = await call(env, '/submit', { body: big, ip: '10.1.1.2' });
  eq(b.status, 413); eq(b.data.error, 'size');
  const wish9k = { ...WISH, text: 'w', extra: 'z'.repeat(9000) };
  const c = await call(env, '/submit', { body: wish9k, ip: '10.1.1.3' });
  eq(c.status, 413); eq(c.data.error, 'size');
});

await test('submit rejects bad JSON (400 invalid), non-object JSON too', async () => {
  const env = makeEnv();
  const a = await call(env, '/submit', { body: '{"type": "wish", nope' });
  eq(a.status, 400); eq(a.data.error, 'invalid');
  const b = await call(env, '/submit', { body: '[1,2,3]' });
  eq(b.status, 400); eq(b.data.error, 'invalid');
});

await test('bug submit with trail + meta stored, read:false', async () => {
  const env = makeEnv();
  const r = await call(env, '/submit', { body: BUG });
  eq(r.status, 200);
  const s = await env.POOL.get(`bug:${r.data.id}`, 'json');
  eq(s.type, 'bug'); eq(s.read, false); eq(s.trail.length, 3); eq(s.trail[2], { k: 'resize', vw: 390 });
  eq(s.meta.shell, 'mobile'); eq(s.meta.vw, 390); eq(s.meta.page, '/zh/'); eq(s.nick, '');
  const r2 = await call(env, '/submit', { body: { type: 'bug', lang: 'zh', text: 'no trail no meta' }, ip: '10.2.2.2' });
  eq(r2.status, 200);
});

await test('GET /wishes: only approved, iph stripped, Cache-Control set', async () => {
  const env = makeEnv();
  const a = await call(env, '/submit', { body: WISH });
  const b = await call(env, '/submit', { body: { ...WISH, nick: 'B' } });
  await call(env, '/admin/update', { body: { id: b.data.id, approved: true }, headers: bearer(env) });
  const r = await call(env, '/wishes', { origin: null });
  eq(r.status, 200); eq(r.data.ok, true); ok(typeof r.data.ts === 'number');
  eq(r.data.items.length, 1); eq(r.data.items[0].id, b.data.id); eq(r.data.items[0].nick, 'B');
  ok(!('iph' in r.data.items[0]), 'iph stripped'); ok(!('approved' in r.data.items[0]), 'approved not needed publicly');
  eq(Object.keys(r.data.items[0]).sort(), ['cat', 'id', 'lang', 'link', 'nick', 'reply', 'replyLang', 'status', 'text', 'ts', 'votes']);
  eq(r.headers.get('cache-control'), 'public, max-age=60');
  ok(!JSON.stringify(r.data).includes(a.data.id), 'unapproved wish absent');
});

await test('vote: once ok, second is dup, unapproved/unknown -> 404', async () => {
  const env = makeEnv();
  const w = await call(env, '/submit', { body: WISH });
  const u = await call(env, '/submit', { body: { ...WISH, nick: 'U' } });
  await call(env, '/admin/update', { body: { id: w.data.id, approved: true }, headers: bearer(env) });
  const v1 = await call(env, '/vote', { body: { id: w.data.id } });
  eq(v1.status, 200); eq(v1.data, { ok: true, votes: 1 });
  const v2 = await call(env, '/vote', { body: { id: w.data.id } });
  eq(v2.status, 200); eq(v2.data, { ok: true, votes: 1, dup: true });
  const v3 = await call(env, '/vote', { body: { id: w.data.id }, ip: '198.51.100.9' });
  eq(v3.data.votes, 2, 'other IP can vote');
  const pub = await env.POOL.get('pub:wishes', 'json');
  eq(pub.items[0].votes, 2, 'pub rebuilt after vote');
  const nv = await call(env, '/vote', { body: { id: u.data.id } });
  eq(nv.status, 404); eq(nv.data.error, 'notfound');
  const bad = await call(env, '/vote', { body: { id: 'nope' } });
  eq(bad.status, 400);
});

await test('rate limit: 6th submit from same IP -> 429 rate; other IP unaffected', async () => {
  const env = makeEnv();
  for (let i = 0; i < 5; i++) {
    const r = await call(env, '/submit', { body: { ...WISH, text: `w${i}` } });
    eq(r.status, 200, `submit ${i + 1}`);
  }
  const r6 = await call(env, '/submit', { body: WISH });
  eq(r6.status, 429); eq(r6.data.error, 'rate');
  const other = await call(env, '/submit', { body: WISH, ip: '198.51.100.1' });
  eq(other.status, 200);
  ok(![...env.POOL._store.keys()].some((k) => k.includes('203.0.113.7')), 'rate key does not contain raw IP');
});

await test('admin endpoints: 401 auth without token / with garbage token', async () => {
  const env = makeEnv();
  const a = await call(env, '/admin/list?type=wish', { origin: null });
  eq(a.status, 401); eq(a.data.error, 'auth');
  const b = await call(env, '/admin/update', { body: { id: 'x' }, headers: { Authorization: 'Bearer abc.def' } });
  eq(b.status, 401); eq(b.data.error, 'auth');
  const c = await call(env, '/admin/delete', { body: { id: 'x' }, headers: { Authorization: 'Bearer ' + signToken('wrong-secret', { sub: 'IraStoria', exp: 9999999999 }) } });
  eq(c.status, 401);
  const d = await call(env, '/admin/list?type=wish', { headers: bearer(env, 'someone-else') });
  eq(d.status, 401, 'token for another login rejected');
});

await test('admin: token signed with TOKEN_SECRET for OWNER_LOGIN passes (case-insensitive), lists all', async () => {
  const env = makeEnv();
  await call(env, '/submit', { body: WISH });
  await call(env, '/submit', { body: BUG, ip: '10.3.3.3' });
  const a = await call(env, '/admin/list?type=wish', { headers: bearer(env, 'irastoria') });
  eq(a.status, 200); eq(a.data.items.length, 1); eq(a.data.items[0].approved, false);
  const b = await call(env, '/admin/list?type=bug', { headers: bearer(env) });
  eq(b.status, 200); eq(b.data.items.length, 1); eq(b.data.items[0].trail.length, 3);
  const c = await call(env, '/admin/list?type=cat', { headers: bearer(env) });
  eq(c.status, 400);
});

await test('admin: expired token fails with 401', async () => {
  const env = makeEnv();
  const r = await call(env, '/admin/list?type=wish', { headers: bearer(env, 'IraStoria', Math.floor(Date.now() / 1000) - 5) });
  eq(r.status, 401); eq(r.data.error, 'auth');
});

await test('admin/update: approve -> appears in /wishes; status/reply/link patched; bad status 400', async () => {
  const env = makeEnv();
  const w = await call(env, '/submit', { body: WISH });
  const before = await call(env, '/wishes', { origin: null });
  eq(before.data.items.length, 0);
  const u = await call(env, '/admin/update', { body: { id: w.data.id, approved: true, status: 'considering', reply: '收到', replyLang: 'zh', link: 'work:theme-a' }, headers: bearer(env) });
  eq(u.status, 200); eq(u.data.item.approved, true); eq(u.data.item.status, 'considering'); eq(u.data.item.nick, '小星');
  const after = await call(env, '/wishes', { origin: null });
  eq(after.data.items.length, 1); eq(after.data.items[0].status, 'considering'); eq(after.data.items[0].reply, '收到'); eq(after.data.items[0].link, 'work:theme-a');
  const bad = await call(env, '/admin/update', { body: { id: w.data.id, status: 'maybe' }, headers: bearer(env) });
  eq(bad.status, 400); eq(bad.data.error, 'invalid');
  const nf = await call(env, '/admin/update', { body: { id: '1-ffff', approved: true }, headers: bearer(env) });
  eq(nf.status, 404);
  const bug = await call(env, '/submit', { body: BUG, ip: '10.4.4.4' });
  const rd = await call(env, '/admin/update', { body: { id: bug.data.id, read: true }, headers: bearer(env) });
  eq(rd.status, 200); eq(rd.data.item.read, true);
});

await test('admin/delete removes the wish from KV and from /wishes', async () => {
  const env = makeEnv();
  const w = await call(env, '/submit', { body: WISH });
  await call(env, '/admin/update', { body: { id: w.data.id, approved: true }, headers: bearer(env) });
  eq((await call(env, '/wishes', { origin: null })).data.items.length, 1);
  const d = await call(env, '/admin/delete', { body: { id: w.data.id }, headers: bearer(env) });
  eq(d.status, 200); eq(d.data, { ok: true });
  eq(await env.POOL.get(`wish:${w.data.id}`), null);
  eq((await call(env, '/wishes', { origin: null })).data.items.length, 0);
  const again = await call(env, '/admin/delete', { body: { id: w.data.id }, headers: bearer(env) });
  eq(again.status, 404);
});

await test('auth/start: 302 to github authorize with client_id, scope, signed state', async () => {
  const env = makeEnv();
  const r = await call(env, '/auth/start', { origin: null });
  eq(r.status, 302);
  const loc = new URL(r.headers.get('location'));
  eq(loc.origin + loc.pathname, 'https://github.com/login/oauth/authorize');
  eq(loc.searchParams.get('client_id'), env.GITHUB_CLIENT_ID);
  eq(loc.searchParams.get('scope'), 'read:user');
  const state = loc.searchParams.get('state');
  ok(state && state.split('.').length === 2 && state.split('.')[1].length >= 40, 'state is payload.sig');
});

await test('auth/callback: forged / tampered / missing state rejected', async () => {
  const env = makeEnv();
  const forged = await call(env, '/auth/callback?code=abc&state=' + signToken('not-the-secret', { t: 1 }), { origin: null });
  ok(forged.status === 400 && forged.data.error === 'state', 'forged state -> 400 state');
  const start = await call(env, '/auth/start', { origin: null });
  const real = new URL(start.headers.get('location')).searchParams.get('state');
  const tampered = await call(env, '/auth/callback?code=abc&state=' + real.slice(0, -2) + 'zz', { origin: null });
  eq(tampered.status, 400);
  const missing = await call(env, '/auth/callback?code=abc', { origin: null });
  eq(missing.status, 400);
  ok(!(await env.POOL.get('pub:wishes')), 'nothing written to KV');
});

await test('auth/callback: real state + mocked GitHub -> owner gets pass, stranger gets denied', async () => {
  const env = makeEnv();
  const realFetch = globalThis.fetch;
  let login = 'irastoria';
  globalThis.fetch = async (url, init) => {
    if (String(url).includes('login/oauth/access_token')) {
      const body = JSON.parse(init.body);
      ok(body.client_secret === env.GITHUB_CLIENT_SECRET && body.code === 'code123', 'exchange sends secret+code');
      return new Response(JSON.stringify({ access_token: 'gho_test' }), { headers: { 'Content-Type': 'application/json' } });
    }
    if (String(url).includes('api.github.com/user')) {
      ok(init.headers['User-Agent'], 'GitHub API requires a User-Agent');
      return new Response(JSON.stringify({ login }), { headers: { 'Content-Type': 'application/json' } });
    }
    throw new Error('unexpected fetch ' + url);
  };
  try {
    const state = new URL((await call(env, '/auth/start', { origin: null })).headers.get('location')).searchParams.get('state');
    const r = await call(env, `/auth/callback?code=code123&state=${state}`, { origin: null });
    eq(r.status, 302);
    const loc = r.headers.get('location');
    ok(loc.startsWith(env.SITE_URL + '#wp='), 'redirects to SITE_URL#wp=');
    const token = loc.slice((env.SITE_URL + '#wp=').length);
    ok(token !== 'denied', 'owner not denied');
    const payload = JSON.parse(Buffer.from(token.split('.')[0].replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString());
    eq(payload.sub, 'irastoria'); ok(payload.exp > Date.now() / 1000 + 11 * 3600, 'exp ~12h');
    const adm = await call(env, '/admin/list?type=wish', { headers: { Authorization: 'Bearer ' + token } });
    eq(adm.status, 200, 'issued pass works on admin endpoint');

    login = 'someone-else';
    const state2 = new URL((await call(env, '/auth/start', { origin: null })).headers.get('location')).searchParams.get('state');
    const r2 = await call(env, `/auth/callback?code=code123&state=${state2}`, { origin: null });
    eq(r2.status, 302); eq(r2.headers.get('location'), env.SITE_URL + '#wp=denied');
  } finally {
    globalThis.fetch = realFetch;
  }
});

await test('misc: unknown route 404, wrong method 405, error shape { ok:false, error }', async () => {
  const env = makeEnv();
  const a = await call(env, '/nope', { origin: null });
  eq(a.status, 404); eq(a.data, { ok: false, error: 'notfound' });
  const b = await call(env, '/wishes', { body: {} });
  eq(b.status, 405); eq(b.data.error, 'method');
  const c = await call(env, '/submit', { origin: null });
  eq(c.status, 405);
});

// ---------------------------------------------------------------------------
// TOTP second factor
// ---------------------------------------------------------------------------

await test('TOTP: test-side reference matches RFC 6238 / RFC 4226 SHA-1 vectors', async () => {
  eq(totpRef(RFC_SECRET, 59, 8), '94287082'); eq(totpRef(RFC_SECRET, 59), '287082');
  eq(totpRef(RFC_SECRET, 1111111109, 8), '07081804'); eq(totpRef(RFC_SECRET, 1234567890), '005924');
  eq(hotpRef(b32decode(RFC_SECRET), 0), '755224'); eq(hotpRef(b32decode(RFC_SECRET), 2), '359152'); eq(hotpRef(b32decode(RFC_SECRET), 3), '969429');
  eq(b32decode(' gezdgnbvgy3tqojqgezdgnbvgy3tqojq== ').toString(), '12345678901234567890', 'base32 case/pad/space tolerant');
});

await test('TOTP: worker accepts RFC 6238 vector 287082 at T=59 (window ±1: 755224/359152 ok, 969429 not)', async () => {
  await atTime(59_000, async () => {
    const env = { ...makeEnv(), TOTP_SECRET: RFC_SECRET };
    const pre = signToken(env.TOKEN_SECRET, { sub: 'IraStoria', exp: 59 + 300, pre: 1 });
    const r = await call(env, '/auth/totp', { body: { pre, code: '287082' }, ip: '10.9.9.1' });
    eq(r.status, 200, 'vector code accepted'); eq(r.data.ok, true); ok(typeof r.data.token === 'string');
    for (const [c, want] of [['755224', 200], ['359152', 200], ['969429', 401]]) {
      const x = await call(env, '/auth/totp', { body: { pre, code: c }, ip: '10.9.9.' + c.slice(0, 2) });
      eq(x.status, want, `code ${c}`);
    }
    const n = await call(env, '/auth/totp', { body: { pre, code: 287082 }, ip: '10.9.9.77' });
    eq(n.status, 200, 'numeric code accepted');
  });
});

await test('callback with TOTP_SECRET: redirects to #wp2= pre-token; pre-token is rejected by /admin/list', async () => {
  const env = { ...makeEnv(), TOTP_SECRET: RFC_SECRET };
  const m = mockFetch();
  try {
    const loc = await loginRedirect(env);
    ok(loc.startsWith(env.SITE_URL + '#wp2='), 'redirects to #wp2= (got ' + loc + ')');
    ok(!loc.includes('#wp='), 'no 12 h pass issued');
    const pre = loc.slice((env.SITE_URL + '#wp2=').length);
    const p = decodePayload(pre);
    eq(p.sub, 'irastoria'); eq(p.pre, 1);
    ok(p.exp <= Date.now() / 1000 + 300 && p.exp > Date.now() / 1000 + 250, 'pre exp ~5 min');
    const adm = await call(env, '/admin/list?type=wish', { headers: { Authorization: 'Bearer ' + pre } });
    eq(adm.status, 401, 'pre-token is not an admin pass'); eq(adm.data.error, 'auth');
    eq(m.calls.length, 0, 'no Bark without BARK_KEY');
  } finally { m.restore(); }
});

await test('callback without TOTP_SECRET keeps #wp= behaviour', async () => {
  const env = makeEnv();
  const m = mockFetch();
  try {
    const loc = await loginRedirect(env);
    ok(loc.startsWith(env.SITE_URL + '#wp=') && !loc.includes('#wp2='), 'plain #wp=');
    eq(decodePayload(loc.slice((env.SITE_URL + '#wp=').length)).pre, undefined);
  } finally { m.restore(); }
});

await test('/auth/totp: right code -> 12 h token that passes /admin/list; wrong code -> 401 code', async () => {
  const env = { ...makeEnv(), TOTP_SECRET: 'JBSWY3DPEHPK3PXP' };
  const m = mockFetch();
  try {
    const now = 1_700_000_000_000;
    await atTime(now, async () => {
      const loc = await loginRedirect(env);
      const pre = loc.slice((env.SITE_URL + '#wp2=').length);
      const wrong = await call(env, '/auth/totp', { body: { pre, code: totpRef(env.TOTP_SECRET, now / 1000 + 120) } });
      eq(wrong.status, 401); eq(wrong.data, { ok: false, error: 'code' });
      const junk = await call(env, '/auth/totp', { body: { pre, code: 'abc' } });
      eq(junk.status, 401); eq(junk.data.error, 'code');
      const right = await call(env, '/auth/totp', { body: { pre, code: totpRef(env.TOTP_SECRET, now / 1000) } });
      eq(right.status, 200); eq(right.data.ok, true);
      eq(right.headers.get('access-control-allow-origin'), ORIGIN, 'CORS on /auth/totp');
      const p = decodePayload(right.data.token);
      eq(p.sub, 'irastoria'); eq(p.pre, undefined); ok(p.exp > now / 1000 + 11 * 3600, 'exp ~12h');
      const adm = await call(env, '/admin/list?type=wish', { headers: { Authorization: 'Bearer ' + right.data.token } });
      eq(adm.status, 200, 'token from /auth/totp works on admin endpoint');
    });
  } finally { m.restore(); }
});

await test('/auth/totp: forged / expired / non-pre token -> 401 pre; bad origin 403; rate limit 6th -> 429', async () => {
  const env = { ...makeEnv(), TOTP_SECRET: 'JBSWY3DPEHPK3PXP' };
  const code = totpRef(env.TOTP_SECRET, Date.now() / 1000);
  const forged = await call(env, '/auth/totp', { body: { pre: signToken('wrong-secret', { sub: 'IraStoria', exp: 9999999999, pre: 1 }), code }, ip: '10.8.8.1' });
  eq(forged.status, 401); eq(forged.data, { ok: false, error: 'pre' });
  const expired = await call(env, '/auth/totp', { body: { pre: signToken(env.TOKEN_SECRET, { sub: 'IraStoria', exp: Math.floor(Date.now() / 1000) - 1, pre: 1 }), code }, ip: '10.8.8.2' });
  eq(expired.status, 401); eq(expired.data.error, 'pre');
  const pass = await call(env, '/auth/totp', { body: { pre: signToken(env.TOKEN_SECRET, { sub: 'IraStoria', exp: 9999999999 }), code }, ip: '10.8.8.3' });
  eq(pass.status, 401, 'a real admin pass is not a pre-token'); eq(pass.data.error, 'pre');
  const stranger = await call(env, '/auth/totp', { body: { pre: signToken(env.TOKEN_SECRET, { sub: 'someone', exp: 9999999999, pre: 1 }), code }, ip: '10.8.8.4' });
  eq(stranger.status, 401); eq(stranger.data.error, 'pre');
  const missing = await call(env, '/auth/totp', { body: { code }, ip: '10.8.8.5' });
  eq(missing.status, 401); eq(missing.data.error, 'pre');
  const badOrigin = await call(env, '/auth/totp', { body: { pre: 'x', code }, origin: 'https://evil.example' });
  eq(badOrigin.status, 403);
  const get = await call(env, '/auth/totp', { origin: null });
  eq(get.status, 405);
  const off = await call(makeEnv(), '/auth/totp', { body: { pre: 'x', code } });
  eq(off.status, 500, 'TOTP not configured -> config'); eq(off.data.error, 'config');
  for (let i = 0; i < 5; i++) await call(env, '/auth/totp', { body: { pre: 'x', code }, ip: '10.8.8.9' });
  const sixth = await call(env, '/auth/totp', { body: { pre: 'x', code }, ip: '10.8.8.9' });
  eq(sixth.status, 429); eq(sixth.data.error, 'rate');
});

// ---------------------------------------------------------------------------
// Bark push notifications
// ---------------------------------------------------------------------------

await test('Bark: submit posts to ${BARK_SERVER}/push (default api.day.app); BARK_ON_SUBMIT="0" turns it off', async () => {
  const env = { ...makeEnv(), BARK_KEY: 'devkey123' };
  const m = mockFetch();
  try {
    const w = await call(env, '/submit', { body: WISH });
    eq(w.status, 200);
    eq(m.calls.length, 1, 'one push for a wish');
    eq(m.calls[0].url, 'https://api.day.app/push'); eq(m.calls[0].method, 'POST');
    const b = m.calls[0].body;
    eq(b.device_key, 'devkey123'); eq(b.title, '許願池 · 新願望'); eq(b.body, '小星：希望有夜間模式');
    eq(b.group, 'irastoria-pool'); eq(b.level, 'active');
    const bug = await call(env, '/submit', { body: BUG, ip: '10.5.5.1' });
    eq(bug.status, 200); eq(m.calls.length, 2);
    eq(m.calls[1].body.title, '恥辱柱 · 新回報'); eq(m.calls[1].body.body, '匿名：Dock bubble stuck');
    const long = await call(env, '/submit', { body: { ...WISH, text: 'x'.repeat(200) }, ip: '10.5.5.2' });
    eq(long.status, 200); eq(m.calls[2].body.body, '小星：' + 'x'.repeat(80), 'body truncated to 80');
    const rejected = await call(env, '/submit', { body: { ...WISH, cat: 'nope' }, ip: '10.5.5.3' });
    eq(rejected.status, 400); eq(m.calls.length, 3, 'no push for a rejected submit');

    const custom = { ...env, BARK_SERVER: 'https://bark.example.net/' };
    await call(custom, '/submit', { body: WISH, ip: '10.5.5.4' });
    eq(m.calls[3].url, 'https://bark.example.net/push', 'custom server, trailing slash trimmed');

    const off = { ...env, BARK_ON_SUBMIT: '0' };
    const r = await call(off, '/submit', { body: WISH, ip: '10.5.5.5' });
    eq(r.status, 200); eq(m.calls.length, 4, 'BARK_ON_SUBMIT=0 -> no push');
    const on = { ...env, BARK_ON_SUBMIT: '1' };
    await call(on, '/submit', { body: WISH, ip: '10.5.5.6' });
    eq(m.calls.length, 5);
  } finally { m.restore(); }
});

await test('Bark: no BARK_KEY -> no push at all', async () => {
  const env = makeEnv();
  const m = mockFetch();
  try {
    await call(env, '/submit', { body: WISH });
    await loginRedirect(env);
    eq(m.calls.length, 0);
  } finally { m.restore(); }
});

await test('Bark: denied login (stranger, wrong code, forged pre) pushes 登入被拒 timeSensitive', async () => {
  const env = { ...makeEnv(), BARK_KEY: 'devkey123', TOTP_SECRET: 'JBSWY3DPEHPK3PXP' };
  let m = mockFetch({ login: 'someone-else' });
  try {
    const loc = await loginRedirect(env);
    eq(loc, env.SITE_URL + '#wp=denied');
    eq(m.calls.length, 1); eq(m.calls[0].body.title, '許願池 · 登入被拒'); eq(m.calls[0].body.level, 'timeSensitive');
    ok(m.calls[0].body.body.includes('owner') && m.calls[0].body.body.includes('someone-else') && m.calls[0].body.body.includes('?/?'), 'reason + login + geo: ' + m.calls[0].body.body);
    eq(m.calls[0].body.group, 'irastoria-pool');
  } finally { m.restore(); }
  m = mockFetch();
  try {
    const pre = (await loginRedirect(env)).split('#wp2=')[1];
    eq(m.calls.length, 0, 'GitHub-ok alone (TOTP pending) is not a success yet');
    const wrong = await call(env, '/auth/totp', { body: { pre, code: '000000' } });
    eq(wrong.status, 401);
    eq(m.calls.length, 1); eq(m.calls[0].body.title, '許願池 · 登入被拒');
    ok(m.calls[0].body.body.startsWith('code · irastoria'), 'reason code + login: ' + m.calls[0].body.body);
    const forged = await call(env, '/auth/totp', { body: { pre: signToken('wrong-secret', { sub: 'IraStoria', exp: 9999999999, pre: 1 }), code: '000000' }, ip: '10.6.6.1' });
    eq(forged.status, 401);
    eq(m.calls.length, 2); ok(m.calls[1].body.body.startsWith('pre ·'), 'reason pre: ' + m.calls[1].body.body);
  } finally { m.restore(); }
});

await test('Bark: successful sign-in pushes 登入成功 (after TOTP, and after GitHub when TOTP is off)', async () => {
  const withTotp = { ...makeEnv(), BARK_KEY: 'devkey123', TOTP_SECRET: 'JBSWY3DPEHPK3PXP' };
  let m = mockFetch();
  try {
    const pre = (await loginRedirect(withTotp)).split('#wp2=')[1];
    const r = await call(withTotp, '/auth/totp', { body: { pre, code: totpRef(withTotp.TOTP_SECRET, Date.now() / 1000) } });
    eq(r.status, 200);
    eq(m.calls.length, 1); eq(m.calls[0].url, 'https://api.day.app/push');
    eq(m.calls[0].body.title, '許願池 · 登入成功'); eq(m.calls[0].body.level, 'timeSensitive');
    ok(/^irastoria · \?\/\? · \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(m.calls[0].body.body), 'login · country/city · ISO: ' + m.calls[0].body.body);
  } finally { m.restore(); }
  const noTotp = { ...makeEnv(), BARK_KEY: 'devkey123' };
  m = mockFetch();
  try {
    const loc = await loginRedirect(noTotp);
    ok(loc.startsWith(noTotp.SITE_URL + '#wp='));
    eq(m.calls.length, 1); eq(m.calls[0].body.title, '許願池 · 登入成功');
  } finally { m.restore(); }
});

await test('Bark: failing endpoint (throws / rejects / 500) never breaks the response', async () => {
  const env = { ...makeEnv(), BARK_KEY: 'devkey123', TOTP_SECRET: 'JBSWY3DPEHPK3PXP' };
  const modes = [
    () => { throw new Error('sync boom'); },
    () => Promise.reject(new Error('async boom')),
    () => new Response('nope', { status: 500 }),
    () => undefined,
  ];
  for (const other of modes) {
    const m = mockFetch({ other });
    try {
      const w = await call(env, '/submit', { body: WISH, ip: '10.7.7.' + modes.indexOf(other) });
      eq(w.status, 200, 'submit ok despite Bark failure'); eq(w.data.ok, true);
      const pre = (await loginRedirect(env)).split('#wp2=')[1];
      const r = await call(env, '/auth/totp', { body: { pre, code: totpRef(env.TOTP_SECRET, Date.now() / 1000) }, ip: '10.7.7.' + (10 + modes.indexOf(other)) });
      eq(r.status, 200, 'totp ok despite Bark failure');
      const d = await call(env, '/auth/totp', { body: { pre, code: '000000' }, ip: '10.7.7.' + (20 + modes.indexOf(other)) });
      eq(d.status, 401, 'denied path still answers despite Bark failure');
      ok(m.calls.length >= 3, 'pushes were attempted');
    } finally { m.restore(); }
  }
  // Give any rejected push promises a tick to settle so nothing leaks as unhandled.
  await new Promise((r) => setTimeout(r, 10));
});

// --- LOG-161 追記⑥: POST /mine — the sender's own copies checked against the Worker
await test('POST /mine: pending / public / gone per id; bugs and unknown ids read as gone', async () => {
  const env = makeEnv();
  const a = await call(env, '/submit', { body: WISH, ip: '10.9.0.1' });
  const b = await call(env, '/submit', { body: { ...WISH, text: '第二個願望' }, ip: '10.9.0.2' });
  const c = await call(env, '/submit', { body: { ...WISH, text: '第三個願望' }, ip: '10.9.0.3' });
  const bug = await call(env, '/submit', { body: BUG, ip: '10.9.0.4' });
  await call(env, '/admin/update', { body: { id: b.data.id, approved: true }, headers: bearer(env) });
  await call(env, '/admin/delete', { body: { id: c.data.id }, headers: bearer(env) });
  const r = await call(env, '/mine', { body: { ids: [a.data.id, b.data.id, c.data.id, bug.data.id, 'nope-0000'] } });
  eq(r.status, 200); eq(r.data.ok, true);
  eq(r.data.states, { [a.data.id]: 'pending', [b.data.id]: 'public', [c.data.id]: 'gone', [bug.data.id]: 'gone', 'nope-0000': 'gone' });
  eq(Object.keys(r.data).sort(), ['ok', 'states'], 'nothing but the three words comes back');
  // un-approving a public wish turns it back into pending
  await call(env, '/admin/update', { body: { id: b.data.id, approved: false }, headers: bearer(env) });
  eq((await call(env, '/mine', { body: { ids: [b.data.id] } })).data.states[b.data.id], 'pending');
});
await test('POST /mine: shape, size and origin guards; GET is 405; rate limited per IP', async () => {
  const env = makeEnv();
  eq((await call(env, '/mine', { body: {} })).status, 400);
  eq((await call(env, '/mine', { body: { ids: 'x' } })).status, 400);
  eq((await call(env, '/mine', { body: { ids: [''] } })).status, 400);
  eq((await call(env, '/mine', { body: { ids: [1] } })).status, 400);
  eq((await call(env, '/mine', { body: { ids: Array.from({ length: 11 }, (_, i) => 'id' + i) } })).status, 400, 'more than 10 ids');
  eq((await call(env, '/mine', { body: { ids: [] } })).status, 200, 'empty list is fine');
  eq((await call(env, '/mine', { body: { ids: ['a'] }, origin: 'https://evil.example' })).status, 403);
  eq((await call(env, '/mine', { origin: null })).status, 405);
  let last = null;
  for (let i = 0; i < 31; i++) last = await call(env, '/mine', { body: { ids: [] }, ip: '10.9.9.9' });
  eq(last.status, 429); eq(last.data.error, 'rate');
});

// ---------------------------------------------------------------------------
// LOG-165: optional email on a wish, mail on the owner's updates, unsubscribe
// ---------------------------------------------------------------------------
const MAIL_API = 'https://api.resend.com/emails';
const mailsOf = (m) => m.calls.filter((c) => c.url === MAIL_API);

await test('email (LOG-165): optional on a wish, validated, lowercased, stored, never public', async () => {
  const env = makeEnv();
  const a = await call(env, '/submit', { body: { ...WISH, email: ' Wisher@Example.COM ' } });
  eq(a.status, 200);
  eq((await env.POOL.get(`wish:${a.data.id}`, 'json')).email, 'wisher@example.com', 'stored, trimmed, lowercased');
  const b = await call(env, '/submit', { body: { ...WISH, email: 'not-an-email' }, ip: '203.0.113.8' }); eq(b.status, 400); eq(b.data.error, 'invalid');
  const c = await call(env, '/submit', { body: { ...WISH, email: '' }, ip: '203.0.113.9' }); eq(c.status, 200); eq((await env.POOL.get(`wish:${c.data.id}`, 'json')).email, '');
  const d = await call(env, '/submit', { body: { ...WISH, email: 'x'.repeat(120) + '@example.com' }, ip: '203.0.113.10' }); eq(d.status, 400, 'over 120 chars');
  await call(env, '/admin/update', { body: { id: a.data.id, approved: true }, headers: bearer(env) });
  const w = await call(env, '/wishes'); ok(!JSON.stringify(w.data).includes('example.com'), 'email never in /wishes'); ok(!('email' in w.data.items[0]), 'no email key publicly');
  const m = await call(env, '/mine', { body: { ids: [a.data.id] } }); ok(!JSON.stringify(m.data).includes('example.com'), 'nor in /mine');
  const l = await call(env, '/admin/list?type=wish', { headers: bearer(env) }); ok(l.data.items.some((x) => x.email === 'wisher@example.com'), 'the owner sees it');
});

await test('mail (LOG-165): approval / status / reply mail the wisher once each, quoting the wish, with an unsubscribe link; re-saves and link edits send nothing; silent without MAIL_API_KEY', async () => {
  const env = { ...makeEnv(), MAIL_API_KEY: 'k-test', MAIL_FROM: 'IraStoria <pool@example.net>' };
  const m = mockFetch();
  try {
    const a = await call(env, '/submit', { body: { ...WISH, email: 'wisher@example.com' } });
    await call(env, '/admin/update', { body: { id: a.data.id, approved: true }, headers: bearer(env) });
    eq(mailsOf(m).length, 1, 'approval -> one mail');
    const b1 = mailsOf(m)[0]; eq(b1.method, 'POST'); eq(b1.body.to, ['wisher@example.com']); eq(b1.body.from, env.MAIL_FROM);
    ok(/許願池/.test(b1.body.subject), 'zh subject'); ok(b1.body.text.includes('希望有夜間模式'), 'quotes the wish'); ok(b1.body.text.includes('已放上牆'), 'says it is on the wall');
    ok(b1.body.text.includes(`${BASE}/unsub?id=${a.data.id}&t=`), 'unsubscribe link back to this worker'); ok(b1.body.text.includes('https://irastoria.github.io/zh/'), 'site link in the wish language');
    await call(env, '/admin/update', { body: { id: a.data.id, status: 'wishing', reply: '', link: 'app:demos' }, headers: bearer(env) });
    eq(mailsOf(m).length, 1, 'nothing changed for the wisher -> no mail');
    await call(env, '/admin/update', { body: { id: a.data.id, status: 'done', reply: '做好了' }, headers: bearer(env) });
    eq(mailsOf(m).length, 2, 'status + reply -> one mail');
    const b2 = mailsOf(m)[1].body; ok(b2.text.includes('已實現') && b2.text.includes('「做好了」'), 'both in the one mail'); ok(/已實現/.test(b2.subject), 'granted in the subject');
    await call(env, '/admin/update', { body: { id: a.data.id, status: 'done', reply: '做好了' }, headers: bearer(env) });
    eq(mailsOf(m).length, 2, 'a re-save of the same values sends nothing');
    await call(env, '/admin/update', { body: { id: a.data.id, approved: false }, headers: bearer(env) });
    eq(mailsOf(m).length, 2, 'unapproving is not news');
    const e = await call(env, '/submit', { body: { ...WISH, lang: 'en', text: 'night mode please', email: 'en@example.com' }, ip: '203.0.113.8' });
    await call(env, '/admin/update', { body: { id: e.data.id, status: 'building' }, headers: bearer(env) });
    const b3 = mailsOf(m)[2].body; ok(/Wishing well/.test(b3.subject) && b3.text.includes('In progress') && b3.text.includes('night mode please') && b3.text.includes('/en/'), 'english wish -> english mail');
    const quiet = makeEnv();
    const c = await call(quiet, '/submit', { body: { ...WISH, email: 'quiet@example.com' }, ip: '203.0.113.9' });
    await call(quiet, '/admin/update', { body: { id: c.data.id, status: 'done' }, headers: bearer(quiet) });
    eq(mailsOf(m).length, 3, 'no MAIL_API_KEY -> no mail');
    const env3 = { ...env, MAIL_API: 'https://mail.example.org/send' };
    const d = await call(env3, '/submit', { body: { ...WISH, email: 'x@example.com' }, ip: '203.0.113.10' });
    await call(env3, '/admin/update', { body: { id: d.data.id, approved: true }, headers: bearer(env3) });
    ok(m.calls.some((x) => x.url === 'https://mail.example.org/send' && x.body.to[0] === 'x@example.com'), 'MAIL_API override');
    const bugs = await call(env, '/submit', { body: BUG, ip: '203.0.113.11' });
    await call(env, '/admin/update', { body: { id: bugs.data.id, read: true }, headers: bearer(env) });
    eq(mailsOf(m).length, 3, 'a bug update never mails (the override mail above went to its own URL)');
  } finally { m.restore(); }
});

await test('unsub (LOG-165): a forged link 400s and changes nothing; the signed link drops the email (the wish stays) and later updates send nothing', async () => {
  const env = { ...makeEnv(), MAIL_API_KEY: 'k-test', MAIL_FROM: 'IraStoria <pool@example.net>' };
  const m = mockFetch();
  try {
    const a = await call(env, '/submit', { body: { ...WISH, email: 'wisher@example.com' } });
    const bad = await call(env, `/unsub?id=${a.data.id}&t=forged`, { origin: null }); eq(bad.status, 400); ok(/連結不對/.test(bad.data), 'zh refusal page');
    eq((await env.POOL.get(`wish:${a.data.id}`, 'json')).email, 'wisher@example.com', 'forged link changes nothing');
    const t = b64u(createHmac('sha256', env.TOKEN_SECRET).update(`unsub:${a.data.id}`).digest());
    const good = await call(env, `/unsub?id=${a.data.id}&t=${t}`, { origin: null }); eq(good.status, 200); ok(/不再寄信/.test(good.data)); ok(/text\/html/.test(good.headers.get('content-type')));
    const w = await env.POOL.get(`wish:${a.data.id}`, 'json'); eq(w.email, ''); eq(w.text, WISH.text, 'the wish itself stays');
    const again = await call(env, `/unsub?id=${a.data.id}&t=${t}`, { origin: null }); eq(again.status, 200, 'idempotent');
    await call(env, '/admin/update', { body: { id: a.data.id, approved: true, status: 'done' }, headers: bearer(env) });
    eq(mailsOf(m).length, 0, 'no email left -> no mail');
    const post = await call(env, `/unsub?id=${a.data.id}&t=${t}`, { method: 'POST', body: {} }); eq(post.status, 405);
  } finally { m.restore(); }
});

await test('bug status (LOG-168): new on submit; admin sets any of the bug set; a wish status on a bug (or vice versa) is 400; never mails', async () => {
  const env = { ...makeEnv(), MAIL_API_KEY: 'k-test', MAIL_FROM: 'IraStoria <pool@example.net>' };
  const m = mockFetch();
  try {
    const b = await call(env, '/submit', { body: BUG });
    eq((await env.POOL.get(`bug:${b.data.id}`, 'json')).status, 'new');
    for (const st of ['open', 'watch', 'fixed', 'declined', 'new']) {
      const r = await call(env, '/admin/update', { body: { id: b.data.id, status: st }, headers: bearer(env) });
      eq(r.status, 200); eq(r.data.item.status, st);
    }
    const bad = await call(env, '/admin/update', { body: { id: b.data.id, status: 'done' }, headers: bearer(env) }); eq(bad.status, 400, 'a wish status on a bug');
    const w = await call(env, '/submit', { body: WISH, ip: '203.0.113.8' });
    const bad2 = await call(env, '/admin/update', { body: { id: w.data.id, status: 'fixed' }, headers: bearer(env) }); eq(bad2.status, 400, 'a bug status on a wish');
    eq(mailsOf(m).length, 0, 'bug updates never mail');
  } finally { m.restore(); }
});

await test('bug show (LOG-169): a report is hidden until the owner switches it on; GET /bugs carries only nick/text/verdict/date; off again and delete both drop it', async () => {
  const env = makeEnv();
  const b = await call(env, '/submit', { body: BUG });
  eq((await env.POOL.get(`bug:${b.data.id}`, 'json')).approved, false, 'off on submit');
  let r = await call(env, '/bugs'); eq(r.status, 200); eq(r.data.items.length, 0, 'hidden by default');
  await call(env, '/admin/update', { body: { id: b.data.id, approved: true, status: 'fixed' }, headers: bearer(env) });
  r = await call(env, '/bugs'); eq(r.data.items.length, 1); const it = r.data.items[0];
  eq(Object.keys(it).sort(), ['id', 'lang', 'nick', 'status', 'text', 'ts']); eq(it.status, 'fixed'); eq(it.text, BUG.text);
  ok(!JSON.stringify(r.data).includes('trail') && !JSON.stringify(r.data).includes('Mozilla') && !JSON.stringify(r.data).includes('iph'), 'no trail, no meta, no iph');
  ok(/max-age=60/.test(r.headers.get('cache-control')), 'cached a minute');
  await call(env, '/admin/update', { body: { id: b.data.id, status: 'watch' }, headers: bearer(env) });
  eq((await call(env, '/bugs')).data.items[0].status, 'watch', 'a verdict change reaches the public copy');
  await call(env, '/admin/update', { body: { id: b.data.id, approved: false }, headers: bearer(env) });
  eq((await call(env, '/bugs')).data.items.length, 0, 'switched off again');
  await call(env, '/admin/update', { body: { id: b.data.id, approved: true }, headers: bearer(env) });
  await call(env, '/admin/delete', { body: { id: b.data.id }, headers: bearer(env) });
  eq((await call(env, '/bugs')).data.items.length, 0, 'deleted');
  eq((await call(env, '/bugs', { method: 'POST', body: {} })).status, 405);
});

console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);
