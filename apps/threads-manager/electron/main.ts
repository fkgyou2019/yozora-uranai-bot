import { app, BrowserWindow, shell } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { registerIpcHandlers } from './ipc-bridge';

const NEXT_DEV_URL = 'http://localhost:3050';

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#0b1020',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.loadURL(NEXT_DEV_URL).catch((err) => {
    console.error('Failed to load URL:', err);
  });

  if (process.env.OPEN_DEVTOOLS === '1') {
    mainWindow.webContents.openDevTools();
  }
}

async function autoRefreshExpiringSoonTokens(): Promise<void> {
  try {
    const candidates = [
      process.env.INIT_CWD
        ? path.resolve(process.env.INIT_CWD, 'config', 'accounts.json')
        : null,
      path.resolve(process.cwd(), 'config', 'accounts.json'),
      path.resolve(process.cwd(), '..', '..', 'config', 'accounts.json'),
    ].filter(Boolean) as string[];

    const accountsPath = candidates.find((p) => fs.existsSync(p));
    if (!accountsPath) return;

    const raw = fs.readFileSync(accountsPath, 'utf-8');
    const data = JSON.parse(raw);
    const accounts = (data.threads_accounts ?? []) as Array<{
      id: string;
      auth?: { access_token?: string; token_expires_at?: string };
    }>;

    const SEVEN_DAYS = 7 * 24 * 60 * 60 * 1000;
    const needsRefresh = accounts.filter((a) => {
      if (!a.auth?.access_token || !a.auth.token_expires_at) return false;
      const exp = new Date(a.auth.token_expires_at).getTime();
      return exp - Date.now() < SEVEN_DAYS;
    });

    if (needsRefresh.length === 0) {
      console.log('[startup] All tokens are healthy.');
      return;
    }

    console.log(`[startup] Auto-refreshing ${needsRefresh.length} expiring token(s)...`);

    const { refreshLongToken } = await import('../src/lib/threads-api');
    const { readJsonWithLock, writeJsonWithLock } = await import('../src/lib/file-lock');

    const chunks: typeof needsRefresh[] = [];
    for (let i = 0; i < needsRefresh.length; i += 3) chunks.push(needsRefresh.slice(i, i + 3));

    for (const chunk of chunks) {
      await Promise.all(
        chunk.map(async (a) => {
          try {
            const refreshed = await refreshLongToken(a.auth!.access_token!);
            const expiresAt = new Date(Date.now() + refreshed.expires_in * 1000).toISOString();
            const { data: d, mtimeMs } = await readJsonWithLock<typeof data>(accountsPath);
            const idx = d.threads_accounts.findIndex((acc: any) => acc.id === a.id);
            if (idx !== -1) {
              d.threads_accounts[idx].auth.access_token = refreshed.access_token;
              d.threads_accounts[idx].auth.token_expires_at = expiresAt;
              await writeJsonWithLock(accountsPath, d, { expectedMtimeMs: mtimeMs });
              console.log(`[startup] Refreshed token for ${a.id}`);
            }
          } catch (err: any) {
            console.error(`[startup] Failed to refresh token for ${a.id}: ${err.message}`);
          }
        })
      );
    }
  } catch (err: any) {
    console.error('[startup] autoRefreshExpiringSoonTokens error:', err.message);
  }
}

app.whenReady().then(async () => {
  registerIpcHandlers();
  await autoRefreshExpiringSoonTokens();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
