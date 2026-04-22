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
