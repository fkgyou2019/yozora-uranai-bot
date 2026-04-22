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
