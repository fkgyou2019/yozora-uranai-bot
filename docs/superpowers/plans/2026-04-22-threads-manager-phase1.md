# Threads Manager Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Electron + Next.js 14 の土台を構築し、`config/accounts.json` の既存 threads_accounts を 3 カラムレイアウトで表示できる状態にする。mtime 楽観ロック付きのファイル R/W ユーティリティも実装する。

**Architecture:** Electron main process が Next.js dev server (localhost:3050) を子プロセスで起動し、BrowserWindow に表示。Renderer は API Routes を通じて `config/accounts.json` を読み書き。ファイル操作は全て mtime 楽観ロック経由。

**Tech Stack:** Electron 32.x, Next.js 14 (App Router), TypeScript 5.x, Tailwind CSS 3.x, Vitest (unit tests), husky (pre-commit hook)

**設計書:** [docs/superpowers/specs/2026-04-22-threads-manager-design.md](../specs/2026-04-22-threads-manager-design.md) §11 Phase 1 参照

**重要な前提:**
- 作業ディレクトリ: `C:\Users\fkgyo\OneDrive\デスクトップ\AI×占い自動運用システム開発`
- プロジェクトは git リポジトリ（branch: master）
- 前セッションの教訓: **未コミットで消失**した。**Task 1 終了時点で必ずコミット**してから先に進む
- OneDrive パス特有の問題として、書き込み直後の `rename` が一時的に失敗する可能性あり。対処は file-lock 実装時に考慮

**必須環境:**
- **Node.js: 20.x 以上**（Electron 32 の要件）
- **npm: 10.x 以上**（Node 20 に同梱されるバージョンでOK）
- **Git Bash**（Windows 上での bash コマンド実行用）

Run: `node --version && npm --version` で確認。Node 20 未満なら作業を中断して Node をアップグレードすること。

---

## File Structure

作成・変更するファイル一覧（パスは全て相対パス）:

### 新規作成
| Path | 責務 |
|------|------|
| `apps/threads-manager/package.json` | npm scripts, 依存関係 |
| `apps/threads-manager/tsconfig.json` | Next.js 用 TS 設定 |
| `apps/threads-manager/tsconfig.electron.json` | Electron main 用 TS 設定 (CommonJS) |
| `apps/threads-manager/next.config.js` | Next.js 設定（outputなし、dev server運用） |
| `apps/threads-manager/tailwind.config.ts` | Tailwind 設定 |
| `apps/threads-manager/postcss.config.js` | PostCSS 設定 |
| `apps/threads-manager/next-env.d.ts` | Next.js 型定義（初回起動時に自動生成、stub を事前作成） |
| `apps/threads-manager/electron/main.ts` | Electron main プロセス（Window + Next.js 起動） |
| `apps/threads-manager/electron/preload.ts` | 空の preload（contextBridge の器だけ） |
| `apps/threads-manager/src/app/layout.tsx` | ルートレイアウト |
| `apps/threads-manager/src/app/page.tsx` | 3カラムシェル |
| `apps/threads-manager/src/app/globals.css` | Tailwind + CSS 変数 |
| `apps/threads-manager/src/app/api/accounts/route.ts` | GET /api/accounts |
| `apps/threads-manager/src/components/sidebar.tsx` | 左サイドバー（空でOK） |
| `apps/threads-manager/src/components/account-list.tsx` | 中央カラム（アカウント表示） |
| `apps/threads-manager/src/components/main-area.tsx` | 右メインエリア（空でOK） |
| `apps/threads-manager/src/lib/types.ts` | ThreadsAccount 型定義 |
| `apps/threads-manager/src/lib/file-lock.ts` | mtime 楽観ロック R/W ユーティリティ |
| `apps/threads-manager/src/lib/accounts.ts` | accounts.json R/W |
| `apps/threads-manager/tests/file-lock.test.ts` | file-lock 単体テスト |
| `apps/threads-manager/tests/accounts.test.ts` | accounts 単体テスト |
| `apps/threads-manager/vitest.config.ts` | Vitest 設定 |
| `apps/threads-manager/README.md` | 起動手順（`npm run dev`） |

### 既存ファイル変更
| Path | 変更内容 |
|------|---------|
| `.gitignore` (ルート) | `apps/threads-manager/node_modules/`, `state/threads-manager/app-credentials.json`, `state/threads-manager/logs/` 追加 |
| `.husky/pre-commit` (新規) | app-credentials.json コミット阻止 |
| `package.json` (ルート or 新規) | husky 有効化 |

**注意:** ルートに `package.json` が無い場合は husky のセットアップ戦略を調整する。Task 4 で判断する。

---

## Task 0: Skeleton コミット（前セッション消失の再発防止）

**重要:** 何か作業を始める前に、まず空の apps/threads-manager/ ディレクトリとこのプランファイルをコミットする。

**Files:**
- Create: `apps/threads-manager/.gitkeep`

