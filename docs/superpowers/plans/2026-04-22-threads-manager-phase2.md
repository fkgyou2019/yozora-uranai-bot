# Threads Manager Phase 2 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement task-by-task.

**Goal:** OAuth フローで Threads アカウントを追加し、アクセストークンを管理する。トークン残日数バッジ・⋮メニュー・起動時自動リフレッシュを実装する。

**Architecture:** OAuth ロジックは全て Electron main プロセスで完結（Next.js API Routes 経由なし）。Renderer ↔ Main 間は IPC (contextBridge) で通信。

**前提条件（手動作業）:**
> Meta Developer Console (`https://developers.facebook.com/apps/`) で Threads App を作成し、
> **Redirect URI に `http://localhost:47823/callback` を追加する。**
> App ID と App Secret をメモしておく。HTTP localhost は開発用途で許可されている。

**作業ディレクトリ:** `C:\Users\fkgyo\OneDrive\デスクトップ\AI×占い自動運用システム開発`

---

## File Structure（新規作成・変更ファイル一覧）

| Path | 内容 |
|------|------|
| `apps/threads-manager/electron/oauth-server.ts` | 新規: port 47823 OAuth callback サーバー |
| `apps/threads-manager/electron/ipc-bridge.ts` | 新規: IPC ハンドラ登録 |
| `apps/threads-manager/electron/preload.ts` | 変更: window.threadsManager ブリッジ拡張 |
| `apps/threads-manager/electron/main.ts` | 変更: IPC 登録 + 起動時 token チェック |
| `apps/threads-manager/src/lib/types.ts` | 変更: AppCredentials, OAuthResult, TokenStatus 型追加 |
| `apps/threads-manager/src/lib/token-utils.ts` | 新規: token 残日数・バッジ計算 |
| `apps/threads-manager/src/lib/threads-api.ts` | 新規: Threads Graph API クライアント |
| `apps/threads-manager/src/lib/credentials.ts` | 新規: app-credentials.json R/W |
| `apps/threads-manager/src/lib/accounts.ts` | 変更: writeAccountToken, addThreadsAccount, disableAccount, deleteAccount 追加 |
| `apps/threads-manager/src/components/account-list.tsx` | 変更: バッジ・⋮メニュー・+ボタン追加 |
| `apps/threads-manager/src/components/modals/add-account-modal.tsx` | 新規: OAuth 追加ウィザード |
| `apps/threads-manager/src/app/page.tsx` | 変更: モーダル state + IPC コールバック |
| `apps/threads-manager/tests/token-utils.test.ts` | 新規: 単体テスト |
| `apps/threads-manager/tests/threads-api.test.ts` | 新規: API クライアント単体テスト (fetch モック) |
| `apps/threads-manager/tests/accounts.test.ts` | 変更: 新関数のテスト追加 |
| `state/threads-manager/.gitkeep` | 新規: ディレクトリ作成 |

---

## Task 0: Skeleton コミット

- [ ] **Step 1: state/threads-manager/ ディレクトリ作成**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
mkdir -p state/threads-manager
touch state/threads-manager/.gitkeep
```

- [ ] **Step 2: コミット**

```bash
git add state/threads-manager/.gitkeep docs/superpowers/plans/2026-04-22-threads-manager-phase2.md
git commit -m "chore(threads-manager): Phase 2 skeleton start"
```

---

## Task 1: 型定義・token-utils・threads-api の TDD 実装

**Files:**
- `apps/threads-manager/src/lib/types.ts` (変更)
- `apps/threads-manager/src/lib/token-utils.ts` (新規)
- `apps/threads-manager/src/lib/threads-api.ts` (新規)
- `apps/threads-manager/tests/token-utils.test.ts` (新規)
- `apps/threads-manager/tests/threads-api.test.ts` (新規)

### Step 1: types.ts に型を追加

`apps/threads-manager/src/lib/types.ts` の末尾に追記:

```typescript
export interface AppCredentials {
  app_id: string;
  app_secret: string;
}

export type TokenStatus = 'valid' | 'warning' | 'expired' | 'none';

export interface OAuthResult {
  status: 'success' | 'error' | 'cancelled';
  accountId?: string;
  username?: string;
  message?: string;
}
```

### Step 2: token-utils.test.ts を作成（先にテスト）

`apps/threads-manager/tests/token-utils.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { getTokenStatus, getTokenBadge, getTokenDaysRemaining, getTokenLabel } from '../src/lib/token-utils';

describe('token-utils', () => {
  const now = new Date('2026-04-22T12:00:00+09:00');

  it('returns "none" for empty token_expires_at', () => {
    expect(getTokenStatus('', now)).toBe('none');
    expect(getTokenStatus(undefined as any, now)).toBe('none');
  });

  it('returns "valid" for 30 days remaining', () => {
    const expires = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000).toISOString();
    expect(getTokenStatus(expires, now)).toBe('valid');
  });

  it('returns "warning" for 10 days remaining', () => {
    const expires = new Date(now.getTime() + 10 * 24 * 60 * 60 * 1000).toISOString();
    expect(getTokenStatus(expires, now)).toBe('warning');
  });

  it('returns "warning" for exactly 14 days remaining', () => {
    const expires = new Date(now.getTime() + 14 * 24 * 60 * 60 * 1000).toISOString();
    expect(getTokenStatus(expires, now)).toBe('warning');
  });

  it('returns "valid" for 15 days remaining', () => {
    const expires = new Date(now.getTime() + 15 * 24 * 60 * 60 * 1000).toISOString();
    expect(getTokenStatus(expires, now)).toBe('valid');
  });

  it('returns "expired" for past date', () => {
    const expires = new Date(now.getTime() - 1000).toISOString();
    expect(getTokenStatus(expires, now)).toBe('expired');
  });

  it('getTokenBadge returns correct emoji', () => {
    expect(getTokenBadge('valid')).toBe('🟢');
    expect(getTokenBadge('warning')).toBe('🟡');
    expect(getTokenBadge('expired')).toBe('🔴');
    expect(getTokenBadge('none')).toBe('⚪');
  });

  it('getTokenDaysRemaining returns correct days', () => {
    const expires = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000).toISOString();
    expect(getTokenDaysRemaining(expires, now)).toBe(30);
  });

  it('getTokenDaysRemaining returns 0 for expired', () => {
    const expires = new Date(now.getTime() - 1000).toISOString();
    expect(getTokenDaysRemaining(expires, now)).toBe(0);
  });

  it('getTokenLabel returns readable string', () => {
    const expires30 = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000).toISOString();
    expect(getTokenLabel(expires30, now)).toBe('残30日');
    const expiresNow = new Date(now.getTime() - 1000).toISOString();
    expect(getTokenLabel(expiresNow, now)).toBe('期限切れ');
    expect(getTokenLabel('', now)).toBe('未接続');
  });
});
```

### Step 3: token-utils.ts を実装

`apps/threads-manager/src/lib/token-utils.ts`:

```typescript
import type { TokenStatus } from './types';

