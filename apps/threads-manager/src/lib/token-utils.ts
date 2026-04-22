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