- [ ] **Step 1: ディレクトリ作成と .gitkeep 配置**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
mkdir -p apps/threads-manager
touch apps/threads-manager/.gitkeep
```

- [ ] **Step 2: 現在のステータス確認**

Run: `git status`
Expected: `apps/threads-manager/` が untracked として表示、`docs/superpowers/plans/...` も untracked

- [ ] **Step 3: skeleton コミット**

```bash
git add apps/threads-manager/.gitkeep docs/superpowers/plans/2026-04-22-threads-manager-phase1.md
git commit -m "chore: Threads Manager Phase 1 skeleton start

Phase 1実装開始のskeletonコミット。前セッションのように未コミットで消失するのを防ぐため、
ディレクトリとプランを先行コミットする。"
```

- [ ] **Step 4: ログ確認**

Run: `git log --oneline -3`
Expected: 先頭に `chore: Threads Manager Phase 1 skeleton start` が表示

---

## Task 1: package.json 初期化

**Files:**
- Create: `apps/threads-manager/package.json`

- [ ] **Step 1: package.json を作成**

ファイル内容（正確に）:

```json
{
  "name": "threads-manager",
  "version": "0.1.0",
  "private": true,
  "main": "dist-electron/main.js",
  "scripts": {
    "dev": "concurrently -k -n NEXT,ELECTRON -c cyan,green \"npm:dev:next\" \"npm:dev:electron\"",
    "dev:next": "next dev -p 3050",
    "dev:electron": "npm run build:electron && wait-on http://localhost:3050 && electron .",
    "build:electron": "tsc -p tsconfig.electron.json",
    "lint": "next lint",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "next": "^14.2.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "autoprefixer": "^10.4.0",
    "concurrently": "^8.2.0",
    "electron": "^32.0.0",
    "eslint": "^8.57.0",
    "eslint-config-next": "^14.2.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.4.0",
    "vitest": "^1.6.0",
    "wait-on": "^7.2.0"
  }
}
```

- [ ] **Step 2: npm install 実行**

```bash
cd apps/threads-manager && npm install
```

Expected: `npm install` が成功する（警告は許容、エラー無し）。所要時間 30〜90秒。
失敗する場合: ネットワーク or タイムアウト。`npm install --no-audit --no-fund` で再試行。

- [ ] **Step 3: node_modules が .gitignore されるか確認（ルート）**

Run: `cat "../../.gitignore" | grep -c "node_modules"` （ルートの .gitignore に node_modules 指定があるか）
もし無ければ、Task 4 で追加する（このタスクでは push しない）。

- [ ] **Step 4: コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/package.json apps/threads-manager/package-lock.json
git commit -m "feat(threads-manager): initialize package.json with Electron + Next.js deps"
```

---

## Task 2: TypeScript 設定

**Files:**
- Create: `apps/threads-manager/tsconfig.json`
- Create: `apps/threads-manager/tsconfig.electron.json`

- [ ] **Step 1: Next.js 用 tsconfig.json を作成**

`apps/threads-manager/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "ES2022"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules", "electron", "dist-electron"]
}
```

- [ ] **Step 2: Electron main 用 tsconfig.electron.json を作成**

`apps/threads-manager/tsconfig.electron.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "CommonJS",
    "moduleResolution": "Node",
    "outDir": "./dist-electron",
    "rootDir": "./electron",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true
  },
  "include": ["electron/**/*"]
}
```

- [ ] **Step 3: next-env.d.ts の stub を作成**

Next.js は初回 `next dev` 実行時に自動生成するが、それ以前に `tsc --noEmit` を走らせるとファイル不在エラーになる。stub を先行作成:

`apps/threads-manager/next-env.d.ts`:

```typescript
/// <reference types="next" />
/// <reference types="next/image-types/global" />

// NOTE: This file should not be edited
// see https://nextjs.org/docs/basic-features/typescript for more information.
```

- [ ] **Step 4: 型チェック動作確認**

```bash
cd apps/threads-manager && npx tsc --noEmit -p tsconfig.json
```

Expected: エラー無しで終了（include 対象ファイルがまだ無いため即完了）

- [ ] **Step 5: コミット**

```bash
git add apps/threads-manager/tsconfig.json apps/threads-manager/tsconfig.electron.json apps/threads-manager/next-env.d.ts
git commit -m "feat(threads-manager): add TypeScript configs for Next.js and Electron"
```

---

## Task 3: Next.js + Tailwind 設定

**Files:**
- Create: `apps/threads-manager/next.config.js`
- Create: `apps/threads-manager/tailwind.config.ts`
- Create: `apps/threads-manager/postcss.config.js`

- [ ] **Step 1: next.config.js を作成**

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};

module.exports = nextConfig;
```

- [ ] **Step 2: tailwind.config.ts を作成**

```typescript
import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'bg-primary': 'var(--bg-primary)',
        'bg-secondary': 'var(--bg-secondary)',
        'bg-card': 'var(--bg-card)',
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        accent: 'var(--accent)',
        border: 'var(--border)',
      },
    },
  },
  plugins: [],
};

