const THREADS_OAUTH_BASE = 'https://threads.net/oauth/authorize';
const THREADS_GRAPH_BASE = 'https://graph.threads.net';

export const REDIRECT_URI = 'http://localhost:47823/callback';

const OAUTH_SCOPES = [
  'threads_basic',
  'threads_content_publish',
  'threads_manage_replies',
  'threads_read_replies',
  'threads_manage_insights',
].join(',');

export function buildAuthUrl(appId: string, redirectUri: string, state: string): string {
  const params = new URLSearchParams({
    client_id: appId,
    redirect_uri: redirectUri,
    scope: OAUTH_SCOPES,
    response_type: 'code',
    state,
  });
  return `${THREADS_OAUTH_BASE}?${params.toString()}`;
}

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  const json = await res.json();
  if (!res.ok) {
    const msg = json?.error?.message ?? JSON.stringify(json);
    throw new Error(`Threads API error ${res.status}: ${msg}`);
  }
  return json as T;
}

export interface ShortTokenResponse {
  access_token: string;
  user_id: string;
}

export async function exchangeCodeForShortToken(opts: {
  appId: string;
  appSecret: string;
  redirectUri: string;
  code: string;
}): Promise<ShortTokenResponse> {
  const body = new URLSearchParams({
    client_id: opts.appId,
    client_secret: opts.appSecret,
    redirect_uri: opts.redirectUri,
    code: opts.code,
    grant_type: 'authorization_code',
  });
  return apiFetch<ShortTokenResponse>(`${THREADS_GRAPH_BASE}/oauth/access_token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
}

export interface LongTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export async function exchangeShortForLongToken(
  appSecret: string,
  shortToken: string
): Promise<LongTokenResponse> {
  const params = new URLSearchParams({
    grant_type: 'th_exchange_token',
    client_secret: appSecret,
    access_token: shortToken,
  });
  return apiFetch<LongTokenResponse>(`${THREADS_GRAPH_BASE}/access_token?${params.toString()}`);
}

export async function refreshLongToken(longToken: string): Promise<LongTokenResponse> {
  const params = new URLSearchParams({
    grant_type: 'th_refresh_token',
    access_token: longToken,
  });
  return apiFetch<LongTokenResponse>(
    `${THREADS_GRAPH_BASE}/refresh_access_token?${params.toString()}`
  );
}

export interface ThreadsMe {
  id: string;
  username: string;
}

export async function getThreadsMe(accessToken: string): Promise<ThreadsMe> {
  const params = new URLSearchParams({
    fields: 'id,username',
    access_token: accessToken,
  });
  return apiFetch<ThreadsMe>(`${THREADS_GRAPH_BASE}/me?${params.toString()}`);
}
