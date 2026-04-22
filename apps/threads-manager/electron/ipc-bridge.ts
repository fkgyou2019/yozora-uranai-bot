import { ipcMain, shell, BrowserWindow } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import {
  startOAuthServer,
  generateState,
  waitForCallback,
  cancelState,
} from './oauth-server';
import {
  buildAuthUrl,
  exchangeCodeForShortToken,
  exchangeShortForLongToken,
  refreshLongToken,
  getThreadsMe,
  REDIRECT_URI,
} from '../src/lib/threads-api';
import { readJsonWithLock, writeJsonWithLock } from '../src/lib/file-lock';
import type { AppCredentials, ThreadsAccount, AccountsFile } from '../src/lib/types';

function resolveProjectPath(...parts: string[]): string {
  const candidates = [
    process.env.INIT_CWD ? path.resolve(process.env.INIT_CWD, ...parts) : null,
    path.resolve(process.cwd(), ...parts),
    path.resolve(process.cwd(), '..', '..', ...parts),
    path.resolve(process.cwd(), '..', ...parts),
  ].filter(Boolean) as string[];

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return candidates[0];
}

function getAccountsPath() { return resolveProjectPath('config', 'accounts.json'); }
function getCredentialsPath() { return resolveProjectPath('state', 'threads-manager', 'app-credentials.json'); }

let currentOAuthState: string | null = null;

export function registerIpcHandlers(): void {
  ipcMain.handle('credentials:get', async () => {
    const p = getCredentialsPath();
    if (!fs.existsSync(p)) return null;
    const { data } = await readJsonWithLock<AppCredentials>(p);
    return (data.app_id && data.app_secret) ? data : null;
  });

  ipcMain.handle('credentials:save', async (_event, creds: AppCredentials) => {
    const p = getCredentialsPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const mtimeMs = fs.existsSync(p) ? (await readJsonWithLock<any>(p)).mtimeMs : 0;
    await writeJsonWithLock(p, creds, { expectedMtimeMs: mtimeMs });
  });

  ipcMain.handle('oauth:start', async (_event, opts: { accountName: string; appId: string; appSecret: string }) => {
    await startOAuthServer();

    const state = generateState();
    currentOAuthState = state;

    const authUrl = buildAuthUrl(opts.appId, REDIRECT_URI, state);
    await shell.openExternal(authUrl);

    try {
      const code = await waitForCallback(state);
      currentOAuthState = null;

      const shortToken = await exchangeCodeForShortToken({
        appId: opts.appId,
        appSecret: opts.appSecret,
        redirectUri: REDIRECT_URI,
        code,
      });

      const longToken = await exchangeShortForLongToken(opts.appSecret, shortToken.access_token);
      const me = await getThreadsMe(longToken.access_token);
      const expiresAt = new Date(Date.now() + longToken.expires_in * 1000).toISOString();

      const accountsPath = getAccountsPath();
      const { data, mtimeMs } = await readJsonWithLock<AccountsFile>(accountsPath, {
        defaultValue: { threads_accounts: [], groups: [] },
      });

      const accountId = `threads_${me.username}_${Date.now()}`;
      const newAccount: ThreadsAccount = {
        id: accountId,
        name: opts.accountName,
        username: me.username,
        persona: '',
        group: '',
        enabled: true,
        auth: {
          user_id: me.id,
          access_token: longToken.access_token,
          token_expires_at: expiresAt,
        },
        otp_url: '',
        limits: { max_posts_per_day: 5, min_interval_seconds: 1200 },
      };

      const existingIdx = data.threads_accounts.findIndex((a) => a.auth?.user_id === me.id);
      if (existingIdx !== -1) {
        data.threads_accounts[existingIdx] = newAccount;
      } else {
        data.threads_accounts.push(newAccount);
      }
      await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });

      const win = BrowserWindow.getAllWindows()[0];
      win?.webContents.send('oauth:complete', { status: 'success', accountId, username: me.username });
      return { status: 'success', accountId, username: me.username };
    } catch (err: any) {
      currentOAuthState = null;
      const result = { status: err.message === 'cancelled' ? 'cancelled' : 'error', message: err.message };
      const win = BrowserWindow.getAllWindows()[0];
      win?.webContents.send('oauth:complete', result);
      return result;
    }
  });

  ipcMain.handle('oauth:cancel', () => {
    if (currentOAuthState) {
      cancelState(currentOAuthState);
      currentOAuthState = null;
    }
  });

  ipcMain.handle('token:refresh', async (_event, accountId: string) => {
    const accountsPath = getAccountsPath();
    const { data, mtimeMs } = await readJsonWithLock<AccountsFile>(accountsPath);
    const account = data.threads_accounts.find((a) => a.id === accountId);
    if (!account) throw new Error(`Account not found: ${accountId}`);
    if (!account.auth?.access_token) throw new Error('No access token to refresh');

    const refreshed = await refreshLongToken(account.auth.access_token);
    const expiresAt = new Date(Date.now() + refreshed.expires_in * 1000).toISOString();
    account.auth.access_token = refreshed.access_token;
    account.auth.token_expires_at = expiresAt;

    await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });
    return { access_token: refreshed.access_token, token_expires_at: expiresAt };
  });
}
