import * as core from '@actions/core';
import {run} from './action.js';

// getIDToken has its own HTTP retries and cannot be cancelled by the caller.
// A process deadline also bounds that work, not just the SMS fetch requests.
const deadline = setTimeout(() => {
  core.setFailed('Loading CI secrets exceeded the 60-second Action time limit.');
  process.exit(1);
}, 60_000);

async function main() {
  try {
    if (!process.env.GITHUB_ENV) {
      core.setFailed('A GitHub Actions runner with GITHUB_ENV is required.');
      return;
    }
    await run({core});
  } finally {
    clearTimeout(deadline);
  }
}

main().catch(() => core.setFailed('Could not load and export CI secrets.'));