const WARNING_THRESHOLD_DAYS = 14;

export function getTokenDaysRemaining(tokenExpiresAt: string, now: Date = new Date()): number {
  if (!tokenExpiresAt) return 0;
  const exp = new Date(tokenExpiresAt);
  const diff = exp.getTime() - now.getTime();
  return Math.max(0, Math.floor(diff / (1000 * 60 * 60 * 24)));
}

export function getTokenStatus(tokenExpiresAt: string, now: Date = new Date()): TokenStatus {
  if (!tokenExpiresAt) return 'none';
  const exp = new Date(tokenExpiresAt);
  if (exp <= now) return 'expired';
  const days = getTokenDaysRemaining(tokenExpiresAt, now);
  return days <= WARNING_THRESHOLD_DAYS ? 'warning' : 'valid';
}

export function getTokenBadge(status: TokenStatus): string {
  switch (status) {
    case 'valid':   return '🟢';
    case 'warning': return '🟡';
    case 'expired': return '🔴';
    case 'none':    return '⚪';
  }
}

export function getTokenLabel(tokenExpiresAt: string, now: Date = new Date()): string {
  if (!tokenExpiresAt) return '未接続';
  const exp = new Date(tokenExpiresAt);
  if (exp <= now) return '期限切れ';
  const days = getTokenDaysRemaining(tokenExpiresAt, now);
  return `残${days}日`;
}
```

### Step 4: threads-api.test.ts を作成

`apps/threads-manager/tests/threads-api.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  buildAuthUrl,
  exchangeCodeForShortToken,
  exchangeShortForLongToken,
  refreshLongToken,
  getThreadsMe,
} from '../src/lib/threads-api';

const mockFetch = vi.fn();
global.fetch = mockFetch;

describe('threads-api', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it('buildAuthUrl generates correct URL', () => {
    const url = buildAuthUrl('APP123', 'http://localhost:47823/callback', 'STATE456');
    expect(url).toContain('threads.net/oauth/authorize');
    expect(url).toContain('client_id=APP123');
    expect(url).toContain('state=STATE456');
    expect(url).toContain('threads_basic');
  });

  it('exchangeCodeForShortToken calls correct endpoint', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ access_token: 'SHORT_TOKEN', user_id: '12345' }),
    });
    const result = await exchangeCodeForShortToken({
      appId: 'APP123',
      appSecret: 'SECRET',
      redirectUri: 'http://localhost:47823/callback',
      code: 'CODE',
    });
    expect(result.access_token).toBe('SHORT_TOKEN');
    expect(mockFetch).toHaveBeenCalledWith(
      'https://graph.threads.net/oauth/access_token',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('exchangeShortForLongToken calls correct endpoint', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ access_token: 'LONG_TOKEN', expires_in: 5184000 }),
    });
    const result = await exchangeShortForLongToken('SECRET', 'SHORT_TOKEN');
    expect(result.access_token).toBe('LONG_TOKEN');
    expect(result.expires_in).toBe(5184000);
  });

  it('refreshLongToken calls correct endpoint', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ access_token: 'NEW_TOKEN', expires_in: 5184000 }),
    });
    const result = await refreshLongToken('OLD_TOKEN');
    expect(result.access_token).toBe('NEW_TOKEN');
  });

  it('getThreadsMe returns user info', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: '12345', username: 'testuser' }),
    });
    const result = await getThreadsMe('TOKEN123');
    expect(result.id).toBe('12345');
    expect(result.username).toBe('testuser');
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: { message: 'Bad request' } }),
    });
    await expect(getThreadsMe('BAD_TOKEN')).rejects.toThrow('Threads API error 400');
  });
});
```

### Step 5: threads-api.ts を実装

`apps/threads-manager/src/lib/threads-api.ts`:

```typescript
const THREADS_OAUTH_BASE = 'https://threads.net/oauth/authorize';
const THREADS_GRAPH_BASE = 'https://graph.threads.net';
const REDIRECT_URI = 'http://localhost:47823/callback';
const OAUTH_SCOPES = [
  'threads_basic',
  'threads_content_publish',
  'threads_manage_replies',
  'threads_read_replies',
  'threads_manage_insights',
].join(',');

export function buildAuthUrl(appId: string, redirectUri: string, state: string): string {
  const params = new URLSearchParams({
    client_id: appId,
    redirect_uri: redirectUri,
    scope: OAUTH_SCOPES,
    response_type: 'code',
    state,
  });
  return `${THREADS_OAUTH_BASE}?${params.toString()}`;
}

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  const json = await res.json();
  if (!res.ok) {
    const msg = json?.error?.message ?? JSON.stringify(json);
    throw new Error(`Threads API error ${res.status}: ${msg}`);
  }
  return json as T;
}

export interface ShortTokenResponse {
  access_token: string;
  user_id: string;
}

