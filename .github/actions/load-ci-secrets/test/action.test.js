'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { run } = require('../src/action.js');

const API_URL = 'https://secrets.homelab.robinjoon.xyz';
const AUDIENCE = 'urn:homelab:ci-secrets:v1';
const JWT = 'synthetic-oidc-token-not-a-real-credential';
const VALUE = 'synthetic-ci-value-not-a-real-credential';
const encoder = new TextEncoder();

function response(body = { zot: { TOKEN: VALUE } }, options = {}) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return new Response(text, {
    status: options.status ?? 200,
    headers: { 'content-type': 'application/json', ...options.headers },
  });
}

function streamedResponse(chunks, { failAtEnd = false } = {}) {
  let next = 0;
  return new Response(new ReadableStream({
    pull(controller) {
      if (next < chunks.length) {
        const chunk = chunks[next++];
        controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk);
      } else if (failAtEnd) {
        controller.error(new Error(`synthetic stream error ${JWT} ${VALUE}`));
      } else {
        controller.close();
      }
    },
  }), { headers: { 'content-type': 'application/json' } });
}

async function invoke({ app = 'zot', env = Object.create(null), fetchResponse,
  inputError, tokenError, token = JWT } = {}) {
  const events = [];
  const failures = [];
  const fetches = [];
  const sleeps = [];
  const inputCalls = [];
  const audiences = [];
  const core = {
    getInput(name, options) {
      inputCalls.push({ name, options });
      if (inputError) throw inputError;
      if (options.required && !app) throw new Error('Required input missing');
      return app;
    },
    async getIDToken(audience) {
      audiences.push(audience);
      if (tokenError) throw tokenError;
      return token;
    },
    setSecret(value) { events.push({ type: 'mask', value }); },
    exportVariable(key, value) {
      events.push({ type: 'export', key, value });
      Object.defineProperty(env, key, { value, configurable: true, enumerable: true, writable: true });
    },
    setFailed(message) { failures.push(message); },
  };
  await run({
    core,
    env,
    fetch: async (url, options) => {
      fetches.push({ url, options });
      return fetchResponse ? fetchResponse(fetches.length) : response();
    },
    sleep: async (milliseconds) => { sleeps.push(milliseconds); },
  });
  return { events, failures, fetches, sleeps, inputCalls, audiences, env };
}

function exportsOf(result) {
  return result.events.filter((event) => event.type === 'export');
}

function assertSuccess(result) {
  assert.equal(result.failures.length, 0, 'The Action should succeed.');
}

function assertSafeFailure(result, forbidden = []) {
  assert.equal(result.failures.length, 1, 'The Action should report one failure.');
  assert.equal(typeof result.failures[0], 'string', 'Failure diagnostics must be text.');
  assert.ok(result.failures[0].length > 0, 'Failure diagnostics must not be empty.');
  for (const sensitive of [JWT, VALUE, ...forbidden]) {
    assert.equal(result.failures[0].includes(sensitive), false, 'Failure diagnostics must not contain sensitive data.');
  }
  assert.equal(exportsOf(result).length, 0, 'A rejected response must export no values.');
}

test('requests the required app input, fixed OIDC audience and fixed HTTPS GET endpoint', async () => {
  const result = await invoke();
  assertSuccess(result);
  assert.deepEqual(result.inputCalls, [{ name: 'app', options: { required: true } }]);
  assert.deepEqual(result.audiences, [AUDIENCE]);
  assert.equal(result.fetches.length, 1);
  const { url, options } = result.fetches[0];
  assert.equal(String(url), `${API_URL}/v1/ci/secrets/zot`);
  assert.equal(options.method, 'GET');
  assert.equal(options.redirect, 'error');
  assert.equal(options.body, undefined);
  assert.ok(options.signal instanceof AbortSignal, 'The request must be abortable.');
  const headers = new Headers(options.headers);
  assert.ok(headers.get('authorization') === `Bearer ${JWT}`, 'Authorization must contain the OIDC bearer token.');
  assert.ok(!String(url).includes(JWT), 'The token must not appear in the URL.');
  assert.ok(result.env.TOKEN === VALUE, 'The returned value must reach the job environment.');
});

