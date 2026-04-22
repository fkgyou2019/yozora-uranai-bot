import { contextBridge } from 'electron';

// 将来の IPC ブリッジ用の器（Phase 1 では空）
contextBridge.exposeInMainWorld('threadsManager', {
  version: '0.1.0',
});
