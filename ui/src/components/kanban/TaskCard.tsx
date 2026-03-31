'use client'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import type { KanbanTask } from '@/types/kanban'

function formatElapsed(secs: number): string {
  if (secs < 60) return `${secs}s`
  return `${Math.floor(secs / 60)}m ${secs % 60}s`
}


export default function TaskCard({ task, index, isSelected, onSelect }: { task: KanbanTask; index?: number; isSelected?: boolean; onSelect?: (task: KanbanTask) => void }) {
  const [elapsed, setElapsed] = useState<number>(() =>
    task.started_at ? Math.floor(Date.now() / 1000) - task.started_at : 0
  )
  const [visible, setVisible] = useState(false)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    const id = requestAnimationFrame(() => setVisible(true))
    return () => cancelAnimationFrame(id)
  }, [])

  useEffect(() => {
    if (task.status !== 'running' || !task.started_at) return
    const id = setInterval(() => {
      setElapsed(Math.floor(Date.now() / 1000) - task.started_at!)
    }, 1000)
    return () => clearInterval(id)
  }, [task.status, task.started_at])

  const elapsedDisplay =
    task.status === 'running'
      ? formatElapsed(elapsed)
      : task.elapsed_secs !== undefined
      ? formatElapsed(task.elapsed_secs)
      : null

  return (
    <div
      className={cn("bg-card border rounded-md p-2.5 space-y-1 text-sm shadow-sm cursor-pointer transition-all duration-150 hover:bg-muted/50 hover:border-border", isSelected ? "ring-1 ring-primary" : "border-border")}
      onClick={() => { setExpanded(e => !e); onSelect?.(task) }}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0)' : 'translateY(8px)',
        transition: 'opacity 90ms cubic-bezier(0.4,0,0.2,1), transform 90ms cubic-bezier(0.4,0,0.2,1)',
        transitionDelay: `${(index ?? 0) * 30}ms`,
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium truncate max-w-[120px]">
          {task.agent_name}
        </span>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {task.status === 'running' && (
            <span className="block h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
          )}
          {elapsedDisplay && (
            <span className="text-xs text-muted-foreground tabular-nums">{elapsedDisplay}</span>
          )}
          <svg
            className="h-3 w-3 text-muted-foreground/50 transition-transform duration-150 flex-shrink-0"
            style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)' }}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          >
            <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>

      <p className={cn('text-sm font-medium leading-snug', task.status === 'failed' && 'text-muted-foreground', !expanded && 'line-clamp-1')}>
        {task.title}
      </p>

      {task.summary && (task.status === 'running' || task.status === 'completed') && (
        <div className="flex items-center gap-1 min-w-0 mt-1.5">
          <span className={cn(
            'flex-shrink-0 text-[10px] leading-none',
            task.status === 'running' ? 'text-blue-500' : 'text-green-500'
          )}>●</span>
          <span className={cn('font-mono text-[12px] text-muted-foreground', expanded ? 'break-words whitespace-pre-wrap' : 'truncate')}>
            {task.summary}
          </span>
        </div>
      )}

      {task.status === 'failed' && task.error && (
        <p className={cn('text-xs text-destructive break-words mt-1.5', !expanded && 'line-clamp-2')}>{task.error}</p>
      )}

      {expanded && (
        <div className="pt-1 border-t border-border/50 space-y-0.5">
          <div className="flex items-center gap-1 min-w-0">
            <span className="text-[10px] text-muted-foreground/50 flex-shrink-0">id</span>
            <span className="font-mono text-[10px] text-muted-foreground truncate">{task.task_id}</span>
          </div>
          {task.created_at && (
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-muted-foreground/50 flex-shrink-0">created</span>
              <span className="text-[10px] text-muted-foreground">{new Date(task.created_at * 1000).toLocaleTimeString()}</span>
            </div>
          )}
          {task.run_id && (
            <div className="flex items-center gap-1 min-w-0">
              <span className="text-[10px] text-muted-foreground/50 flex-shrink-0">run</span>
              <span className="font-mono text-[10px] text-muted-foreground truncate">{task.run_id}</span>
            </div>
          )}
        </div>
      )}

      <div className="flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground font-normal">
          {task.status}
        </span>
        <span className="text-[10px] text-muted-foreground">
          {task.source}
        </span>
      </div>
    </div>
  )
}