test('valid app names include one character, digits and the 63-character boundary', async (t) => {
  for (const app of ['a', '1', 'test-app-2', 'constructor', 'a'.repeat(63)]) {
    await t.test(`valid app length ${app.length}`, async () => {
      const result = await invoke({ app, fetchResponse: () => response({ [app]: { TOKEN: VALUE } }) });
      assertSuccess(result);
      assert.equal(String(result.fetches[0].url), `${API_URL}/v1/ci/secrets/${app}`);
    });
  }
});

test('missing and invalid app names fail before OIDC or network access', async (t) => {
  const invalid = ['', ' ', 'UPPER', '-first', 'last-', 'under_score', 'a/b', 'a?b', 'a.b', '가', 'a'.repeat(64)];
  for (let index = 0; index < invalid.length; index++) {
    await t.test(`invalid input ${index + 1}`, async () => {
      const result = await invoke({ app: invalid[index] });
      assertSafeFailure(result);
      assert.equal(result.audiences.length, 0);
      assert.equal(result.fetches.length, 0);
    });
  }
  const result = await invoke({ inputError: new Error(`input failure ${JWT} ${VALUE}`) });
  assertSafeFailure(result);
  assert.equal(result.fetches.length, 0);
});

test('OIDC failures do not expose the original exception or call SMS', async () => {
  const result = await invoke({ tokenError: new Error(`OIDC failure ${JWT} ${VALUE}`) });
  assertSafeFailure(result);
  assert.equal(result.fetches.length, 0);
});

test('rejects a missing, additional or invalid outer app object', async (t) => {
  const invalid = [null, [], 'null', '[]', 'true', '23', '"text"', {},
    { other: { TOKEN: VALUE } }, { zot: { TOKEN: VALUE }, other: { TOKEN: VALUE } },
    { zot: null }, { zot: [] }, { zot: VALUE }, { zot: {} }];
  for (let index = 0; index < invalid.length; index++) {
    await t.test(`invalid object ${index + 1}`, async () => {
      const result = await invoke({ fetchResponse: () => response(invalid[index]) });
      assertSafeFailure(result);
    });
  }
});

test('rejects literal and escaped duplicate JSON keys before exporting', async (t) => {
  const invalid = [
    '{"zot":{"TOKEN":"first-synthetic-secret","TOKEN":"second-synthetic-secret"}}',
    '{"zot":{"TOKEN":"first-synthetic-secret","\\u0054OKEN":"second-synthetic-secret"}}',
    '{"zot":{"TOKEN":"first-synthetic-secret"},"zot":{"TOKEN":"second-synthetic-secret"}}',
    '{"zot":{"TOKEN":"first-synthetic-secret"},"z\\u006ft":{"TOKEN":"second-synthetic-secret"}}',
    '{"zot":{"\\u0054OKEN":"first-synthetic-secret","T\\u004fKEN":"second-synthetic-secret"}}',
  ];
  for (let index = 0; index < invalid.length; index++) {
    await t.test(`duplicate form ${index + 1}`, async () => {
      const result = await invoke({ fetchResponse: () => response(invalid[index]) });
      assertSafeFailure(result, ['first-synthetic-secret', 'second-synthetic-secret']);
    });
  }
});

test('rejects invalid names, reserved keys and prefixes case-insensitively', async (t) => {
  const invalid = ['', '1KEY', 'HAS-HYPHEN', 'HAS SPACE', '한글', 'KEY\n',
    'PATH', 'path', 'BaSh_EnV', 'env', 'node_options',
    'GITHUB_TOKEN', 'github_token', 'Runner_TEMP', 'actions_value', 'INPUT_APP'];
  for (let index = 0; index < invalid.length; index++) {
    await t.test(`invalid environment key ${index + 1}`, async () => {
      const result = await invoke({ fetchResponse: () => response({ zot: { VALID_KEY: VALUE, [invalid[index]]: 'second-synthetic-value' } }) });
      assertSafeFailure(result);
    });
  }
});

