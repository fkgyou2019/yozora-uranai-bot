import { NextResponse } from 'next/server';
import * as path from 'path';
import { existsSync } from 'fs';
import { getThreadsAccounts } from '@/lib/accounts';
import { readJsonWithLock, writeJsonWithLock } from '@/lib/file-lock';

/**
 * Resolve the path to config/accounts.json in a cwd-independent way.
 * process.cwd() differs depending on where npm run dev is invoked from,
 * so we search upward from multiple candidate roots and fail fast if not found.
 */
function resolveAccountsPath(): string {
  const candidates = [
    process.env.INIT_CWD ? path.resolve(process.env.INIT_CWD, 'config', 'accounts.json') : null,
    path.resolve(process.cwd(), 'config', 'accounts.json'),
    path.resolve(process.cwd(), '..', '..', 'config', 'accounts.json'),
    path.resolve(process.cwd(), '..', 'config', 'accounts.json'),
  ].filter(Boolean) as string[];

  for (const p of candidates) {
    if (existsSync(p)) return p;
  }

  throw new Error(
    `Could not locate config/accounts.json. Searched:\n` +
      candidates.map((c) => `  - ${c}`).join('\n') +
      `\nprocess.cwd() = ${process.cwd()}\n` +
      `INIT_CWD = ${process.env.INIT_CWD ?? '(unset)'}`
  );
}

export async function GET() {
  try {
    const accountsPath = resolveAccountsPath();
    const accounts = await getThreadsAccounts(accountsPath);
    return NextResponse.json({ accounts });
  } catch (error: any) {
    console.error('[api/accounts] error:', error);
    return NextResponse.json(
      { error: error.message ?? 'Failed to read accounts' },
      { status: 500 }
    );
  }
}

export async function PATCH(request: Request) {
  try {
    const accountsPath = resolveAccountsPath();
    const body = await request.json();
    const { id, enabled } = body as { id: string; enabled: boolean };

    const { data, mtimeMs } = await readJsonWithLock<any>(accountsPath);
    const idx = (data.threads_accounts ?? []).findIndex((a: any) => a.id === id);
    if (idx === -1) {
      return NextResponse.json({ error: 'Account not found' }, { status: 404 });
    }
    data.threads_accounts[idx].enabled = enabled;
    await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });
    return NextResponse.json({ ok: true });
  } catch (error: any) {
    console.error('[api/accounts PATCH] error:', error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