export default config;
```

- [ ] **Step 3: postcss.config.js を作成**

```javascript
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 4: コミット**

```bash
git add apps/threads-manager/next.config.js apps/threads-manager/tailwind.config.ts apps/threads-manager/postcss.config.js
git commit -m "feat(threads-manager): add Next.js and Tailwind configs"
```

---

## Task 4: .gitignore 更新 + husky 導入

**Files:**
- Modify: `.gitignore` (ルート)
- Create: `.husky/pre-commit` (ルート)
- Modify or Create: ルートの `package.json`

- [ ] **Step 1: ルート .gitignore に追記**

既存の `.gitignore` ファイルの末尾に以下を追加（既に同内容があれば重複は避ける）:

```gitignore

# Threads Manager
apps/threads-manager/node_modules/
apps/threads-manager/.next/
apps/threads-manager/dist-electron/
apps/threads-manager/out/

# Threads Manager sensitive data
state/threads-manager/app-credentials.json
state/threads-manager/logs/
```

Run: `cat .gitignore | tail -20` して追記内容が反映されているか確認。

- [ ] **Step 2: ルートに package.json があるか確認**

Run: `ls package.json 2>/dev/null && echo exists || echo missing`

- Case A (exists): Task 4 Step 3 で husky を既存 package.json に追加
- Case B (missing): husky はプロジェクトルートで動かす必要があるため、最小の package.json を新規作成

- [ ] **Step 3a (Case B のみ): 最小 package.json を作成**

ルートに以下の `package.json` を作成（husky v9 では `"prepare": "husky"` が正しい）:

```json
{
  "name": "ai-uranai-root",
  "version": "1.0.0",
  "private": true,
  "description": "Monorepo root for AI占い自動運用システム (Threads Manager含む)",
  "scripts": {
    "prepare": "husky"
  },
  "devDependencies": {
    "husky": "^9.0.0"
  }
}
```

- [ ] **Step 3b: husky インストール（v9 形式）**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
npm install --save-dev husky@9
npx husky init
```

Expected: `.husky/` ディレクトリと `.husky/pre-commit`（サンプル内容）が生成される。`package.json` に `"prepare": "husky"` が自動追加される。

- [ ] **Step 4: pre-commit hook を上書き（v9 形式、sourcing 行は不要）**

```bash
cat > .husky/pre-commit <<'EOF'
# Prevent accidental commit of app-credentials.json
if git diff --cached --name-only | grep -qE "state/threads-manager/app-credentials\.json$"; then
  echo "❌ ERROR: state/threads-manager/app-credentials.json must not be committed."
  echo "   This file contains the Threads App Secret."
  echo "   If committed accidentally, ROTATE the App Secret in Meta Developer Console."
  exit 1
fi

# Prevent commit of log files
if git diff --cached --name-only | grep -qE "state/threads-manager/logs/"; then
  echo "❌ ERROR: state/threads-manager/logs/ must not be committed."
  exit 1
fi

exit 0
EOF
```

注: husky v9 では `.husky/_/husky.sh` のサーシングは不要。shebang 行も不要。hook は通常の shell script として実行される。

- [ ] **Step 5: pre-commit hook 動作確認（危険物を混ぜて試行）**

```bash
mkdir -p state/threads-manager
echo '{"app_id":"test","app_secret":"SENSITIVE"}' > state/threads-manager/app-credentials.json
git add -f state/threads-manager/app-credentials.json 2>&1 || true
git commit -m "test: should fail" 2>&1 | head -5
# このコマンドは失敗すべき
git reset HEAD state/threads-manager/app-credentials.json
rm state/threads-manager/app-credentials.json
```

Expected: `❌ ERROR:` メッセージが表示され、コミットが阻止される。

- [ ] **Step 6: 正常コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add .gitignore .husky/pre-commit package.json package-lock.json 2>/dev/null
git commit -m "chore: add husky + pre-commit hook + .gitignore for app-credentials"
```

---

## Task 5: file-lock.ts の TDD 実装

**目的:** mtime 楽観ロックでファイル R/W。書き込み直前に mtime 検証、不一致なら例外。

**Files:**
- Create: `apps/threads-manager/src/lib/file-lock.ts`
- Create: `apps/threads-manager/tests/file-lock.test.ts`
- Create: `apps/threads-manager/vitest.config.ts`

- [ ] **Step 1: vitest.config.ts を作成**

```typescript
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
```

- [ ] **Step 2: 失敗するテストを書く**

