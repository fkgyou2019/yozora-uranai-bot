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