test('accepts ordinary names that only resemble reserved keys', async () => {
  const values = { _KEY: VALUE, key2: VALUE, PATH_SUFFIX: VALUE, GITHUB: VALUE, X_GITHUB_TOKEN: VALUE };
  const result = await invoke({ fetchResponse: () => response({ zot: values }) });
  assertSuccess(result);
  assert.equal(exportsOf(result).length, Object.keys(values).length);
});

test('rejects empty, non-string, NUL and unpaired-surrogate values without partial exports', async (t) => {
  const invalid = ['', null, 1, false, [], {}, '\0', 'before\0after', '\ud800', '\udfff'];
  for (let index = 0; index < invalid.length; index++) {
    await t.test(`invalid value ${index + 1}`, async () => {
      const result = await invoke({ fetchResponse: () => response({ zot: { FIRST: VALUE, SECOND: invalid[index] } }) });
      assertSafeFailure(result);
    });
  }
});

test('preserves unicode, quotes, shell syntax and CRLF multiline values', async () => {
  const multiline = '첫째 줄\r\n둘째 "줄"\nemoji 🐈\n$(echo unchanged) `unchanged` %value%';
  const result = await invoke({ fetchResponse: () => response({ zot: { MULTILINE: multiline } }) });
  assertSuccess(result);
  assert.ok(result.env.MULTILINE === multiline, 'The original string must be preserved exactly.');
});

test('handles prototype-like keys as data without prototype pollution', async () => {
  const beforePrototype = Object.getPrototypeOf({});
  const env = {};
  const result = await invoke({ env, fetchResponse: () => response('{"zot":{"__proto__":"synthetic-proto","constructor":"synthetic-constructor"}}') });
  assertSuccess(result);
  assert.equal(exportsOf(result).length, 2);
  assert.ok(Object.hasOwn(result.env, '__proto__'), 'A valid prototype-like name must remain an own data property.');
  assert.ok(Object.hasOwn(result.env, 'constructor'), 'A valid constructor name must remain an own data property.');
  assert.equal(Object.getPrototypeOf(result.env), beforePrototype);
  assert.equal(Object.getPrototypeOf({}), beforePrototype);
  assert.equal(Object.prototype.polluted, undefined);
});

test('checks all inherited environment conflicts before any export', async () => {
  const env = { SECOND: 'existing-synthetic-value' };
  const result = await invoke({ env, fetchResponse: () => response({ zot: { FIRST: VALUE, SECOND: 'new-synthetic-value' } }) });
  assertSafeFailure(result, ['existing-synthetic-value', 'new-synthetic-value']);
  assert.equal(Object.hasOwn(env, 'FIRST'), false);
});

test('allows reuse of the same exact environment key and value', async () => {
  const env = { TOKEN: VALUE };
  const result = await invoke({ env });
  assertSuccess(result);
  assert.ok(env.TOKEN === VALUE, 'The existing matching value must be preserved.');
  assert.ok(result.events.some((event) => event.type === 'mask' && event.value === VALUE), 'Reused values must still be masked.');
});

test('rejects case-insensitive environment collisions conservatively', async (t) => {
  for (const sameValue of [false, true]) {
    await t.test(sameValue ? 'same value with a different key case' : 'different value with a different key case', async () => {
      const result = await invoke({ env: { token: sameValue ? VALUE : 'existing-synthetic-value' } });
      assertSafeFailure(result);
    });
  }
  const result = await invoke({ fetchResponse: () => response({ zot: { TOKEN: VALUE, token: VALUE } }) });
  assertSafeFailure(result);
});