`apps/threads-manager/tests/file-lock.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { readJsonWithLock, writeJsonWithLock, LockConflictError } from '../src/lib/file-lock';

describe('file-lock', () => {
  let tmpDir: string;
  let testFile: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'threads-manager-test-'));
    testFile = path.join(tmpDir, 'test.json');
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it('readJsonWithLock returns data and mtime for existing file', async () => {
    fs.writeFileSync(testFile, JSON.stringify({ hello: 'world' }));
    const result = await readJsonWithLock<{ hello: string }>(testFile);
    expect(result.data.hello).toBe('world');
    expect(result.mtimeMs).toBeGreaterThan(0);
  });

  it('readJsonWithLock returns null-like for missing file', async () => {
    const result = await readJsonWithLock<any>(path.join(tmpDir, 'nope.json'), { defaultValue: { items: [] } });
    expect(result.data.items).toEqual([]);
    expect(result.mtimeMs).toBe(0);
  });

  it('writeJsonWithLock writes data atomically when mtime matches', async () => {
    fs.writeFileSync(testFile, JSON.stringify({ v: 1 }));
    const { mtimeMs } = await readJsonWithLock<{ v: number }>(testFile);
    await writeJsonWithLock(testFile, { v: 2 }, { expectedMtimeMs: mtimeMs });
    const after = JSON.parse(fs.readFileSync(testFile, 'utf-8'));
    expect(after.v).toBe(2);
  });

  it('writeJsonWithLock throws LockConflictError when mtime mismatches', async () => {
    fs.writeFileSync(testFile, JSON.stringify({ v: 1 }));
    // 外部から変更されたことをシミュレート: 少し待って別の mtime で上書き
    await new Promise((r) => setTimeout(r, 50));
    fs.writeFileSync(testFile, JSON.stringify({ v: 999 }));
    await expect(
      writeJsonWithLock(testFile, { v: 2 }, { expectedMtimeMs: 1 })
    ).rejects.toThrow(LockConflictError);
  });

  it('writeJsonWithLock creates new file when expectedMtimeMs=0 and file does not exist', async () => {
    const newFile = path.join(tmpDir, 'new.json');
    await writeJsonWithLock(newFile, { created: true }, { expectedMtimeMs: 0 });
    expect(fs.existsSync(newFile)).toBe(true);
    const data = JSON.parse(fs.readFileSync(newFile, 'utf-8'));
    expect(data.created).toBe(true);
  });
});
```

- [ ] **Step 3: テストを実行して失敗を確認**

```bash
cd apps/threads-manager && npx vitest run tests/file-lock.test.ts
```

Expected: `Cannot find module '../src/lib/file-lock'` 等のエラーで全失敗

- [ ] **Step 4: 最小実装**

`apps/threads-manager/src/lib/file-lock.ts`:

```typescript
import * as fs from 'fs/promises';
import { statSync, existsSync } from 'fs';
import * as path from 'path';

/**
 * Windows / NTFS / OneDrive では mtimeMs がミリ秒単位で切り捨てられたり
 * 再同期で微小に変わることがあるため、mtime 比較にはトレランスを設ける。
 * 5ms 以下の差なら同一とみなす。
 */
const MTIME_TOLERANCE_MS = 5;

/**
 * rename が OneDrive のファイルハンドルで一時的に失敗するケースに対応するリトライ回数と間隔。
 */
const RENAME_RETRY_ATTEMPTS = 3;
const RENAME_RETRY_BACKOFF_MS = 50;

export class LockConflictError extends Error {
  constructor(public filePath: string, public expectedMtimeMs: number, public actualMtimeMs: number) {
    super(`Lock conflict on ${filePath}: expected mtime ${expectedMtimeMs}, got ${actualMtimeMs}`);
    this.name = 'LockConflictError';
  }
}

export interface ReadResult<T> {
  data: T;
  mtimeMs: number;
}

export interface ReadOptions<T> {
  defaultValue?: T;
}

export async function readJsonWithLock<T>(filePath: string, options: ReadOptions<T> = {}): Promise<ReadResult<T>> {
  if (!existsSync(filePath)) {
    if (options.defaultValue !== undefined) {
      return { data: options.defaultValue, mtimeMs: 0 };
    }
    throw new Error(`File not found: ${filePath}`);
  }
  const stat = statSync(filePath);
  const content = await fs.readFile(filePath, 'utf-8');
  const data = JSON.parse(content) as T;
  return { data, mtimeMs: stat.mtimeMs };
}

export interface WriteOptions {
  expectedMtimeMs: number;
}

export interface WriteResult {
  /** 書き込み後の新しい mtime。次回書き込みの expectedMtimeMs として渡すこと。 */
  newMtimeMs: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function writeJsonWithLock(
  filePath: string,
  data: unknown,
  options: WriteOptions
): Promise<WriteResult> {
  // mtime 検証（トレランス付き）
  if (existsSync(filePath)) {
    const current = statSync(filePath);
    if (Math.abs(options.expectedMtimeMs - current.mtimeMs) > MTIME_TOLERANCE_MS) {
      throw new LockConflictError(filePath, options.expectedMtimeMs, current.mtimeMs);
    }
  } else {
    // 新規ファイル作成時は expectedMtimeMs === 0 のみ許可
    if (options.expectedMtimeMs !== 0) {
      throw new LockConflictError(filePath, options.expectedMtimeMs, 0);
    }
  }

  await fs.mkdir(path.dirname(filePath), { recursive: true });

  // tmp → rename で原子的書き込み（OneDrive 対策のリトライ付き）
  const tmpPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  const content = JSON.stringify(data, null, 2);
  await fs.writeFile(tmpPath, content, 'utf-8');

  try {
    let lastErr: any = null;
    for (let attempt = 0; attempt < RENAME_RETRY_ATTEMPTS; attempt++) {
      try {
        await fs.rename(tmpPath, filePath);
        lastErr = null;
        break;
      } catch (err: any) {
        lastErr = err;
        if (attempt < RENAME_RETRY_ATTEMPTS - 1) {
          await sleep(RENAME_RETRY_BACKOFF_MS * (attempt + 1));
        }
      }
    }
    // 全リトライ失敗時は copy+unlink フォールバック
    if (lastErr) {
      await fs.copyFile(tmpPath, filePath);
    }
  } finally {
    // tmp ファイル残骸を掃除（成功時は既に rename 済み、失敗時は copy 済みなので削除）
    await fs.unlink(tmpPath).catch(() => {});
  }

  // 書き込み後の mtime を返す
  const stat = statSync(filePath);
  return { newMtimeMs: stat.mtimeMs };
}
```

