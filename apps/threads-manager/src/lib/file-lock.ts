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