test('masks every response value before exporting the first value', async () => {
  const values = { FIRST: VALUE, SECOND: 'another-synthetic-value', THIRD: 'third-synthetic-value' };
  const result = await invoke({ fetchResponse: () => response({ zot: values }) });
  assertSuccess(result);
  const firstExport = result.events.findIndex((event) => event.type === 'export');
  assert.ok(firstExport >= 0, 'There must be at least one export.');
  for (const value of Object.values(values)) {
    assert.ok(result.events.slice(0, firstExport).some((event) => event.type === 'mask' && event.value === value), 'Each value must be masked before any export.');
  }
});

test('accepts application/json with a charset and rejects non-JSON content types', async (t) => {
  const valid = await invoke({ fetchResponse: () => response(undefined, { headers: { 'content-type': 'application/json; charset=utf-8' } }) });
  assertSuccess(valid);
  for (const contentType of ['text/plain', 'text/html', 'application/problem+json', 'application/jsonp']) {
    await t.test(contentType, async () => {
      const result = await invoke({ fetchResponse: () => response(undefined, { headers: { 'content-type': contentType } }) });
      assertSafeFailure(result);
    });
  }
  const missing = await invoke({ fetchResponse: () => new Response(encoder.encode(JSON.stringify({ zot: { TOKEN: VALUE } }))) });
  assertSafeFailure(missing);
});

test('does not retry malformed JSON or a contract-invalid successful response', async (t) => {
  for (const body of ['{', `not json ${JWT} ${VALUE}`, '{"zot":{"TOKEN":"value"}} trailing', { zot: {} }]) {
    await t.test(typeof body === 'string' ? 'invalid JSON' : 'invalid payload', async () => {
      const result = await invoke({ fetchResponse: () => response(body) });
      assertSafeFailure(result);
      assert.equal(result.fetches.length, 1);
      assert.equal(result.sleeps.length, 0);
    });
  }
});

test('reads streamed JSON across byte boundaries without corrupting Unicode', async () => {
  const original = '한글 🐈\r\nline two';
  const bytes = encoder.encode(JSON.stringify({ zot: { TOKEN: original } }));
  const chunks = Array.from(bytes, (byte) => Uint8Array.of(byte));
  const result = await invoke({ fetchResponse: () => streamedResponse(chunks) });
  assertSuccess(result);
  assert.ok(result.env.TOKEN === original, 'Splitting UTF-8 sequences across chunks must preserve the value.');
});

test('rejects malformed UTF-8, truncated streams and stream failures', async (t) => {
  const makeResponses = [
    () => streamedResponse(['{"zot":{"TOKEN":"', Uint8Array.of(0xc3, 0x28), '"}}']),
    () => streamedResponse(['{"zot":{"TOKEN":"', Uint8Array.of(0xf0, 0x9f)]),
    () => streamedResponse(['{"zot":{"TOKEN":"unfinished']),
    () => streamedResponse(['{"zot":'], { failAtEnd: true }),
  ];
  for (let index = 0; index < makeResponses.length; index++) {
    await t.test(`invalid stream ${index + 1}`, async () => {
      const result = await invoke({ fetchResponse: makeResponses[index] });
      assertSafeFailure(result);
      assert.equal(result.fetches.length, 1);
    });
  }
});

test('enforces the 64 KiB limit on actual UTF-8 response bytes', async () => {
  const prefix = '{"zot":{"TOKEN":"';
  const suffix = '"}}';
  const capacity = 64 * 1024 - encoder.encode(prefix + suffix).byteLength;
  const exact = prefix + 'a'.repeat(capacity) + suffix;
  assert.equal(encoder.encode(exact).byteLength, 64 * 1024);
  const accepted = await invoke({ fetchResponse: () => streamedResponse([exact.slice(0, 100), exact.slice(100)]) });
  assertSuccess(accepted);
  const oversized = await invoke({ fetchResponse: () => streamedResponse([prefix, 'a'.repeat(capacity), 'b', suffix]) });
  assertSafeFailure(oversized);
  const multibyte = await invoke({ fetchResponse: () => streamedResponse([prefix, '한'.repeat(22000), suffix]) });
  assertSafeFailure(multibyte);
});

