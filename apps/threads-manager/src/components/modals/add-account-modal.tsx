'use client';

import { useState, useEffect } from 'react';

type Step = 'credentials' | 'name' | 'waiting' | 'success' | 'error';

interface Props {
  onClose: () => void;
  onSuccess: () => void;
}

declare global {
  interface Window {
    threadsManager?: {
      version: string;
      credentials: {
        get(): Promise<{ app_id: string; app_secret: string } | null>;
        save(creds: { app_id: string; app_secret: string }): Promise<void>;
      };
      oauth: {
        start(opts: { accountName: string; appId: string; appSecret: string }): Promise<any>;
        cancel(): Promise<void>;
        onResult(cb: (result: any) => void): () => void;
      };
      token: { refresh(id: string): Promise<void> };
    };
  }
}

export default function AddAccountModal({ onClose, onSuccess }: Props) {
  const [step, setStep] = useState<Step>('credentials');
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [accountName, setAccountName] = useState('');
  const [countdown, setCountdown] = useState(300);
  const [errorMsg, setErrorMsg] = useState('');
  const [successUsername, setSuccessUsername] = useState('');

  useEffect(() => {
    window.threadsManager?.credentials.get().then((creds) => {
      if (creds) {
        setAppId(creds.app_id);
        setAppSecret(creds.app_secret);
        setStep('name');
      }
    });
  }, []);

  useEffect(() => {
    if (step !== 'waiting') return;
    if (countdown <= 0) { setErrorMsg('タイムアウトしました。再試行してください。'); setStep('error'); return; }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [step, countdown]);

  async function handleSaveCredentials() {
    if (!appId.trim() || !appSecret.trim()) return;
    await window.threadsManager?.credentials.save({ app_id: appId.trim(), app_secret: appSecret.trim() });
    setStep('name');
  }

  async function handleStartOAuth() {
    if (!accountName.trim()) return;
    setStep('waiting');
    setCountdown(300);

    const result = await window.threadsManager?.oauth.start({
      accountName: accountName.trim(),
      appId,
      appSecret,
    });

    if (result?.status === 'success') {
      setSuccessUsername(result.username ?? '');
      setStep('success');
    } else if (result?.status === 'cancelled') {
      onClose();
    } else {
      setErrorMsg(result?.message ?? '不明なエラー');
      setStep('error');
    }
  }

  async function handleCancel() {
    await window.threadsManager?.oauth.cancel();
    onClose();
  }

  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50,
  };
  const modal: React.CSSProperties = {
    background: 'var(--bg-card)', border: '1px solid var(--border)',
    borderRadius: 12, padding: 32, width: 440, maxWidth: '90vw',
  };
  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)',
    background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14,
    boxSizing: 'border-box',
  };
  const btnPrimary: React.CSSProperties = {
    padding: '10px 20px', borderRadius: 8, border: 'none', cursor: 'pointer',
    background: 'var(--accent)', color: 'white', fontWeight: 600, fontSize: 14,
  };
  const btnSecondary: React.CSSProperties = {
    ...btnPrimary, background: 'transparent',
    border: '1px solid var(--border)', color: 'var(--text-secondary)',
  };

  return (
    <div style={overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modal}>
        <h2 style={{ marginTop: 0, fontSize: 18 }}>アカウント追加</h2>

        {step === 'credentials' && (
          <div>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 16 }}>
              Meta Developer Console の Threads App 認証情報を入力してください。
            </p>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>App ID</label>
            <input style={{ ...inputStyle, marginBottom: 12, marginTop: 4 }} value={appId}
              onChange={(e) => setAppId(e.target.value)} placeholder="123456789" />
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>App Secret</label>
            <input style={{ ...inputStyle, marginBottom: 20, marginTop: 4 }} type="password"
              value={appSecret} onChange={(e) => setAppSecret(e.target.value)} placeholder="••••••••" />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button style={btnSecondary} onClick={onClose}>キャンセル</button>
              <button style={btnPrimary} onClick={handleSaveCredentials}
                disabled={!appId || !appSecret}>次へ →</button>
            </div>
          </div>
        )}

        {step === 'name' && (
          <div>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 16 }}>
              アカウントの表示名を入力し、Threads で認証してください。
            </p>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>表示名</label>
            <input style={{ ...inputStyle, marginBottom: 20, marginTop: 4 }} value={accountName}
              onChange={(e) => setAccountName(e.target.value)} placeholder="例: 月詞メイン" />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button style={btnSecondary} onClick={() => setStep('credentials')}>← 戻る</button>
              <button style={btnPrimary} onClick={handleStartOAuth}
                disabled={!accountName.trim()}>Threads で認証 →</button>
            </div>
          </div>
        )}

        {step === 'waiting' && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>🔄</div>
            <p style={{ fontSize: 15, fontWeight: 600 }}>ブラウザで認証中...</p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
              開いたブラウザで Threads アカウントを認証してください。
            </p>
            <p style={{ fontSize: 24, fontWeight: 700, color: 'var(--accent)', margin: '20px 0' }}>
              {Math.floor(countdown / 60)}:{String(countdown % 60).padStart(2, '0')}
            </p>
            <button style={btnSecondary} onClick={handleCancel}>キャンセル</button>
          </div>
        )}

        {step === 'success' && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>✅</div>
            <p style={{ fontSize: 15, fontWeight: 600 }}>追加完了！</p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 20 }}>
              @{successUsername} を追加しました。
            </p>
            <button style={btnPrimary} onClick={() => { onSuccess(); onClose(); }}>閉じる</button>
          </div>
        )}

        {step === 'error' && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>❌</div>
            <p style={{ fontSize: 15, fontWeight: 600, color: '#f87171' }}>認証失敗</p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 20 }}>{errorMsg}</p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button style={btnSecondary} onClick={onClose}>閉じる</button>
              <button style={btnPrimary} onClick={() => setStep('name')}>再試行</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
