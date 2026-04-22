'use client';

import { useEffect, useState } from 'react';
import type { ThreadsAccount } from '@/lib/types';

export default function AccountList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [accounts, setAccounts] = useState<ThreadsAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/accounts')
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setAccounts(data.accounts ?? []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

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
          <p className="text-xs p-3" style={{ color: 'var(--text-secondary)' }}>
            アカウントがありません
          </p>
        )}
        {accounts.map((a) => (
          <button
            key={a.id}
            onClick={() => onSelect(a.id)}
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
              <p className="text-[10px] truncate" style={{ color: 'var(--text-secondary)' }}>
                @{a.username}
              </p>
            </div>
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ background: a.enabled ? '#22c55e' : '#6b7280' }}
            />
          </button>
        ))}
      </div>
    </div>
  );
}
