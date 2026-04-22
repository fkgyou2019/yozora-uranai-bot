'use client';

import { useState, useEffect } from 'react';
import Sidebar from '@/components/sidebar';
import AccountList from '@/components/account-list';
import MainArea from '@/components/main-area';
import AddAccountModal from '@/components/modals/add-account-modal';

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const unsubscribe = window.threadsManager?.oauth.onResult(() => {
      setRefreshKey((k) => k + 1);
    });
    return () => unsubscribe?.();
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar />
      <AccountList
        selectedId={selectedId}
        onSelect={setSelectedId}
        onAddClick={() => setShowAddModal(true)}
        refreshKey={refreshKey}
      />
      <MainArea selectedId={selectedId} />

      {showAddModal && (
        <AddAccountModal
          onClose={() => setShowAddModal(false)}
          onSuccess={() => setRefreshKey((k) => k + 1)}
        />
      )}
    </div>
  );
}