export async function exchangeCodeForShortToken(opts: {
  appId: string;
  appSecret: string;
  redirectUri: string;
  code: string;
}): Promise<ShortTokenResponse> {
  const body = new URLSearchParams({
    client_id: opts.appId,
    client_secret: opts.appSecret,
    redirect_uri: opts.redirectUri,
    code: opts.code,
    grant_type: 'authorization_code',
  });
  return apiFetch<ShortTokenResponse>(`${THREADS_GRAPH_BASE}/oauth/access_token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
}

export interface LongTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number; // seconds
}

export async function exchangeShortForLongToken(
  appSecret: string,
  shortToken: string
): Promise<LongTokenResponse> {
  const params = new URLSearchParams({
    grant_type: 'th_exchange_token',
    client_secret: appSecret,
    access_token: shortToken,
  });
  return apiFetch<LongTokenResponse>(`${THREADS_GRAPH_BASE}/access_token?${params.toString()}`);
}

export async function refreshLongToken(longToken: string): Promise<LongTokenResponse> {
  const params = new URLSearchParams({
    grant_type: 'th_refresh_token',
    access_token: longToken,
  });
  return apiFetch<LongTokenResponse>(
    `${THREADS_GRAPH_BASE}/refresh_access_token?${params.toString()}`
  );
}

export interface ThreadsMe {
  id: string;
  username: string;
}

export async function getThreadsMe(accessToken: string): Promise<ThreadsMe> {
  const params = new URLSearchParams({
    fields: 'id,username',
    access_token: accessToken,
  });
  return apiFetch<ThreadsMe>(`${THREADS_GRAPH_BASE}/me?${params.toString()}`);
}

export { REDIRECT_URI };
```

### Step 6: テスト実行（全 PASS 確認）

```bash
cd apps/threads-manager && npm test
```

Expected: 既存 9 + 新規 (token-utils: 9 + threads-api: 5) = 23 PASS

### Step 7: コミット

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/lib/types.ts apps/threads-manager/src/lib/token-utils.ts apps/threads-manager/src/lib/threads-api.ts apps/threads-manager/tests/token-utils.test.ts apps/threads-manager/tests/threads-api.test.ts
git commit -m "feat(threads-manager): add token-utils and Threads API client with tests"
```

---

## Task 2: credentials.ts + accounts.ts 拡張

**Files:**
- `apps/threads-manager/src/lib/credentials.ts` (新規)
- `apps/threads-manager/src/lib/accounts.ts` (変更)
- `apps/threads-manager/tests/accounts.test.ts` (変更: 新関数追加)

### Step 1: credentials.ts を作成

`apps/threads-manager/src/lib/credentials.ts`:

```typescript
import * as path from 'path';
import { existsSync } from 'fs';
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

  // credentials は存在しなくてもパスを返す（初回書き込み用）
  for (const p of candidates) {
    if (existsSync(p)) return p;
  }
  // デフォルト候補の 1 番目（INIT_CWD 優先、なければ cwd/../..）
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
  const mtimeMs = existsSync(p)
    ? (await readJsonWithLock<AppCredentials>(p)).mtimeMs
    : 0;
  await writeJsonWithLock(p, creds, { expectedMtimeMs: mtimeMs });
}
```

### Step 2: accounts.ts に新関数を追加

`apps/threads-manager/src/lib/accounts.ts` の末尾に以下を追加:

```typescript
import { writeJsonWithLock } from './file-lock';
import type { ThreadsAccount, AccountsFile } from './types';

export async function writeAccountToken(
  accountsPath: string,
  accountId: string,
  token: { access_token: string; user_id: string; token_expires_at: string }
): Promise<void> {
  const { data, mtimeMs } = await readAccountsFile(accountsPath);
  const idx = data.threads_accounts.findIndex((a) => a.id === accountId);
  if (idx === -1) throw new Error(`Account not found: ${accountId}`);
  data.threads_accounts[idx].auth = {
    ...data.threads_accounts[idx].auth,
    ...token,
  };
  await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });
}

export async function addThreadsAccount(
  accountsPath: string,
  account: ThreadsAccount
): Promise<void> {
  const { data, mtimeMs } = await readAccountsFile(accountsPath);
  const existing = data.threads_accounts.findIndex((a) => a.id === account.id);
  if (existing !== -1) {
    // 既存アカウントのトークンを更新
    data.threads_accounts[existing] = account;
  } else {
    data.threads_accounts.push(account);
  }
  await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });
}

export async function disableAccount(
  accountsPath: string,
  accountId: string
): Promise<void> {
  const { data, mtimeMs } = await readAccountsFile(accountsPath);
  const idx = data.threads_accounts.findIndex((a) => a.id === accountId);
  if (idx === -1) throw new Error(`Account not found: ${accountId}`);
  data.threads_accounts[idx].enabled = false;
  await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });
}

export async function deleteAccount(
  accountsPath: string,
  accountId: string
): Promise<void> {
  const { data, mtimeMs } = await readAccountsFile(accountsPath);
  data.threads_accounts = data.threads_accounts.filter((a) => a.id !== accountId);
  await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });
}
```

### Step 3: accounts.test.ts にテスト追加

`apps/threads-manager/tests/accounts.test.ts` の describe ブロック内に追記:

```typescript
  it('writeAccountToken updates auth fields', async () => {
    const data = {
      threads_accounts: [{
        id: 'acc_01', name: 'テスト', username: 'test', persona: '', group: '',
        enabled: true, auth: { user_id: '', access_token: '', token_expires_at: '' },
        otp_url: '', limits: { max_posts_per_day: 5, min_interval_seconds: 1200 },
      }],
      groups: [],
    };
    fs.writeFileSync(accountsPath, JSON.stringify(data));
    await writeAccountToken(accountsPath, 'acc_01', {
      access_token: 'NEW_TOKEN',
      user_id: '99999',
      token_expires_at: '2026-06-01T00:00:00Z',
    });
    const updated = JSON.parse(fs.readFileSync(accountsPath, 'utf-8'));
    expect(updated.threads_accounts[0].auth.access_token).toBe('NEW_TOKEN');
    expect(updated.threads_accounts[0].auth.user_id).toBe('99999');
  });

  it('addThreadsAccount appends new account', async () => {
    fs.writeFileSync(accountsPath, JSON.stringify({ threads_accounts: [], groups: [] }));
    const newAcc = {
      id: 'acc_02', name: '新規', username: 'newuser', persona: '', group: '',
      enabled: true, auth: { user_id: '123', access_token: 'TK', token_expires_at: '2026-06-01T00:00:00Z' },
      otp_url: '', limits: { max_posts_per_day: 5, min_interval_seconds: 1200 },
    };
    await addThreadsAccount(accountsPath, newAcc);
    const updated = JSON.parse(fs.readFileSync(accountsPath, 'utf-8'));
    expect(updated.threads_accounts).toHaveLength(1);
    expect(updated.threads_accounts[0].id).toBe('acc_02');
  });

  it('disableAccount sets enabled to false', async () => {
    const data = {
      threads_accounts: [{
        id: 'acc_03', name: 'X', username: 'x', persona: '', group: '',
        enabled: true, auth: { user_id: '', access_token: '', token_expires_at: '' },
        otp_url: '', limits: { max_posts_per_day: 5, min_interval_seconds: 1200 },
      }],
      groups: [],
    };
    fs.writeFileSync(accountsPath, JSON.stringify(data));
    await disableAccount(accountsPath, 'acc_03');
    const updated = JSON.parse(fs.readFileSync(accountsPath, 'utf-8'));
    expect(updated.threads_accounts[0].enabled).toBe(false);
  });

  it('deleteAccount removes account', async () => {
    const data = {
      threads_accounts: [
        { id: 'acc_04', name: 'A', username: 'a', persona: '', group: '',
          enabled: true, auth: { user_id: '', access_token: '', token_expires_at: '' },
          otp_url: '', limits: { max_posts_per_day: 5, min_interval_seconds: 1200 } },
        { id: 'acc_05', name: 'B', username: 'b', persona: '', group: '',
          enabled: true, auth: { user_id: '', access_token: '', token_expires_at: '' },
          otp_url: '', limits: { max_posts_per_day: 5, min_interval_seconds: 1200 } },
      ],
      groups: [],
    };
    fs.writeFileSync(accountsPath, JSON.stringify(data));
    await deleteAccount(accountsPath, 'acc_04');
    const updated = JSON.parse(fs.readFileSync(accountsPath, 'utf-8'));
    expect(updated.threads_accounts).toHaveLength(1);
    expect(updated.threads_accounts[0].id).toBe('acc_05');
  });
