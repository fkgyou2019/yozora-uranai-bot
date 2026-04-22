export default function MainArea({ selectedId }: { selectedId: string | null }) {
  return (
    <div className="flex-1 flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
      <div className="text-center">
        {selectedId ? (
          <>
            <p className="text-lg">{selectedId}</p>
            <p className="text-sm mt-2" style={{ color: 'var(--text-secondary)' }}>
              アカウント詳細は Phase 3 以降で実装します
            </p>
          </>
        ) : (
          <p style={{ color: 'var(--text-secondary)' }}>左のリストからアカウントを選択してください</p>
        )}
      </div>
    </div>
  );
}