**注意**: テストコード側で `writeJsonWithLock` の戻り値 `{ newMtimeMs }` を使うように更新する必要はない（既存テストは返り値を無視しているので壊れない）。ただし新規テストで連続書き込みを検証する場合は `newMtimeMs` を次の呼び出しに渡すこと。

- [ ] **Step 5: テスト再実行**

```bash
cd apps/threads-manager && npx vitest run tests/file-lock.test.ts
```

Expected: 5 テスト全て PASS

- [ ] **Step 6: コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/lib/file-lock.ts apps/threads-manager/tests/file-lock.test.ts apps/threads-manager/vitest.config.ts
git commit -m "feat(threads-manager): add file-lock utility with mtime optimistic lock"
```

---

## Task 6: types.ts と accounts.ts の TDD 実装

**Files:**
- Create: `apps/threads-manager/src/lib/types.ts`
- Create: `apps/threads-manager/src/lib/accounts.ts`
- Create: `apps/threads-manager/tests/accounts.test.ts`

- [ ] **Step 1: types.ts を作成**

`apps/threads-manager/src/lib/types.ts`:

```typescript
export interface ThreadsAccount {
  id: string;
  name: string;
  username: string;
  persona: string;
  group: string;
  enabled: boolean;
  auth: {
    user_id: string;
    access_token: string;
    token_expires_at: string;
  };
  otp_url: string;
  limits: {
    max_posts_per_day: number;
    min_interval_seconds: number;
  };
}

export interface Group {
  id: string;
  description: string;
}

export interface AccountsFile {
  x_accounts?: any[];
  threads_accounts: ThreadsAccount[];
  groups: Group[];
  personas?: string[];
}
```

- [ ] **Step 2: accounts.test.ts を作成**

`apps/threads-manager/tests/accounts.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { getThreadsAccounts, getGroups } from '../src/lib/accounts';

describe('accounts', () => {
  let tmpDir: string;
  let accountsPath: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'threads-manager-accounts-'));
    accountsPath = path.join(tmpDir, 'accounts.json');
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it('getThreadsAccounts returns empty array for missing file', async () => {
    const result = await getThreadsAccounts(accountsPath);
    expect(result).toEqual([]);
  });

  it('getThreadsAccounts returns threads_accounts from existing file', async () => {
    const data = {
      threads_accounts: [
        {
          id: 'threads_test_01',
          name: 'テストアカウント',
          username: 'testuser',
          persona: 'rin',
          group: '占い_辛口',
          enabled: true,
          auth: { user_id: '', access_token: '', token_expires_at: '' },
          otp_url: '',
          limits: { max_posts_per_day: 5, min_interval_seconds: 1200 },
        },
      ],
      groups: [],
    };
    fs.writeFileSync(accountsPath, JSON.stringify(data));
    const result = await getThreadsAccounts(accountsPath);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('threads_test_01');
    expect(result[0].name).toBe('テストアカウント');
  });

  it('getGroups returns groups array', async () => {
    const data = {
      threads_accounts: [],
      groups: [
        { id: '占い_辛口', description: '辛口占い' },
        { id: '占い_癒し', description: '癒し系' },
      ],
    };
    fs.writeFileSync(accountsPath, JSON.stringify(data));
    const groups = await getGroups(accountsPath);
    expect(groups).toHaveLength(2);
    expect(groups[0].id).toBe('占い_辛口');
  });

  it('getGroups returns empty array for missing file', async () => {
    const groups = await getGroups(accountsPath);
    expect(groups).toEqual([]);
  });
});
```

- [ ] **Step 3: テスト実行して失敗確認**

```bash
cd apps/threads-manager && npx vitest run tests/accounts.test.ts
```

Expected: 4 テスト全て失敗

- [ ] **Step 4: accounts.ts 実装**

`apps/threads-manager/src/lib/accounts.ts`:

```typescript
import { readJsonWithLock } from './file-lock';
import type { ThreadsAccount, Group, AccountsFile } from './types';