```

accounts.test.ts の import を更新（追加インポート）:

```typescript
import { getThreadsAccounts, getGroups, writeAccountToken, addThreadsAccount, disableAccount, deleteAccount } from '../src/lib/accounts';
```

### Step 4: テスト実行

```bash
cd apps/threads-manager && npm test
```

Expected: 23 + 4 (新規) = 27 PASS

### Step 5: コミット

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/lib/credentials.ts apps/threads-manager/src/lib/accounts.ts apps/threads-manager/tests/accounts.test.ts
git commit -m "feat(threads-manager): add credentials store and account CRUD functions"
```

---

## Task 3: Electron OAuth サーバー実装

**Files:**
- `apps/threads-manager/electron/oauth-server.ts` (新規)

`apps/threads-manager/electron/oauth-server.ts`:

```typescript
import * as http from 'http';
import * as crypto from 'crypto';

interface PendingState {
  resolve: (code: string) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

const TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes
const PORT = 47823;

const pendingStates = new Map<string, PendingState>();
let server: http.Server | null = null;

export function generateState(): string {
  return crypto.randomBytes(32).toString('hex');
}

export function startOAuthServer(): Promise<void> {
  if (server?.listening) return Promise.resolve();

  return new Promise((resolve, reject) => {
    server = http.createServer((req, res) => {
      if (!req.url?.startsWith('/callback')) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }

      const url = new URL(req.url, `http://localhost:${PORT}`);
      const code = url.searchParams.get('code');
      const state = url.searchParams.get('state');
      const error = url.searchParams.get('error');

      if (!state || !pendingStates.has(state)) {
        res.writeHead(400, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end('<h1>無効なリクエスト</h1><p>認証セッションが見つかりません。</p>');
        return;
      }

      const pending = pendingStates.get(state)!;
      clearTimeout(pending.timer);
      pendingStates.delete(state);

      if (error || !code) {
        res.writeHead(400, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(`<h1>認証エラー</h1><p>${error ?? 'code が受信できませんでした'}</p>`);
        pending.reject(new Error(error ?? 'No code received'));
        return;
      }

      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`
        <!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
        <title>Threads Manager</title>
        <style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#0b1020;color:#e7e9f4;}</style>
        </head><body>
        <div style="text-align:center">
          <h1 style="color:#7c3aed">✅ 認証完了</h1>
          <p>このウィンドウを閉じて Threads Manager に戻ってください。</p>
        </div>
        </body></html>
      `);
      pending.resolve(code);
    });

    server.listen(PORT, '127.0.0.1', () => {
      console.log(`[oauth-server] Listening on http://127.0.0.1:${PORT}`);
      resolve();
    });

    server.on('error', (err: NodeJS.ErrnoException) => {
      if (err.code === 'EADDRINUSE') {
        reject(new Error(`ポート ${PORT} は使用中です。他のアプリを終了してから再起動してください。`));
      } else {
        reject(err);
      }
    });
  });
}

export function stopOAuthServer(): Promise<void> {
  return new Promise((resolve) => {
    if (!server) { resolve(); return; }
    // cancel all pending states
    for (const [, pending] of pendingStates) {
      clearTimeout(pending.timer);
      pending.reject(new Error('Server stopped'));
    }
    pendingStates.clear();
    server.close(() => { server = null; resolve(); });
  });
}

/** state を登録し、コールバック受信まで待つ Promise を返す */
export function waitForCallback(state: string): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingStates.delete(state);
      reject(new Error('OAuth タイムアウト（5分）。再試行してください。'));
    }, TIMEOUT_MS);

    pendingStates.set(state, { resolve, reject, timer });
  });
}

export function cancelState(state: string): void {
  const pending = pendingStates.get(state);
  if (pending) {
    clearTimeout(pending.timer);
    pendingStates.delete(state);
    pending.reject(new Error('cancelled'));
  }
}
```

コミット:

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/electron/oauth-server.ts
git commit -m "feat(threads-manager): add OAuth callback server on port 47823"
```

---

## Task 4: IPC ブリッジ実装 + preload + main 更新

**Files:**
- `apps/threads-manager/electron/ipc-bridge.ts` (新規)
- `apps/threads-manager/electron/preload.ts` (変更)
- `apps/threads-manager/electron/main.ts` (変更)

### Step 1: ipc-bridge.ts を作成

`apps/threads-manager/electron/ipc-bridge.ts`:

