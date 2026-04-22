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
