'use client';

import { useEffect, useState, useCallback } from 'react';
import type { ThreadsAccount } from '@/lib/types';
import { getTokenStatus, getTokenBadge, getTokenLabel } from '@/lib/token-utils';

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAddClick: () => void;
  refreshKey: number;
}

export default function AccountList({ selectedId, onSelect, onAddClick, refreshKey }: Props) {
  const [accounts, setAccounts] = useState<ThreadsAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);

  const fetchAccounts = useCallback(() => {
    setLoading(true);
    fetch('/api/accounts')
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setAccounts(data.accounts ?? []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchAccounts(); }, [fetchAccounts, refreshKey]);

  async function handleRefreshToken(accountId: string) {
    setMenuOpenId(null);
    try {
      await window.threadsManager?.token.refresh(accountId);
      fetchAccounts();
    } catch (e: any) {
      alert(`Token更新失敗: ${e.message}`);
    }
  }

  async function handleDisable(accountId: string) {
    setMenuOpenId(null);
    if (!confirm('このアカウントを無効化しますか？')) return;
    await fetch('/api/accounts', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: accountId, enabled: false }),
    });
    fetchAccounts();
  }

  return (
    <div
      className="w-72 h-full border-r flex flex-col"
      style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-sm font-semibold">アカウント</h2>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading && <p className="text-xs p-3" style={{ color: 'var(--text-secondary)' }}>読み込み中...</p>}
        {error && <p className="text-xs p-3 text-red-400">エラー: {error}</p>}
        {!loading && !error && accounts.length === 0 && (
          <p className="text-xs p-3" style={{ color: 'var(--text-secondary)' }}>アカウントがありません</p>
        )}

        {accounts.map((a) => {
          const status = getTokenStatus(a.auth?.token_expires_at ?? '');
          const badge = getTokenBadge(status);
          const label = getTokenLabel(a.auth?.token_expires_at ?? '');

          return (
            <div key={a.id} className="relative">
              <button
                onClick={() => { onSelect(a.id); setMenuOpenId(null); }}
                className="w-full flex items-center gap-3 p-2 rounded-lg text-left transition-colors"
                style={{
                  background: selectedId === a.id ? 'var(--bg-card)' : 'transparent',
                  border: selectedId === a.id ? '1px solid var(--accent)' : '1px solid transparent',
                }}
              >
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                  style={{ background: 'var(--accent)', color: 'white' }}
                >
                  {a.name.charAt(0)}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate">{a.name}</p>
                  <p className="text-[10px] truncate" style={{ color: 'var(--text-secondary)' }}>@{a.username}</p>
                  <p className="text-[10px]" style={{
                    color: status === 'expired' ? '#f87171' : status === 'warning' ? '#fbbf24' : 'var(--text-secondary)'
                  }}>{badge} {label}</p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); setMenuOpenId(menuOpenId === a.id ? null : a.id); }}
                  className="text-xs px-1 rounded"
                  style={{ color: 'var(--text-secondary)' }}
                >⋮</button>
              </button>

              {menuOpenId === a.id && (
                <div
                  className="absolute right-2 top-10 z-10 rounded-lg shadow-lg text-xs py-1"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', minWidth: 140 }}
                >
                  <button className="w-full text-left px-4 py-2 hover:opacity-80"
                    onClick={() => handleRefreshToken(a.id)}>🔄 Token 更新</button>
                  <button className="w-full text-left px-4 py-2 hover:opacity-80"
                    onClick={() => handleDisable(a.id)} style={{ color: '#fbbf24' }}>⏸ 無効化</button>
                  <div style={{ borderTop: '1px solid var(--border)', margin: '4px 0' }} />
                  <button className="w-full text-left px-4 py-2 hover:opacity-80"
                    onClick={() => { setMenuOpenId(null); alert('編集機能は近日実装予定です'); }}
                    style={{ color: 'var(--text-secondary)' }}>✏️ 編集</button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="p-3 border-t" style={{ borderColor: 'var(--border)' }}>
        <button onClick={onAddClick} className="w-full py-2 rounded-lg text-sm font-medium"
          style={{ background: 'var(--accent)', color: 'white' }}>
          ＋ アカウント追加
        </button>
      </div>
    </div>
  );
}