```typescript
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

// ── パス解決ユーティリティ ─────────────────────────────────────

function resolveProjectPath(...parts: string[]): string {
  // INIT_CWD > cwd > cwd/../.. > cwd/../
  const candidates = [
    process.env.INIT_CWD ? path.resolve(process.env.INIT_CWD, ...parts) : null,
    path.resolve(process.cwd(), ...parts),
    path.resolve(process.cwd(), '..', '..', ...parts),
    path.resolve(process.cwd(), '..', ...parts),
  ].filter(Boolean) as string[];

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  // 書き込み用: 存在しなくても返す（最初の候補）
  return candidates[0];
}

function getAccountsPath() { return resolveProjectPath('config', 'accounts.json'); }
function getCredentialsPath() { return resolveProjectPath('state', 'threads-manager', 'app-credentials.json'); }

// ── IPC ハンドラ登録 ──────────────────────────────────────────

let currentOAuthState: string | null = null;

export function registerIpcHandlers(): void {

  // credentials:get
  ipcMain.handle('credentials:get', async () => {
    const p = getCredentialsPath();
    if (!fs.existsSync(p)) return null;
    const { data } = await readJsonWithLock<AppCredentials>(p);
    return (data.app_id && data.app_secret) ? data : null;
  });

  // credentials:save
  ipcMain.handle('credentials:save', async (_event, creds: AppCredentials) => {
    const p = getCredentialsPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const mtimeMs = fs.existsSync(p) ? (await readJsonWithLock<any>(p)).mtimeMs : 0;
    await writeJsonWithLock(p, creds, { expectedMtimeMs: mtimeMs });
  });

  // oauth:start
  ipcMain.handle('oauth:start', async (_event, opts: { accountName: string; appId: string; appSecret: string }) => {
    await startOAuthServer();

    const state = generateState();
    currentOAuthState = state;

    const authUrl = buildAuthUrl(opts.appId, REDIRECT_URI, state);
    await shell.openExternal(authUrl);

    try {
      const code = await waitForCallback(state);
      currentOAuthState = null;

      // code → short-lived token
      const shortToken = await exchangeCodeForShortToken({
        appId: opts.appId,
        appSecret: opts.appSecret,
        redirectUri: REDIRECT_URI,
        code,
      });

      // short-lived → long-lived token (60 days)
      const longToken = await exchangeShortForLongToken(opts.appSecret, shortToken.access_token);

      // /me でユーザー情報取得
      const me = await getThreadsMe(longToken.access_token);

      // token_expires_at 計算（現在時刻 + expires_in秒）
      const expiresAt = new Date(Date.now() + longToken.expires_in * 1000).toISOString();

      // accounts.json に保存
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

      // Renderer に通知
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

  // oauth:cancel
  ipcMain.handle('oauth:cancel', () => {
    if (currentOAuthState) {
      cancelState(currentOAuthState);
      currentOAuthState = null;
    }
  });

  // token:refresh
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
```

### Step 2: preload.ts を更新

`apps/threads-manager/electron/preload.ts`:

```typescript
import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('threadsManager', {
  version: '0.1.0',

  credentials: {
    get: () => ipcRenderer.invoke('credentials:get'),
    save: (creds: { app_id: string; app_secret: string }) =>
      ipcRenderer.invoke('credentials:save', creds),
  },

  oauth: {
    start: (opts: { accountName: string; appId: string; appSecret: string }) =>
      ipcRenderer.invoke('oauth:start', opts),
    cancel: () => ipcRenderer.invoke('oauth:cancel'),
    onResult: (cb: (result: any) => void) => {
      const handler = (_event: any, result: any) => cb(result);
      ipcRenderer.on('oauth:complete', handler);
      return () => ipcRenderer.removeListener('oauth:complete', handler);
    },
  },

  token: {
    refresh: (accountId: string) => ipcRenderer.invoke('token:refresh', accountId),
  },
});
```

### Step 3: main.ts を更新

`apps/threads-manager/electron/main.ts` を以下に書き換え:

```typescript
import { app, BrowserWindow, shell } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { registerIpcHandlers } from './ipc-bridge';

const NEXT_DEV_URL = 'http://localhost:3050';

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#0b1020',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.loadURL(NEXT_DEV_URL).catch((err) => {
    console.error('Failed to load URL:', err);
  });

  if (process.env.OPEN_DEVTOOLS === '1') {
    mainWindow.webContents.openDevTools();
  }
}

/** 起動時に token 残 7 日以下のアカウントを自動リフレッシュ */
async function autoRefreshExpiringSoonTokens(): Promise<void> {
  try {
    const candidates = [
      process.env.INIT_CWD
        ? path.resolve(process.env.INIT_CWD, 'config', 'accounts.json')
        : null,
      path.resolve(process.cwd(), 'config', 'accounts.json'),
      path.resolve(process.cwd(), '..', '..', 'config', 'accounts.json'),
    ].filter(Boolean) as string[];

    const accountsPath = candidates.find((p) => fs.existsSync(p));
    if (!accountsPath) return;

    const raw = fs.readFileSync(accountsPath, 'utf-8');
    const data = JSON.parse(raw);
    const accounts = (data.threads_accounts ?? []) as Array<{
      id: string;
      auth?: { access_token?: string; token_expires_at?: string };
    }>;

    const SEVEN_DAYS = 7 * 24 * 60 * 60 * 1000;
    const needsRefresh = accounts.filter((a) => {
      if (!a.auth?.access_token || !a.auth.token_expires_at) return false;
      const exp = new Date(a.auth.token_expires_at).getTime();
      return exp - Date.now() < SEVEN_DAYS;
    });

    if (needsRefresh.length === 0) {
      console.log('[startup] All tokens are healthy.');
      return;
    }

    console.log(`[startup] Auto-refreshing ${needsRefresh.length} expiring token(s)...`);

    // 並列最大 3
    const { refreshLongToken } = await import('./oauth-server').then(() =>
      import('../src/lib/threads-api')
    );
    const { readJsonWithLock, writeJsonWithLock } = await import('../src/lib/file-lock');

    const chunks: typeof needsRefresh[] = [];
    for (let i = 0; i < needsRefresh.length; i += 3) chunks.push(needsRefresh.slice(i, i + 3));

    for (const chunk of chunks) {
      await Promise.all(
        chunk.map(async (a) => {
          try {
            const refreshed = await refreshLongToken(a.auth!.access_token!);
            const expiresAt = new Date(Date.now() + refreshed.expires_in * 1000).toISOString();
            const { data: d, mtimeMs } = await readJsonWithLock<typeof data>(accountsPath);
            const idx = d.threads_accounts.findIndex((acc: any) => acc.id === a.id);
            if (idx !== -1) {
              d.threads_accounts[idx].auth.access_token = refreshed.access_token;
              d.threads_accounts[idx].auth.token_expires_at = expiresAt;
              await writeJsonWithLock(accountsPath, d, { expectedMtimeMs: mtimeMs });
              console.log(`[startup] Refreshed token for ${a.id}`);
            }
          } catch (err: any) {
            console.error(`[startup] Failed to refresh token for ${a.id}: ${err.message}`);
          }
        })
      );
    }
  } catch (err: any) {
    console.error('[startup] autoRefreshExpiringSoonTokens error:', err.message);
  }
}

app.whenReady().then(async () => {
  registerIpcHandlers();
  await autoRefreshExpiringSoonTokens();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
```

