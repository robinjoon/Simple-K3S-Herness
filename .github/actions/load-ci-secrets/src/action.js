'use strict';

const {setTimeout: sleep} = require('node:timers/promises');

const API_URL = 'https://secrets.homelab.robinjoon.xyz';
const AUDIENCE = 'urn:homelab:ci-secrets:v1';
const MAX_BYTES = 64 * 1024;
const REQUEST_TIMEOUT_MS = 10_000;
const MAX_ATTEMPTS = 3;
const MAX_RETRY_DELAY_MS = 5_000;
const APP_NAME = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const RESERVED_NAME = /^(?:PATH|BASH_ENV|ENV|NODE_OPTIONS)$|^(?:GITHUB_|RUNNER_|ACTIONS_|INPUT_)/i;

class ActionError extends Error {}

// Only the two object levels and string values in the SMS contract are accepted.
// Parsing string tokens individually preserves escaped-key duplicate detection.
function parseResponse(text, app) {
  let offset = 0;
  const stringToken = /"(?:[^"\\\u0000-\u001f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/y;
  const invalid = () => {
    throw new ActionError('SMS returned an invalid secret response.');
  };
  const whitespace = () => {
    while (offset < text.length && /[ \t\r\n]/.test(text[offset])) offset++;
  };
  const expect = (character) => {
    whitespace();
    if (text[offset++] !== character) invalid();
  };
  const readString = () => {
    whitespace();
    stringToken.lastIndex = offset;
    const token = stringToken.exec(text);
    if (!token) invalid();
    offset = stringToken.lastIndex;
    return JSON.parse(token[0]);
  };

  expect('{');
  if (readString() !== app) invalid();
  expect(':');
  expect('{');
  const entries = [];
  const names = new Set();
  do {
    const name = readString();
    if (!ENV_NAME.test(name) || RESERVED_NAME.test(name) || names.has(name)) invalid();
    names.add(name);
    expect(':');
    const value = readString();
    if (!value || value.includes('\0') || !value.isWellFormed()) invalid();
    entries.push([name, value]);
    whitespace();
    if (text[offset] !== ',') break;
    offset++;
  } while (true);
  expect('}');
  expect('}');
  whitespace();
  if (offset !== text.length) invalid();
  return entries;
}

async function readBody(response) {
  const contentType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
  if (contentType !== 'application/json' || !response.body) {
    throw new ActionError('SMS did not return a JSON response.');
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) throw new ActionError('SMS response exceeds 64 KiB.');
      chunks.push(value);
    }
    // Fatal decoding rejects damaged UTF-8 instead of silently changing a credential.
    return new TextDecoder('utf-8', {fatal: true, ignoreBOM: true})
      .decode(Buffer.concat(chunks, size));
  } finally {
    reader.releaseLock();
  }
}

function retryDelay(response, attempt) {
  const header = response.headers.get('retry-after');
  if (header !== null && /^\d+$/.test(header.trim())) {
    const delay = Number(header) * 1000;
    // Do not retry earlier than the server asks just to fit our time budget.
    if (delay > MAX_RETRY_DELAY_MS) {
      throw new ActionError('SMS requested a retry delay beyond the Action time limit.');
    }
    return delay;
  }
  return 1000 * attempt;
}

async function loadSecrets(app, token, fetch, wait) {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let response;
    let delay;
    try {
      response = await fetch(`${API_URL}/v1/ci/secrets/${app}`, {
        method: 'GET',
        headers: {Accept: 'application/json', Authorization: `Bearer ${token}`},
        redirect: 'error',
        cache: 'no-store',
        signal: controller.signal,
      });
      if (response.status === 200) {
        return parseResponse(await readBody(response), app);
      }
      if (![429, 503].includes(response.status) || attempt === MAX_ATTEMPTS) {
        throw new ActionError(`SMS request failed (HTTP ${response.status}).`);
      }
      delay = retryDelay(response, attempt);
    } catch (error) {
      if (error instanceof ActionError) throw error;
      throw new ActionError('SMS request failed or timed out. Check HTTPS connectivity and the service.');
    } finally {
      // Covers header waits, body streaming, and bodies discarded on any error/retry.
      controller.abort();
      clearTimeout(timeout);
      if (response?.body && !response.body.locked) {
        await response.body.cancel().catch(() => {});
      }
    }
    await wait(delay);
  }
}

function checkConflicts(entries, env) {
  // Reject case aliases too, so the same Action cannot silently overwrite values
  // when moved between runners with different environment-name casing rules.
  const existing = new Map();
  for (const [name, value] of Object.entries(env)) {
    if (value !== undefined) {
      const folded = name.toUpperCase();
      const aliases = existing.get(folded) || [];
      aliases.push([name, value]);
      existing.set(folded, aliases);
    }
  }
  for (const [name, value] of entries) {
    const folded = name.toUpperCase();
    if (existing.get(folded)?.some(([oldName, oldValue]) => oldName !== name || oldValue !== value)) {
      throw new ActionError('A secret conflicts with an existing environment variable.');
    }
    existing.set(folded, [[name, value]]);
  }
}

async function run({core, fetch = globalThis.fetch, env = process.env, sleep: wait = sleep}) {
  try {
    let app;
    try {
      app = core.getInput('app', {required: true});
    } catch {
      throw new ActionError('The app input is required.');
    }
    if (typeof app !== 'string' || !APP_NAME.test(app)) {
      throw new ActionError('The app input must be a valid app name of 1–63 characters.');
    }
    let token;
    try {
      token = await core.getIDToken(AUDIENCE);
      if (typeof token !== 'string' || !token) throw new Error();
      core.setSecret(token);
    } catch {
      throw new ActionError('Cannot obtain a GitHub OIDC token. Check job permissions: id-token: write.');
    }
    const entries = await loadSecrets(app, token, fetch, wait);
    checkConflicts(entries, env);
    for (const [, value] of entries) core.setSecret(value);
    // Export even an identical inherited value: it may have been scoped to this
    // Action step, and must also be available to subsequent steps in the job.
    for (const [name, value] of entries) core.exportVariable(name, value);
  } catch (error) {
    // Toolkit, fetch, JSON and file-command errors can contain credentials.
    core.setFailed(error instanceof ActionError ? error.message : 'Could not load and export CI secrets.');
  }
}

module.exports = {run};
