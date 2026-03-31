'use client'
import { cn } from '@/lib/utils'
import { useStore } from '@/store'

export default function TabBar() {
  const activeTab = useStore((state) => state.activeTab)
  const setActiveTab = useStore((state) => state.setActiveTab)
  const tasks = useStore((state) => state.tasks)

  const tabs = [
    { id: 'chat' as const, label: 'Chat' },
    { id: 'kanban' as const, label: 'Kanban', badge: tasks.length || null },
  ]

  return (
    <div className="flex border-b border-border bg-background flex-shrink-0">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => setActiveTab(tab.id)}
          className={cn(
            'flex items-center gap-1.5 px-4 py-2 text-sm transition-colors',
            activeTab === tab.id
              ? 'border-b-2 border-foreground font-medium text-foreground'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          {tab.label}
          {tab.badge != null && (
            <span className="bg-primary text-primary-foreground rounded-full text-xs px-1.5 min-w-[20px] text-center tabular-nums">
              {tab.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
