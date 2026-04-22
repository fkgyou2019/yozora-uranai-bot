'use client';

import { useState } from 'react';
import Sidebar from '@/components/sidebar';
import AccountList from '@/components/account-list';
import MainArea from '@/components/main-area';

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar />
      <AccountList selectedId={selectedId} onSelect={setSelectedId} />
      <MainArea selectedId={selectedId} />
    </div>
  );
}
