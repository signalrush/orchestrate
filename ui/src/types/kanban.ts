export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface KanbanTask {
  task_id: string
  agent_name: string
  /** Truncated task instruction (≤80 chars from server) */
  title: string
  source: string
  status: TaskStatus
  session_id: string
  created_at: number       // unix timestamp
  started_at?: number      // set on TaskStarted
  completed_at?: number    // set on TaskCompleted
  failed_at?: number       // set on TaskFailed
  elapsed_secs?: number    // stored elapsed from TaskCompleted
  summary?: string         // first 200 chars of agent response
  error?: string           // error message from TaskFailed
  run_id?: string
}
