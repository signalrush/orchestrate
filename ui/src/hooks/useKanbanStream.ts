import { useEffect } from 'react'
import { useStore } from '@/store'
import type { KanbanTask } from '@/types/kanban'

export default function useKanbanStream() {
  const setTasks = useStore((state) => state.setTasks)

  useEffect(() => {
    const handler = (e: Event) => {
      const chunk = (e as CustomEvent).detail
      const event = chunk.event as string

      if (event === 'TaskCreated') {
        const task: KanbanTask = {
          task_id: chunk.task_id,
          agent_name: chunk.agent_name,
          title: chunk.title,
          source: chunk.source,
          status: 'queued',
          session_id: chunk.session_id,
          created_at: chunk.created_at,
        }
        setTasks((prev) => [task, ...prev])
      } else if (event === 'TaskStarted') {
        setTasks((prev) =>
          prev.map((t) =>
            t.task_id === chunk.task_id
              ? { ...t, status: 'running', started_at: chunk.started_at, run_id: chunk.run_id }
              : t
          )
        )
      } else if (event === 'TaskCompleted') {
        setTasks((prev) =>
          prev.map((t) =>
            t.task_id === chunk.task_id
              ? {
                  ...t,
                  status: 'completed',
                  completed_at: chunk.completed_at,
                  elapsed_secs: chunk.elapsed_secs,
                  summary: chunk.summary,
                  run_id: chunk.run_id,
                }
              : t
          )
        )
      } else if (event === 'TaskFailed') {
        setTasks((prev) =>
          prev.map((t) =>
            t.task_id === chunk.task_id
              ? {
                  ...t,
                  status: 'failed',
                  failed_at: chunk.failed_at,
                  error: chunk.error,
                  run_id: chunk.run_id,
                }
              : t
          )
        )
      }
    }

    window.addEventListener('team-sse-event', handler)
    return () => window.removeEventListener('team-sse-event', handler)
  }, [setTasks])
}
