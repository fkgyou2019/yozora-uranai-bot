import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { getThreadsAccounts, getGroups, writeAccountToken, addThreadsAccount, disableAccount, deleteAccount } from '../src/lib/accounts';

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
});