test('HTTP failures other than 429 and 503 do not retry or expose the body', async (t) => {
  for (const status of [201, 302, 400, 401, 403, 404, 500, 502, 504]) {
    await t.test(`HTTP ${status}`, async () => {
      const result = await invoke({ fetchResponse: () => response({ error: { code: VALUE, requestId: JWT } }, { status }) });
      assertSafeFailure(result);
      assert.equal(result.fetches.length, 1);
      assert.equal(result.sleeps.length, 0);
    });
  }
});

test('redirect and network exceptions fail without retries or leaked exception text', async (t) => {
  for (const kind of ['redirect', 'network', 'timeout']) {
    await t.test(kind, async () => {
      const result = await invoke({ fetchResponse: async () => { throw new Error(`${kind} ${JWT} ${VALUE}`); } });
      assertSafeFailure(result);
      assert.equal(result.fetches.length, 1);
      assert.equal(result.sleeps.length, 0);
    });
  }
});

test('retries 429 and 503 at most three total requests', async (t) => {
  for (const status of [429, 503]) {
    await t.test(`HTTP ${status}`, async () => {
      const result = await invoke({ fetchResponse: () => response({ error: { code: VALUE } }, { status, headers: { 'retry-after': '1' } }) });
      assertSafeFailure(result);
      assert.equal(result.fetches.length, 3);
      assert.equal(result.sleeps.length, 2);
      for (const delay of result.sleeps) assert.ok(Number.isFinite(delay) && delay >= 0 && delay <= 5000, 'Retry delays must be bounded.');
    });
  }
});

test('recovers from transient 429 and 503 responses and exports only the final data', async () => {
  const result = await invoke({ fetchResponse: (attempt) => attempt < 3
    ? response({ error: { code: 'TEMPORARY' } }, { status: attempt === 1 ? 429 : 503, headers: { 'retry-after': '0' } })
    : response() });
  assertSuccess(result);
  assert.equal(result.fetches.length, 3);
  assert.equal(exportsOf(result).length, 1);
  assert.ok(result.env.TOKEN === VALUE);
});

test('honors seconds Retry-After within the five-second delay budget', async (t) => {
  for (const [retryAfter, expected] of [['2', 2000], ['5', 5000]]) {
    await t.test(`delay ${expected} ms`, async () => {
      const result = await invoke({ fetchResponse: (attempt) => attempt === 1
        ? response({}, { status: 429, headers: { 'retry-after': retryAfter } })
        : response() });
      assertSuccess(result);
      assert.deepEqual(result.sleeps, [expected]);
    });
  }
});

test('a Retry-After beyond the delay budget fails without retrying earlier than requested', async (t) => {
  for (const retryAfter of ['6', '99999', '999999999999999999999999999999999999999999999']) {
    await t.test('delay exceeds budget', async () => {
      const result = await invoke({ fetchResponse: () => response({}, { status: 429, headers: { 'retry-after': retryAfter } }) });
      assertSafeFailure(result);
      assert.equal(result.fetches.length, 1);
      assert.equal(result.sleeps.length, 0);
    });
  }
});

test('missing and invalid Retry-After values use bounded fallback delays', async (t) => {
  for (const retryAfter of [undefined, '-1', 'NaN']) {
    await t.test(retryAfter === undefined ? 'missing header' : 'untrusted header', async () => {
      const headers = retryAfter === undefined ? {} : { 'retry-after': retryAfter };
      const result = await invoke({ fetchResponse: (attempt) => attempt === 1 ? response({}, { status: 429, headers }) : response() });
      assertSuccess(result);
      assert.equal(result.sleeps.length, 1);
      assert.equal(result.sleeps[0], 1000, 'An unavailable valid Retry-After uses the first fallback delay.');
    });
  }
});
