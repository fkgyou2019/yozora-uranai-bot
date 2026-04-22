export default function Sidebar() {
  return (
    <aside
      className="w-56 h-full border-r flex flex-col"
      style={{ background: 'var(--sidebar-bg)', borderColor: 'var(--border)' }}
    >
      <div className="px-4 py-5 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center text-white font-bold"
            style={{ background: 'var(--accent)' }}
          >
            T
          </div>
          <div>
            <p className="font-bold text-sm">Threads Manager</p>
            <p className="text-[10px]" style={{ color: 'var(--text-secondary)' }}>
              マルチアカウント管理
            </p>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-3 space-y-1 text-sm">
        <div className="px-3 py-2 rounded-lg" style={{ background: 'var(--bg-card)', color: 'var(--accent)' }}>
          🏠 全て
        </div>
        <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
          📁 フォルダ (Phase 6 で実装)
        </div>
      </nav>
    </aside>
  );
}
