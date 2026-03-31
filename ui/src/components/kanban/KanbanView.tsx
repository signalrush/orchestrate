'use client'
import { useStore } from '@/store'
import { useState, useCallback, useRef, useEffect } from 'react'
import KanbanColumn from './KanbanColumn'
import { ChatArea } from '@/components/chat/ChatArea'
import { useQueryState } from 'nuqs'
import useSessionLoader from '@/hooks/useSessionLoader'

export default function KanbanView() {
  const tasks = useStore((state) => state.tasks)
  const agents = useStore((state) => state.agents)
  const selectedTask = useStore((state) => state.selectedTask)
  const setSelectedTask = useStore((state) => state.setSelectedTask)
  const setMessages = useStore((state) => state.setMessages)
  const [chatPanelWidth, setChatPanelWidth] = useState(320)

  const [agentId, setAgentId] = useQueryState('agent')
  const [teamId, setTeamId] = useQueryState('team')
  const [dbId, setDbId] = useQueryState('db_id')
  const [, setSessionId] = useQueryState('session')

  const { getSession } = useSessionLoader()

  // Save params before switching to a task session
  const savedParams = useRef<{ agentId: string | null; teamId: string | null; dbId: string | null } | null>(null)

  const queued = tasks.filter((t) => t.status === 'queued')
  const running = tasks.filter((t) => t.status === 'running')
  const failed = tasks.filter((t) => t.status === 'failed')
  const completed = tasks.filter((t) => t.status === 'completed')

  // When selectedTask changes, switch agent/session
  useEffect(() => {
    if (selectedTask) {
      // Save current params (only on first selection, not on task switches)
      if (!savedParams.current) {
        savedParams.current = { agentId, teamId, dbId }
      }

      // Look up db_id from agents list
      const agent = agents.find((a) => a.id === selectedTask.agent_name)
      const taskDbId = agent?.db_id ?? ''

      // Update query params
      setAgentId(selectedTask.agent_name)
      setTeamId(null)
      setDbId(taskDbId || null)
      setSessionId(selectedTask.session_id)

      // Clear messages and load the session
      setMessages([])
      getSession(
        { entityType: 'agent', agentId: selectedTask.agent_name, teamId: null, dbId: taskDbId || null },
        selectedTask.session_id
      )
    } else {
      // Restore previous params
      if (savedParams.current) {
        const { agentId: prevAgentId, teamId: prevTeamId, dbId: prevDbId } = savedParams.current
        savedParams.current = null
        setAgentId(prevAgentId)
        setTeamId(prevTeamId)
        setDbId(prevDbId)
        setSessionId(null)
        setMessages([])
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTask])

  const handleDeselect = useCallback(() => {
    setSelectedTask(null)
  }, [setSelectedTask])

  const onResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = chatPanelWidth

    const onMouseMove = (ev: MouseEvent) => {
      const delta = startX - ev.clientX
      const newWidth = Math.min(600, Math.max(240, startWidth + delta))
      setChatPanelWidth(newWidth)
    }

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [chatPanelWidth])

  return (
    <div className="flex flex-1 min-w-0 h-full overflow-hidden">
      {/* Left: Kanban columns — contained, never bleeds into chat */}
      <div className="flex min-w-0 flex-1 gap-3 p-4 overflow-hidden">
        <KanbanColumn title="Backlog" status="queued" tasks={queued} selectedTaskId={selectedTask?.task_id} onSelectTask={setSelectedTask} />
        <KanbanColumn title="In Progress" status="running" tasks={running} selectedTaskId={selectedTask?.task_id} onSelectTask={setSelectedTask} />
        <KanbanColumn title="Failed" status="failed" tasks={failed} selectedTaskId={selectedTask?.task_id} onSelectTask={setSelectedTask} />
        <KanbanColumn title="Done" status="completed" tasks={completed} selectedTaskId={selectedTask?.task_id} onSelectTask={setSelectedTask} />
      </div>

      {/* Right: Chat panel — fixed width, never pushed by columns */}
      <div
        className="flex-shrink-0 border-l border-border flex flex-col min-h-0 overflow-hidden relative"
        style={{ width: chatPanelWidth, maxWidth: chatPanelWidth }}
      >
        {/* Resize handle */}
        <div
          className="absolute left-0 top-0 bottom-0 w-[5px] cursor-col-resize z-10 group flex items-center justify-center"
          onMouseDown={onResizeMouseDown}
        >
          <div className="w-px h-full bg-border group-hover:bg-primary/40 transition-colors duration-150" />
        </div>
        {/* Chat header */}
        <div className="flex items-center px-3 py-2 border-b border-border flex-shrink-0 gap-2 min-w-0">
          {selectedTask ? (
            <>
              <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium truncate flex-shrink-0 max-w-[100px]">
                {selectedTask.agent_name}
              </span>
              <span className="text-xs text-muted-foreground truncate flex-1 min-w-0">
                {selectedTask.title}
              </span>
              <button
                onClick={handleDeselect}
                className="flex-shrink-0 h-5 w-5 rounded flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                aria-label="Close agent chat"
              >
                <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </>
          ) : (
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Chat</span>
          )}
        </div>
        <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
          <ChatArea />
        </div>
      </div>
    </div>
  )
}