### Step 4: Electron ビルド確認

```bash
cd apps/threads-manager && npm run build:electron
```

Expected: `dist-electron/main.js`, `dist-electron/preload.js`, `dist-electron/ipc-bridge.js` 生成。エラー無し。

### Step 5: コミット

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/electron/ipc-bridge.ts apps/threads-manager/electron/preload.ts apps/threads-manager/electron/main.ts
git commit -m "feat(threads-manager): add IPC bridge for OAuth, credentials, token refresh"
```

---

## Task 5: Add Account モーダル UI

**Files:**
- `apps/threads-manager/src/components/modals/add-account-modal.tsx` (新規)

`apps/threads-manager/src/components/modals/add-account-modal.tsx`:

```tsx
'use client';

import { useState, useEffect } from 'react';

type Step = 'credentials' | 'name' | 'waiting' | 'success' | 'error';

interface Props {
  onClose: () => void;
  onSuccess: () => void;
}

declare global {
  interface Window {
    threadsManager?: {
      version: string;
      credentials: {
        get(): Promise<{ app_id: string; app_secret: string } | null>;
        save(creds: { app_id: string; app_secret: string }): Promise<void>;
      };
      oauth: {
        start(opts: { accountName: string; appId: string; appSecret: string }): Promise<any>;
        cancel(): Promise<void>;
        onResult(cb: (result: any) => void): () => void;
      };
      token: { refresh(id: string): Promise<void> };
    };
  }
}

export default function AddAccountModal({ onClose, onSuccess }: Props) {
  const [step, setStep] = useState<Step>('credentials');
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [accountName, setAccountName] = useState('');
  const [countdown, setCountdown] = useState(300); // 5min
  const [errorMsg, setErrorMsg] = useState('');
  const [successUsername, setSuccessUsername] = useState('');

  // App credentials を事前読み込み
  useEffect(() => {
    window.threadsManager?.credentials.get().then((creds) => {
      if (creds) {
        setAppId(creds.app_id);
        setAppSecret(creds.app_secret);
        setStep('name');
      }
    });
  }, []);

  // 待機中カウントダウン
  useEffect(() => {
    if (step !== 'waiting') return;
    if (countdown <= 0) { setErrorMsg('タイムアウトしました。再試行してください。'); setStep('error'); return; }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [step, countdown]);

  async function handleSaveCredentials() {
    if (!appId.trim() || !appSecret.trim()) return;
    await window.threadsManager?.credentials.save({ app_id: appId.trim(), app_secret: appSecret.trim() });
    setStep('name');
  }

  async function handleStartOAuth() {
    if (!accountName.trim()) return;
    setStep('waiting');
    setCountdown(300);

    const result = await window.threadsManager?.oauth.start({
      accountName: accountName.trim(),
      appId,
      appSecret,
    });

    if (result?.status === 'success') {
      setSuccessUsername(result.username ?? '');
      setStep('success');
    } else if (result?.status === 'cancelled') {
      onClose();
    } else {
      setErrorMsg(result?.message ?? '不明なエラー');
      setStep('error');
    }
  }

  async function handleCancel() {
    await window.threadsManager?.oauth.cancel();
    onClose();
  }

  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50,
  };
  const modal: React.CSSProperties = {
    background: 'var(--bg-card)', border: '1px solid var(--border)',
    borderRadius: 12, padding: 32, width: 440, maxWidth: '90vw',
  };
  const input: React.CSSProperties = {
    width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)',
    background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14,
    boxSizing: 'border-box',
  };
  const btnPrimary: React.CSSProperties = {
    padding: '10px 20px', borderRadius: 8, border: 'none', cursor: 'pointer',
    background: 'var(--accent)', color: 'white', fontWeight: 600, fontSize: 14,
  };
  const btnSecondary: React.CSSProperties = {
    ...btnPrimary, background: 'transparent',
    border: '1px solid var(--border)', color: 'var(--text-secondary)',
  };

  return (
    <div style={overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modal}>
        <h2 style={{ marginTop: 0, fontSize: 18 }}>アカウント追加</h2>

        {/* Step: credentials */}
        {step === 'credentials' && (
          <div>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 16 }}>
              Meta Developer Console で作成した Threads App の認証情報を入力してください。
            </p>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>App ID</label>
            <input style={{ ...input, marginBottom: 12, marginTop: 4 }} value={appId}
              onChange={(e) => setAppId(e.target.value)} placeholder="123456789" />
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>App Secret</label>
            <input style={{ ...input, marginBottom: 20, marginTop: 4 }} type="password"
              value={appSecret} onChange={(e) => setAppSecret(e.target.value)} placeholder="••••••••" />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button style={btnSecondary} onClick={onClose}>キャンセル</button>
              <button style={btnPrimary} onClick={handleSaveCredentials}
                disabled={!appId || !appSecret}>次へ →</button>
            </div>
          </div>
        )}

        {/* Step: name */}
        {step === 'name' && (
          <div>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 16 }}>
              アカウントの表示名を入力し、Threads で認証してください。
            </p>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>表示名</label>
            <input style={{ ...input, marginBottom: 20, marginTop: 4 }} value={accountName}
              onChange={(e) => setAccountName(e.target.value)} placeholder="例: 月詞メイン" />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button style={btnSecondary} onClick={() => setStep('credentials')}>← 戻る</button>
              <button style={btnPrimary} onClick={handleStartOAuth}
                disabled={!accountName.trim()}>Threads で認証 →</button>
            </div>
          </div>
        )}

        {/* Step: waiting */}
        {step === 'waiting' && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>🔄</div>
            <p style={{ fontSize: 15, fontWeight: 600 }}>ブラウザで認証中...</p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
              開いたブラウザで Threads アカウントを認証してください。
            </p>
            <p style={{ fontSize: 24, fontWeight: 700, color: 'var(--accent)', margin: '20px 0' }}>
              {Math.floor(countdown / 60)}:{String(countdown % 60).padStart(2, '0')}
            </p>
            <button style={btnSecondary} onClick={handleCancel}>キャンセル</button>
          </div>
        )}

        {/* Step: success */}
        {step === 'success' && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>✅</div>
            <p style={{ fontSize: 15, fontWeight: 600 }}>追加完了！</p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 20 }}>
              @{successUsername} を追加しました。
            </p>
            <button style={btnPrimary} onClick={() => { onSuccess(); onClose(); }}>閉じる</button>
          </div>
        )}

        {/* Step: error */}
        {step === 'error' && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>❌</div>
            <p style={{ fontSize: 15, fontWeight: 600, color: '#f87171' }}>認証失敗</p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 20 }}>{errorMsg}</p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button style={btnSecondary} onClick={onClose}>閉じる</button>
              <button style={btnPrimary} onClick={() => setStep('name')}>再試行</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

