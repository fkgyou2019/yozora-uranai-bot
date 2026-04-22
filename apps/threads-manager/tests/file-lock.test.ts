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
