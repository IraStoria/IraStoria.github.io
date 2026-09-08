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

console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);
