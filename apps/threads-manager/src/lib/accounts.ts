import { readJsonWithLock, writeJsonWithLock } from './file-lock';
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
