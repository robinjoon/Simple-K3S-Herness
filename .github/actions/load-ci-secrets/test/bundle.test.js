'use strict';

const assert = require('node:assert/strict');
const {execFile} = require('node:child_process');
const {createServer} = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {promisify} = require('node:util');
const {test} = require('node:test');

const execute = promisify(execFile);
const TOKEN = 'fixture-oidc-token-never-log';
const REQUEST_TOKEN = 'fixture-request-token-never-log';
const SECRET = 'fixture-credential-never-log';

// Exercise the real bundled toolkit and file commands. Only SMS fetch is replaced;
// GitHub OIDC is a local dummy server, and no inherited credentials reach the child.
async function executeBundle(t, {mode = 'success', body, inherited = {}, missingEnv = false} = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'load-ci-secrets-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  await fs.copyFile(path.join(__dirname, '../dist/index.js'), path.join(directory, 'index.js'));
  const requests = [];
  const server = createServer((request, response) => {
    requests.push({url: request.url, authorization: request.headers.authorization});
    if (mode === 'oidc-timeout') return;
    response.writeHead(mode === 'oidc-error' ? 401 : 200, {'content-type': 'application/json'});
    response.end(JSON.stringify(mode === 'oidc-error' ? {message: SECRET} : {value: TOKEN}));
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  const environmentFile = path.join(directory, 'env');
  const outputFile = path.join(directory, 'output');
  const callFile = path.join(directory, 'sms-called');
  const fixtureFile = path.join(directory, 'fixture.json');
  await Promise.all([
    fs.writeFile(environmentFile, ''),
    fs.writeFile(outputFile, ''),
    fs.writeFile(fixtureFile, JSON.stringify({mode, body, token: TOKEN, callFile})),
    fs.writeFile(path.join(directory, 'preload.cjs'), `
      const fs = require('node:fs');
      const fixture = JSON.parse(fs.readFileSync(process.env.FIXTURE_FILE, 'utf8'));
      const realSetTimeout = globalThis.setTimeout;
      globalThis.setTimeout = (callback, delay, ...args) => realSetTimeout(
        callback,
        delay === 60000 ? (fixture.mode === 'oidc-timeout' ? 200 : 3000) : delay === 10000 ? 50 : delay,
        ...args
      );
      globalThis.fetch = async (url, options) => {
        if (url !== 'https://secrets.homelab.robinjoon.xyz/v1/ci/secrets/zot' ||
            options.method !== 'GET' || options.redirect !== 'error' ||
            options.headers.Authorization !== 'Bearer ' + fixture.token) {
          throw new Error('Unexpected SMS request in bundle fixture');
        }
        fs.writeFileSync(fixture.callFile, 'called');
        if (fixture.mode === 'header-timeout') {
          return new Promise((resolve, reject) => options.signal.addEventListener('abort',
            () => reject(new Error('fixture request timed out'))));
        }
        if (fixture.mode === 'body-timeout') {
          return new Response(new ReadableStream({start(controller) {
            options.signal.addEventListener('abort', () => controller.error(new Error('fixture body timed out')));
          }}), {headers: {'content-type': 'application/json'}});
        }
        return new Response(fixture.body, {headers: {'content-type': 'application/json'}});
      };
    `),
  ]);

  const env = {
    INPUT_APP: 'zot',
    ACTIONS_ID_TOKEN_REQUEST_TOKEN: REQUEST_TOKEN,
    ACTIONS_ID_TOKEN_REQUEST_URL: `http://127.0.0.1:${server.address().port}/oidc?fixture=true`,
    GITHUB_ENV: environmentFile,
    GITHUB_OUTPUT: outputFile,
    FIXTURE_FILE: fixtureFile,
    ...inherited,
  };
  if (missingEnv) delete env.GITHUB_ENV;
  let result;
  try {
    result = {...await execute(process.execPath, ['--require', './preload.cjs', './index.js'], {
      cwd: directory, env, timeout: 8000, maxBuffer: 256 * 1024,
    }), code: 0};
  } catch (error) {
    assert.equal(error.killed, false, 'The Action must exit without the test killing it');
    result = {stdout: error.stdout, stderr: error.stderr, code: error.code};
  }
  // add-mask is a runner control command, not a human-visible log line.
  const visibleLogs = result.stdout.replace(/^::add-mask::.*(?:\r?\n|$)/gm, '') + result.stderr;
  for (const value of [TOKEN, REQUEST_TOKEN, SECRET]) {
    assert.equal(visibleLogs.includes(value), false, 'Credentials must not appear outside masking commands');
  }
  assert.equal(await fs.readFile(outputFile, 'utf8'), '', 'Secrets must not be Action outputs');
  return {
    ...result,
    environment: await fs.readFile(environmentFile, 'utf8'),
    calledSMS: await fs.access(callFile).then(() => true, () => false),
    requests,
  };
}

function readEnvironmentFile(text) {
  const values = new Map();
  let offset = 0;
  while (offset < text.length) {
    const header = /^([^\r\n]+)<<([^\r\n]+)\r?\n/.exec(text.slice(offset));
    assert.ok(header, 'Expected the toolkit heredoc file-command format');
    offset += header[0].length;
    const endMarker = `${os.EOL}${header[2]}${os.EOL}`;
    const end = text.indexOf(endMarker, offset);
    assert.notEqual(end, -1);
    values.set(header[1], text.slice(offset, end));
    offset = end + endMarker.length;
  }
  return values;
}

test('standalone bundle includes toolkit and preserves values through the real GITHUB_ENV file', async (t) => {
  const multiline = `${SECRET}\r\n한글 🐈\n"quoted" % value\n$(never-run)\n::error::not-a-command\n`;
  const body = JSON.stringify({zot: {REGISTRY_PASSWORD: multiline, REGISTRY_USERNAME: 'fixture-user'}});
  const result = await executeBundle(t, {body, inherited: {REGISTRY_USERNAME: 'fixture-user'}});
  assert.equal(result.code, 0);
  assert.equal(result.calledSMS, true);
  assert.equal(result.requests.length, 1);
  assert.equal(result.requests[0].authorization, `Bearer ${REQUEST_TOKEN}`);
  assert.equal(new URL(result.requests[0].url, 'http://localhost').searchParams.get('audience'), 'urn:homelab:ci-secrets:v1');
  const values = readEnvironmentFile(result.environment);
  assert.equal(values.get('REGISTRY_PASSWORD'), multiline);
  assert.equal(values.get('REGISTRY_USERNAME'), 'fixture-user', 'An identical step-scoped value must reach later steps too');
  assert.ok(result.stdout.includes('::add-mask::'));
});

test('bundle rejects a late invalid key without writing any GITHUB_ENV data', async (t) => {
  const body = JSON.stringify({zot: {REGISTRY_PASSWORD: SECRET, GITHUB_ENV: 'should-not-be-used'}});
  const result = await executeBundle(t, {body});
  assert.equal(result.code, 1);
  assert.equal(result.environment, '');
});

test('bundle sanitizes real toolkit OIDC failures and never calls SMS', async (t) => {
  const result = await executeBundle(t, {mode: 'oidc-error'});
  assert.equal(result.code, 1);
  assert.equal(result.calledSMS, false);
  assert.equal(result.environment, '');
});

test('bundle refuses legacy set-env fallback outside the file-command runner environment', async (t) => {
  const result = await executeBundle(t, {missingEnv: true});
  assert.equal(result.code, 1);
  assert.equal(result.requests.length, 0);
  assert.equal(result.calledSMS, false);
  assert.equal(result.stdout.includes('::set-env'), false);
});

for (const mode of ['header-timeout', 'body-timeout', 'oidc-timeout']) {
  test(`bundle enforces the ${mode} deadline without exporting secrets`, async (t) => {
    const result = await executeBundle(t, {mode});
    assert.equal(result.code, 1);
    assert.equal(result.environment, '');
    const expected = mode === 'oidc-timeout'
      ? '60-second Action time limit'
      : 'SMS request failed or timed out';
    assert.ok(result.stdout.includes(expected), 'The specific deadline must fail, not a later fallback');
  });
}
