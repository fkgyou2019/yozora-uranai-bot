import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('threadsManager', {
  version: '0.1.0',

  credentials: {
    get: () => ipcRenderer.invoke('credentials:get'),
    save: (creds: { app_id: string; app_secret: string }) =>
      ipcRenderer.invoke('credentials:save', creds),
  },

  oauth: {
    start: (opts: { accountName: string; appId: string; appSecret: string }) =>
      ipcRenderer.invoke('oauth:start', opts),
    cancel: () => ipcRenderer.invoke('oauth:cancel'),
    onResult: (cb: (result: any) => void) => {
      const handler = (_event: any, result: any) => cb(result);
      ipcRenderer.on('oauth:complete', handler);
      return () => ipcRenderer.removeListener('oauth:complete', handler);
    },
  },

  token: {
    refresh: (accountId: string) => ipcRenderer.invoke('token:refresh', accountId),
  },
});