const DEFAULT_ACCOUNTS: AccountsFile = {
  threads_accounts: [],
  groups: [],
};

export async function readAccountsFile(accountsPath: string): Promise<{ data: AccountsFile; mtimeMs: number }> {
  return readJsonWithLock<AccountsFile>(accountsPath, { defaultValue: DEFAULT_ACCOUNTS });
}

export async function getThreadsAccounts(accountsPath: string): Promise<ThreadsAccount[]> {
  const { data } = await readAccountsFile(accountsPath);
  return data.threads_accounts ?? [];
}

export async function getGroups(accountsPath: string): Promise<Group[]> {
  const { data } = await readAccountsFile(accountsPath);
  return data.groups ?? [];
}
```

- [ ] **Step 5: テスト再実行**

```bash
cd apps/threads-manager && npx vitest run tests/accounts.test.ts
```

Expected: 4 テスト PASS

- [ ] **Step 6: 全テスト確認**

```bash
cd apps/threads-manager && npm test
```

Expected: file-lock (5) + accounts (4) = 9 テスト PASS

- [ ] **Step 7: コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/lib/types.ts apps/threads-manager/src/lib/accounts.ts apps/threads-manager/tests/accounts.test.ts
git commit -m "feat(threads-manager): add types and accounts reader"
```

---

## Task 7: API Route /api/accounts の実装

**Files:**
- Create: `apps/threads-manager/src/app/api/accounts/route.ts`

- [ ] **Step 1: route.ts を作成**

`apps/threads-manager/src/app/api/accounts/route.ts`:

```typescript
import { NextResponse } from 'next/server';
import * as path from 'path';
import { existsSync } from 'fs';
import { getThreadsAccounts } from '@/lib/accounts';

/**
 * Resolve the path to config/accounts.json in a cwd-independent way.
 * process.cwd() differs depending on where npm run dev is invoked from,
 * so we search upward from multiple candidate roots and fail fast if not found.
 */
function resolveAccountsPath(): string {
  // Next.js dev server sets process.cwd() to the directory where `next dev` ran.
  // npm scripts set INIT_CWD to the directory where `npm` was invoked.
  const candidates = [
    process.env.INIT_CWD ? path.resolve(process.env.INIT_CWD, 'config', 'accounts.json') : null,
    path.resolve(process.cwd(), 'config', 'accounts.json'),
    path.resolve(process.cwd(), '..', '..', 'config', 'accounts.json'),
    path.resolve(process.cwd(), '..', 'config', 'accounts.json'),
  ].filter(Boolean) as string[];

  for (const p of candidates) {
    if (existsSync(p)) return p;
  }

  // Fail fast with clear diagnostics
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
```

- [ ] **Step 2: TypeScript チェック**

```bash
cd apps/threads-manager && npx tsc --noEmit
```

Expected: エラー無し

- [ ] **Step 3: コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/app/api/accounts/route.ts
git commit -m "feat(threads-manager): add GET /api/accounts endpoint"
```

---

## Task 8: Next.js UI 土台 (globals.css + layout.tsx)

**Files:**
- Create: `apps/threads-manager/src/app/globals.css`
- Create: `apps/threads-manager/src/app/layout.tsx`

- [ ] **Step 1: globals.css を作成**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --bg-primary: #0b1020;
  --bg-secondary: #121832;
  --bg-card: #1a2148;
  --text-primary: #e7e9f4;
  --text-secondary: #94a0c6;
  --accent: #7c3aed;
  --border: #2a3566;
  --sidebar-bg: #0a0f1e;
}

html, body {
  height: 100%;
  margin: 0;
  padding: 0;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Hiragino Sans', 'Noto Sans JP', sans-serif;
}
```

- [ ] **Step 2: layout.tsx を作成**

`apps/threads-manager/src/app/layout.tsx`:

