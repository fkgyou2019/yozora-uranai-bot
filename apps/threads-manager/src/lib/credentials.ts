import * as path from 'path';
import { existsSync } from 'fs';
import { mkdirSync } from 'fs';
import { readJsonWithLock, writeJsonWithLock } from './file-lock';
import type { AppCredentials } from './types';

function resolveCredentialsPath(): string {
  const candidates = [
    process.env.INIT_CWD
      ? path.resolve(process.env.INIT_CWD, 'state', 'threads-manager', 'app-credentials.json')
      : null,
    path.resolve(process.cwd(), 'state', 'threads-manager', 'app-credentials.json'),
    path.resolve(process.cwd(), '..', '..', 'state', 'threads-manager', 'app-credentials.json'),
    path.resolve(process.cwd(), '..', 'state', 'threads-manager', 'app-credentials.json'),
  ].filter(Boolean) as string[];

  for (const p of candidates) {
    if (existsSync(p)) return p;
  }
  return candidates.find((p) => p.includes('state')) ?? candidates[0];
}

export async function getCredentials(): Promise<AppCredentials | null> {
  const p = resolveCredentialsPath();
  if (!existsSync(p)) return null;
  const { data } = await readJsonWithLock<AppCredentials>(p);
  if (!data.app_id || !data.app_secret) return null;
  return data;
}

export async function saveCredentials(creds: AppCredentials): Promise<void> {
  const p = resolveCredentialsPath();
  mkdirSync(path.dirname(p), { recursive: true });
  const mtimeMs = existsSync(p)
    ? (await readJsonWithLock<AppCredentials>(p)).mtimeMs
    : 0;
  await writeJsonWithLock(p, creds, { expectedMtimeMs: mtimeMs });
}
