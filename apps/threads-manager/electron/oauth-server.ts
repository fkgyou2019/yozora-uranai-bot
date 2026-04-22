import * as http from 'http';
import * as crypto from 'crypto';

interface PendingState {
  resolve: (code: string) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

const TIMEOUT_MS = 5 * 60 * 1000;
const PORT = 47823;

const pendingStates = new Map<string, PendingState>();
let server: http.Server | null = null;

export function generateState(): string {
  return crypto.randomBytes(32).toString('hex');
}

export function startOAuthServer(): Promise<void> {
  if (server?.listening) return Promise.resolve();

  return new Promise((resolve, reject) => {
    server = http.createServer((req, res) => {
      if (!req.url?.startsWith('/callback')) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }

      const url = new URL(req.url, `http://localhost:${PORT}`);
      const code = url.searchParams.get('code');
      const state = url.searchParams.get('state');
      const error = url.searchParams.get('error');

      if (!state || !pendingStates.has(state)) {
        res.writeHead(400, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end('<h1>無効なリクエスト</h1><p>認証セッションが見つかりません。</p>');
        return;
      }

      const pending = pendingStates.get(state)!;
      clearTimeout(pending.timer);
      pendingStates.delete(state);

      if (error || !code) {
        res.writeHead(400, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(`<h1>認証エラー</h1><p>${error ?? 'code が受信できませんでした'}</p>`);
        pending.reject(new Error(error ?? 'No code received'));
        return;
      }

      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><title>Threads Manager</title>
        <style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#0b1020;color:#e7e9f4;}</style>
        </head><body><div style="text-align:center">
        <h1 style="color:#7c3aed">✅ 認証完了</h1>
        <p>このウィンドウを閉じて Threads Manager に戻ってください。</p>
        </div></body></html>`);
      pending.resolve(code);
    });

    server.listen(PORT, '127.0.0.1', () => {
      console.log(`[oauth-server] Listening on http://127.0.0.1:${PORT}`);
      resolve();
    });

    server.on('error', (err: NodeJS.ErrnoException) => {
      if (err.code === 'EADDRINUSE') {
        reject(new Error(`ポート ${PORT} は使用中です。他のアプリを終了してから再起動してください。`));
      } else {
        reject(err);
      }
    });
  });
}

export function stopOAuthServer(): Promise<void> {
  return new Promise((resolve) => {
    if (!server) { resolve(); return; }
    for (const [, pending] of pendingStates) {
      clearTimeout(pending.timer);
      pending.reject(new Error('Server stopped'));
    }
    pendingStates.clear();
    server.close(() => { server = null; resolve(); });
  });
}

export function waitForCallback(state: string): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingStates.delete(state);
      reject(new Error('OAuth タイムアウト（5分）。再試行してください。'));
    }, TIMEOUT_MS);
    pendingStates.set(state, { resolve, reject, timer });
  });
}

export function cancelState(state: string): void {
  const pending = pendingStates.get(state);
  if (pending) {
    clearTimeout(pending.timer);
    pendingStates.delete(state);
    pending.reject(new Error('cancelled'));
  }
}