コミット:

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/components/modals/
git commit -m "feat(threads-manager): add OAuth add-account modal"
```

---

## Task 6: account-list.tsx 更新 + page.tsx 更新

**Files:**
- `apps/threads-manager/src/components/account-list.tsx` (変更)
- `apps/threads-manager/src/app/page.tsx` (変更)

### Step 1: account-list.tsx を更新

`apps/threads-manager/src/components/account-list.tsx`:

```tsx
'use client';

import { useEffect, useState, useCallback } from 'react';
import type { ThreadsAccount } from '@/lib/types';
import { getTokenStatus, getTokenBadge, getTokenLabel } from '@/lib/token-utils';

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAddClick: () => void;
  refreshKey: number;
}

export default function AccountList({ selectedId, onSelect, onAddClick, refreshKey }: Props) {
  const [accounts, setAccounts] = useState<ThreadsAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);

  const fetchAccounts = useCallback(() => {
    setLoading(true);
    fetch('/api/accounts')
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setAccounts(data.accounts ?? []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchAccounts(); }, [fetchAccounts, refreshKey]);

  async function handleRefreshToken(accountId: string) {
    setMenuOpenId(null);
    try {
      await window.threadsManager?.token.refresh(accountId);
      fetchAccounts();
    } catch (e: any) {
      alert(`Token更新失敗: ${e.message}`);
    }
  }

  async function handleDisable(accountId: string) {
    setMenuOpenId(null);
    if (!confirm('このアカウントを無効化しますか？')) return;
    await fetch('/api/accounts', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: accountId, enabled: false }),
    });
    fetchAccounts();
  }

  return (
    <div
      className="w-72 h-full border-r flex flex-col"
      style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-sm font-semibold">アカウント</h2>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading && <p className="text-xs p-3" style={{ color: 'var(--text-secondary)' }}>読み込み中...</p>}
        {error && <p className="text-xs p-3 text-red-400">エラー: {error}</p>}
        {!loading && !error && accounts.length === 0 && (
          <p className="text-xs p-3" style={{ color: 'var(--text-secondary)' }}>
            アカウントがありません
          </p>
        )}

        {accounts.map((a) => {
          const status = getTokenStatus(a.auth?.token_expires_at ?? '');
          const badge = getTokenBadge(status);
          const label = getTokenLabel(a.auth?.token_expires_at ?? '');

          return (
            <div key={a.id} className="relative">
              <button
                onClick={() => onSelect(a.id)}
                className="w-full flex items-center gap-3 p-2 rounded-lg text-left transition-colors"
                style={{
                  background: selectedId === a.id ? 'var(--bg-card)' : 'transparent',
                  border: selectedId === a.id ? '1px solid var(--accent)' : '1px solid transparent',
                }}
              >
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                  style={{ background: 'var(--accent)', color: 'white' }}
                >
                  {a.name.charAt(0)}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate">{a.name}</p>
                  <p className="text-[10px] truncate" style={{ color: 'var(--text-secondary)' }}>
                    @{a.username}
                  </p>
                  <p className="text-[10px]" style={{
                    color: status === 'expired' ? '#f87171' : status === 'warning' ? '#fbbf24' : 'var(--text-secondary)'
                  }}>
                    {badge} {label}
                  </p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); setMenuOpenId(menuOpenId === a.id ? null : a.id); }}
                  className="text-xs px-1 rounded"
                  style={{ color: 'var(--text-secondary)' }}
                >⋮</button>
              </button>

              {/* ⋮ ドロップダウンメニュー */}
              {menuOpenId === a.id && (
                <div
                  className="absolute right-2 top-10 z-10 rounded-lg shadow-lg text-xs py-1"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', minWidth: 140 }}
                >
                  <button
                    className="w-full text-left px-4 py-2 hover:opacity-80"
                    onClick={() => handleRefreshToken(a.id)}
                  >🔄 Token 更新</button>
                  <button
                    className="w-full text-left px-4 py-2 hover:opacity-80"
                    onClick={() => handleDisable(a.id)}
                    style={{ color: '#fbbf24' }}
                  >⏸ 無効化</button>
                  <div style={{ borderTop: '1px solid var(--border)', margin: '4px 0' }} />
                  <button
                    className="w-full text-left px-4 py-2 hover:opacity-80"
                    onClick={() => { setMenuOpenId(null); alert('編集機能は近日実装予定です'); }}
                    style={{ color: 'var(--text-secondary)' }}
                  >✏️ 編集</button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* + アカウント追加ボタン */}
      <div className="p-3 border-t" style={{ borderColor: 'var(--border)' }}>
        <button
          onClick={onAddClick}
          className="w-full py-2 rounded-lg text-sm font-medium"
          style={{ background: 'var(--accent)', color: 'white' }}
        >
          ＋ アカウント追加
        </button>
      </div>
    </div>
  );
}
```

### Step 2: page.tsx を更新

`apps/threads-manager/src/app/page.tsx`:

```tsx
'use client';

import { useState, useEffect } from 'react';
import Sidebar from '@/components/sidebar';
import AccountList from '@/components/account-list';
import MainArea from '@/components/main-area';
import AddAccountModal from '@/components/modals/add-account-modal';

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Electron IPC から oauth:complete イベントを受信
  useEffect(() => {
    const unsubscribe = window.threadsManager?.oauth.onResult(() => {
      setRefreshKey((k) => k + 1);
    });
    return () => unsubscribe?.();
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar />
      <AccountList
        selectedId={selectedId}
        onSelect={setSelectedId}
        onAddClick={() => setShowAddModal(true)}
        refreshKey={refreshKey}
      />
      <MainArea selectedId={selectedId} />

      {showAddModal && (
        <AddAccountModal
          onClose={() => setShowAddModal(false)}
          onSuccess={() => setRefreshKey((k) => k + 1)}
        />
      )}
    </div>
  );
}
```

### Step 3: TypeScript チェック

```bash
cd apps/threads-manager && npx tsc --noEmit
```

Expected: エラー無し（または軽微な型警告のみ）

### Step 4: Electron ビルド + テスト確認

```bash
npm run build:electron && npm test
```

### Step 5: コミット

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/components/account-list.tsx apps/threads-manager/src/app/page.tsx
git commit -m "feat(threads-manager): update account-list with badges, menu, + button; wire up OAuth modal"
```

---

## Task 7: PATCH /api/accounts エンドポイント追加

`apps/threads-manager/src/app/api/accounts/route.ts` に PATCH を追加:

```typescript
// 既存の GET の後に追加
export async function PATCH(request: Request) {
  try {
    const accountsPath = resolveAccountsPath();
    const body = await request.json();
    const { id, enabled } = body as { id: string; enabled: boolean };

    const { data, mtimeMs } = await readJsonWithLock<any>(accountsPath);
    const idx = data.threads_accounts?.findIndex((a: any) => a.id === id);
    if (idx === -1 || idx === undefined) {
      return NextResponse.json({ error: 'Account not found' }, { status: 404 });
    }
    data.threads_accounts[idx].enabled = enabled;
    await writeJsonWithLock(accountsPath, data, { expectedMtimeMs: mtimeMs });
    return NextResponse.json({ ok: true });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
```

`route.ts` の import に `readJsonWithLock, writeJsonWithLock` を追加:

```typescript
import { getThreadsAccounts, readAccountsFile } from '@/lib/accounts';
import { readJsonWithLock, writeJsonWithLock } from '@/lib/file-lock';
```

コミット:

```bash
git add apps/threads-manager/src/app/api/accounts/route.ts
git commit -m "feat(threads-manager): add PATCH /api/accounts for enable/disable"
```

---

## Task 8: 動作確認 + Phase 2 完了コミット

- [ ] **Step 1: 全テスト確認**

```bash
cd apps/threads-manager && npm test
```

Expected: 27+ テスト PASS

- [ ] **Step 2: `npm run dev` で起動確認**

確認項目:
- [ ] 「＋ アカウント追加」ボタンが表示される
- [ ] クリックするとモーダルが開く
- [ ] App ID / Secret 入力 → 次へ
- [ ] アカウント名入力 → 「Threads で認証 →」クリック
- [ ] 外部ブラウザが開く（実際の認証は Meta App 登録後に実施）
- [ ] 5分タイムアウトカウントダウンが動作
- [ ] キャンセルボタンでモーダルが閉じる
- [ ] 既存アカウントの ⋮ メニューが開く

- [ ] **Step 3: Phase 2 完了コミット**

```bash
git commit --allow-empty -m "chore(threads-manager): Phase 2 complete

Phase 2 completion checklist:
- [x] token-utils.ts: getTokenStatus/Badge/DaysRemaining/Label
- [x] threads-api.ts: buildAuthUrl, exchangeCode, exchangeShortForLong, refresh, /me
- [x] credentials.ts: app-credentials.json R/W
- [x] accounts.ts: writeAccountToken, addThreadsAccount, disableAccount, deleteAccount
- [x] electron/oauth-server.ts: port 47823 HTTP server, 5min timeout
- [x] electron/ipc-bridge.ts: credentials/oauth/token handlers
- [x] electron/preload.ts: window.threadsManager IPC bridge
- [x] electron/main.ts: registerIpcHandlers + autoRefreshExpiringSoonTokens
- [x] account-list.tsx: badge, label, kebab menu, + button
- [x] add-account-modal.tsx: OAuth wizard (4 steps)
- [x] page.tsx: modal state + IPC event listener
- [x] PATCH /api/accounts: disable/enable

Next: Phase 3 (投稿タブ)
See: docs/superpowers/specs/2026-04-22-threads-manager-design.md §11"
```

---

## トラブルシューティング

### ポート 47823 が使用中
```
netstat -ano | findstr 47823
```
PID 確認後タスクマネージャーで終了。

### `window.threadsManager` が undefined
Electron ウィンドウ外（ブラウザで直接 localhost:3050 を開いた場合）は undefined になる。正常動作。
`npm run dev` で起動した Electron ウィンドウ内では必ず定義される。

### OAuth コールバックが届かない
1. Meta Developer Console の Redirect URI が `http://localhost:47823/callback`（HTTP）か確認
2. `OPEN_DEVTOOLS=1 npm run dev` で DevTools Console のログを確認
3. `[oauth-server] Listening on http://127.0.0.1:47823` が出ているか確認

### TypeScript エラー: electron モジュールが src/lib 内で見つからない
`src/lib/threads-api.ts` 等は Next.js (tsconfig.json) でコンパイルされる。Electron 固有の API (`ipcMain` 等) は `electron/` ディレクトリのみで使用すること。