```tsx
import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Threads Manager',
  description: 'マルチアカウント管理ツール',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 3: コミット**

```bash
git add apps/threads-manager/src/app/globals.css apps/threads-manager/src/app/layout.tsx
git commit -m "feat(threads-manager): add Next.js root layout and globals.css"
```

---

## Task 9: 3カラムシェル + コンポーネント

**Files:**
- Create: `apps/threads-manager/src/app/page.tsx`
- Create: `apps/threads-manager/src/components/sidebar.tsx`
- Create: `apps/threads-manager/src/components/account-list.tsx`
- Create: `apps/threads-manager/src/components/main-area.tsx`

- [ ] **Step 1: sidebar.tsx を作成（最小実装）**

```tsx
export default function Sidebar() {
  return (
    <aside
      className="w-56 h-full border-r flex flex-col"
      style={{ background: 'var(--sidebar-bg)', borderColor: 'var(--border)' }}
    >
      <div className="px-4 py-5 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center text-white font-bold"
            style={{ background: 'var(--accent)' }}
          >
            T
          </div>
          <div>
            <p className="font-bold text-sm">Threads Manager</p>
            <p className="text-[10px]" style={{ color: 'var(--text-secondary)' }}>
              マルチアカウント管理
            </p>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-3 space-y-1 text-sm">
        <div className="px-3 py-2 rounded-lg" style={{ background: 'var(--bg-card)', color: 'var(--accent)' }}>
          🏠 全て
        </div>
        <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
          📁 フォルダ (Phase 6 で実装)
        </div>
      </nav>
    </aside>
  );
}
```

- [ ] **Step 2: account-list.tsx を作成**

```tsx
'use client';

import { useEffect, useState } from 'react';
import type { ThreadsAccount } from '@/lib/types';

export default function AccountList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [accounts, setAccounts] = useState<ThreadsAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/accounts')
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setAccounts(data.accounts ?? []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

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
        {accounts.map((a) => (
          <button
            key={a.id}
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
            </div>
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ background: a.enabled ? '#22c55e' : '#6b7280' }}
            />
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: main-area.tsx を作成（最小実装）**

```tsx
export default function MainArea({ selectedId }: { selectedId: string | null }) {
  return (
    <div className="flex-1 flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
      <div className="text-center">
        {selectedId ? (
          <>
            <p className="text-lg">{selectedId}</p>
            <p className="text-sm mt-2" style={{ color: 'var(--text-secondary)' }}>
              アカウント詳細は Phase 3 以降で実装します
            </p>
          </>
        ) : (
          <p style={{ color: 'var(--text-secondary)' }}>左のリストからアカウントを選択してください</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: page.tsx を作成**

```tsx
'use client';

import { useState } from 'react';
import Sidebar from '@/components/sidebar';
import AccountList from '@/components/account-list';
import MainArea from '@/components/main-area';

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar />
      <AccountList selectedId={selectedId} onSelect={setSelectedId} />
      <MainArea selectedId={selectedId} />
    </div>
  );
}
```

- [ ] **Step 5: TypeScript チェック**

```bash
cd apps/threads-manager && npx tsc --noEmit
```

Expected: エラー無し

- [ ] **Step 6: コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/src/app/page.tsx apps/threads-manager/src/components/
git commit -m "feat(threads-manager): add 3-column shell with sidebar, account list, main area"
```

---

## Task 10: Electron main プロセス実装

**Files:**
- Create: `apps/threads-manager/electron/main.ts`
- Create: `apps/threads-manager/electron/preload.ts`

- [ ] **Step 1: preload.ts を作成**

`apps/threads-manager/electron/preload.ts`:

```typescript
import { contextBridge } from 'electron';

// 将来の IPC ブリッジ用の器（Phase 1 では空）
contextBridge.exposeInMainWorld('threadsManager', {
  version: '0.1.0',
});
```

- [ ] **Step 2: main.ts を作成**

`apps/threads-manager/electron/main.ts`:

```typescript
import { app, BrowserWindow, shell } from 'electron';
import * as path from 'path';

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

  // 外部リンクはデフォルトブラウザで開く
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

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
```

- [ ] **Step 3: Electron TS ビルド確認**

```bash
cd apps/threads-manager && npm run build:electron
```

Expected: `dist-electron/main.js` と `dist-electron/preload.js` が生成される。エラー無し。

- [ ] **Step 4: dist-electron を .gitignore（既に Task 4 で追加済みか確認）**

Run: `grep "dist-electron" ../../.gitignore`
Expected: `apps/threads-manager/dist-electron/` が含まれている

- [ ] **Step 5: コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/electron/
git commit -m "feat(threads-manager): add Electron main process and preload script"
```

---

## Task 11: 動作確認（npm run dev）

**目的:** 全てが組み合わさって動作することを確認。

- [ ] **Step 1: `npm run dev` 起動**

```bash
cd apps/threads-manager && npm run dev
```

Expected:
- Next.js dev server が `http://localhost:3050` で起動
- Electron ウィンドウが開く
- ウィンドウに 3 カラムレイアウトが表示される
- 中央カラムに「月詞メイン @tsukuyomi_uranai」が表示される（既存 `config/accounts.json` から読み込み）

- [ ] **Step 2: 動作確認項目のチェック**

