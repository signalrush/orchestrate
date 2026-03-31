import React from 'react'
import { cn } from '@/lib/utils'
import type { KanbanTask, TaskStatus } from '@/types/kanban'
import TaskCard from './TaskCard'

const EMPTY_STATE: Record<TaskStatus, { icon: React.ReactNode; message: string }> = {
  queued: {
    icon: (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
    message: 'No tasks queued',
  },
  running: {
    icon: (
      <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M12 3a9 9 0 1 0 9 9" strokeLinecap="round" />
      </svg>
    ),
    message: 'Waiting for agents…',
  },
  completed: {
    icon: (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="12" cy="12" r="9" />
        <path d="M8.5 12.5l2.5 2.5 4.5-4.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
    message: 'No completed work yet',
  },
  failed: {
    icon: (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="12" cy="12" r="9" />
        <path d="M15 9l-6 6M9 9l6 6" strokeLinecap="round" />
      </svg>
    ),
    message: 'No failures',
  },
}

const HEADER_STYLE: Record<TaskStatus, string> = {
  queued: 'text-muted-foreground',
  running: 'text-blue-600 dark:text-blue-400',
  completed: 'text-green-600 dark:text-green-400',
  failed: 'text-red-600 dark:text-red-400',
}

const HEADER_DOT: Record<TaskStatus, string> = {
  queued: 'bg-muted-foreground/40',
  running: 'bg-blue-500',
  completed: 'bg-green-500',
  failed: 'bg-red-500',
}

interface KanbanColumnProps {
  title: string
  status: TaskStatus
  tasks: KanbanTask[]
  selectedTaskId?: string
  onSelectTask?: (task: KanbanTask) => void
}

export default function KanbanColumn({ title, status, tasks, selectedTaskId, onSelectTask }: KanbanColumnProps) {
  return (
    <div className="flex flex-col min-w-[180px] flex-1 bg-card/50 rounded-lg p-2 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-1 pb-2 mb-1">
        <span className={cn('block h-2 w-2 rounded-full flex-shrink-0', HEADER_DOT[status])} />
        <span className={cn('text-xs font-semibold uppercase tracking-wide', HEADER_STYLE[status])}>
          {title}
        </span>
        <span className="ml-auto text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded-full">
          {tasks.length}
        </span>
      </div>

      {/* Card list */}
      <div className="flex-1 overflow-y-auto space-y-2 pr-0.5">
        {tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-1.5 py-8 text-muted-foreground/40">
            {EMPTY_STATE[status].icon}
            <span className="text-[11px] text-muted-foreground/50">{EMPTY_STATE[status].message}</span>
          </div>
        ) : (
          tasks.map((task, i) => <TaskCard key={task.task_id} task={task} index={i} isSelected={selectedTaskId === task.task_id} onSelect={onSelectTask} />)
        )}
      </div>
    </div>
  )
}
