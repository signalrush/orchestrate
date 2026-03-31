'use client'
import Sidebar from '@/components/chat/Sidebar/Sidebar'
import { ChatArea } from '@/components/chat/ChatArea'
import KanbanView from '@/components/kanban/KanbanView'
import TabBar from '@/components/TabBar'
import { Suspense } from 'react'
import { useStore } from '@/store'
import useTeamStream from '@/hooks/useTeamStream'
import useKanbanStream from '@/hooks/useKanbanStream'

function AppContent({ hasEnvToken, envToken }: { hasEnvToken: boolean; envToken: string }) {
  // Lift SSE connection here — stays open on both tabs
  useTeamStream()
  // Accumulate kanban tasks even while on Chat tab
  useKanbanStream()

  const activeTab = useStore((state) => state.activeTab)

  return (
    <div className="flex h-screen bg-background/80">
      <Sidebar hasEnvToken={hasEnvToken} envToken={envToken} />
      <div className="flex flex-col flex-1 min-w-0">
        <TabBar />
        {activeTab === 'chat' ? <ChatArea /> : <KanbanView />}
      </div>
    </div>
  )
}

export default function Home() {
  const hasEnvToken = !!process.env.NEXT_PUBLIC_OS_SECURITY_KEY
  const envToken = process.env.NEXT_PUBLIC_OS_SECURITY_KEY || ''
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <AppContent hasEnvToken={hasEnvToken} envToken={envToken} />
    </Suspense>
  )
}