ウィンドウで目視確認:
- [ ] 左サイドバー: 「Threads Manager」タイトル、「🏠 全て」項目
- [ ] 中央カラム: 「アカウント」ヘッダ、アカウント1件表示
- [ ] 右メインエリア: 「左のリストからアカウントを選択してください」
- [ ] アカウントをクリック → 右エリアに ID が表示される

- [ ] **Step 3: DevTools で console エラーが無いか確認**

- Git Bash: `OPEN_DEVTOOLS=1 npm run dev`
- PowerShell: `$env:OPEN_DEVTOOLS=1; npm run dev`
- cmd.exe: `set OPEN_DEVTOOLS=1 && npm run dev`

もしくはウィンドウメニューから手動で DevTools を開く（Ctrl+Shift+I）。
Expected: Console にエラーが無い（警告は許容）。
fetch エラーがあれば API Route のパスを確認。

- [ ] **Step 4: Ctrl+C で停止、全テスト再実行**

```bash
cd apps/threads-manager && npm test
```

Expected: 全テスト PASS（9テスト）

- [ ] **Step 5: README を作成**

`apps/threads-manager/README.md`:

```markdown
# Threads Manager

マルチ Threads アカウント管理デスクトップアプリ（Electron + Next.js 14）。

## 起動

```bash
cd apps/threads-manager
npm install
npm run dev
```

Electron ウィンドウが開き、`config/accounts.json` の `threads_accounts` を読み込んで表示します。

## テスト

```bash
npm test
```

## 備考

- **本番ビルド (`npm run start`) は MVP 範囲外**。開発モード (`npm run dev`) のみ動作保証。
- OAuth 接続、投稿、メトリクス等は Phase 2 以降で実装。
- 設計書: `docs/superpowers/specs/2026-04-22-threads-manager-design.md`
```

- [ ] **Step 6: README コミット**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git add apps/threads-manager/README.md
git commit -m "docs(threads-manager): add README with startup instructions"
```

---

## Task 12: Phase 1 完了コミット

- [ ] **Step 1: 全テスト PASS を再確認（完了条件の最終ゲート）**

```bash
cd apps/threads-manager && npm test
```

Expected: 全テスト PASS（file-lock 5 + accounts 4 = 9）。**失敗がある場合はコミットせず修正すること**。

- [ ] **Step 2: 変更内容の最終確認**

```bash
cd "/c/Users/fkgyo/OneDrive/デスクトップ/AI×占い自動運用システム開発"
git log --oneline -15
```

Expected: Task 0〜11 のコミットが順に並んでいる

- [ ] **Step 3: Phase 1 完了の集約コミット（空コミットで節目を記録）**

```bash
git commit --allow-empty -m "chore(threads-manager): Phase 1 complete

Phase 1 completion checklist:
- [x] apps/threads-manager/ 初期化
- [x] .gitignore + husky pre-commit hook
- [x] file-lock (mtime 楽観ロック) + テスト
- [x] accounts.ts + テスト
- [x] GET /api/accounts
- [x] Electron main + preload
- [x] 3-column layout with account list
- [x] npm run dev で起動確認

Next: Phase 2 (OAuth + Token management)
See: docs/superpowers/specs/2026-04-22-threads-manager-design.md §11"
```

- [ ] **Step 4: Phase 1 終了報告**

たちこさんに以下を報告:
- Phase 1 完了
- 動作確認できること: `npm run dev` でアプリ起動、既存アカウント表示
- 次フェーズ (Phase 2) の実装プラン作成に進むか判断を仰ぐ

---

## Phase 1 完了後の注意事項

**Phase 2 開始前に以下を確認（設計書 §14.1 参照）:**

1. Meta Developer Console で Threads App 登録
   - Redirect URI `http://localhost:47823/callback` が HTTP で登録可能か確認
   - HTTPS 必須なら Electron main で自己署名証明書の HTTPS サーバー実装が必要
2. `agents/poster.py` / `.github/scripts/generate_posts.py` を読み、`post-history.json` に `deleted_at`, `source` フィールドを追加しても無視されるか確認
3. Phase 2 の詳細実装プラン作成（OAuth callback server、Token 交換、リフレッシュロジック）

---

## トラブルシューティング

### `npm install` が失敗する
- `npm install --no-audit --no-fund` を試す
- それでもダメなら npm cache clean: `npm cache clean --force`

### Electron ウィンドウが白画面
- Next.js dev server が起動していない → ログを確認
- `localhost:3050` にブラウザで直接アクセスしてみる
- ポート衝突 → `netstat -ano | findstr 3050` で確認

### `npm run dev` で Electron が先に起動してしまう
- `wait-on` の指定が効いていない → Next.js dev server のアドレスを確認

### mtime テストが Windows で不安定
- `fs.statSync(path).mtimeMs` は Windows では 100ms 単位で切り捨てられる
- テストの `setTimeout` を 200ms に延長

### ルートに既に `package.json` がある場合
- Task 4 Step 3a はスキップ、既存 package.json に husky の依存と scripts を追記
