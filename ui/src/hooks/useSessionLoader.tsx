import { useCallback, useRef } from 'react'
import { getSessionAPI, getAllSessionsAPI, getSessionEventsAPI } from '@/api/os'

// Module-level: survives remounts and is shared across all hook instances
const _sessionLastSeq: Record<string, number> = {}
const _sessionMessages: Record<string, ChatMessage[]> = {}
import { useStore } from '../store'
import { toast } from 'sonner'
import { ChatMessage, ToolCall, ReasoningMessage, ChatEntry } from '@/types/os'
import { getJsonMarkdown } from '@/lib/utils'

interface SessionResponse {
  session_id: string
  agent_id: string
  user_id: string | null
  runs?: ChatEntry[]
  memory: {
    runs?: ChatEntry[]
    chats?: ChatEntry[]
  }
  agent_data: Record<string, unknown>
}

interface LoaderArgs {
  entityType: 'agent' | 'team' | null
  agentId?: string | null
  teamId?: string | null
  dbId: string | null
}

const useSessionLoader = () => {
  const setMessages = useStore((state) => state.setMessages)
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const authToken = useStore((state) => state.authToken)
  const setIsSessionsLoading = useStore((state) => state.setIsSessionsLoading)
  const setSessionsData = useStore((state) => state.setSessionsData)
  const getSessionCounterRef = useRef(0)

  const getSessions = useCallback(
    async ({ entityType, agentId, teamId, dbId }: LoaderArgs) => {
      const selectedId = entityType === 'agent' ? agentId : teamId
      if (!selectedEndpoint || !entityType || !selectedId) return

      try {
        setIsSessionsLoading(true)

        const sessions = await getAllSessionsAPI(
          selectedEndpoint,
          entityType,
          selectedId,
          dbId ?? '',
          authToken
        )
        setSessionsData(sessions.data ?? [])
      } catch {
        toast.error('Error loading sessions')
        setSessionsData([])
      } finally {
        setIsSessionsLoading(false)
      }
    },
    [selectedEndpoint, authToken, setSessionsData, setIsSessionsLoading]
  )

  const getSession = useCallback(
    async (
      { entityType, agentId, teamId, dbId }: LoaderArgs,
      sessionId: string
    ) => {
      const selectedId = entityType === 'agent' ? agentId : teamId
      if (
        !selectedEndpoint ||
        !sessionId ||
        !entityType ||
        !selectedId
      )
        return

      getSessionCounterRef.current += 1
      const requestId = getSessionCounterRef.current

      try {
        // Try events endpoint first (preferred path)
        let events: Array<Record<string, unknown>> = []
        try {
          const after = _sessionLastSeq[sessionId] ?? 0
          events = await getSessionEventsAPI(selectedEndpoint, sessionId, after, authToken)
        } catch {
          // ignore — fall through to runs-based loading
        }

        if (requestId !== getSessionCounterRef.current) return null

        if (events.length > 0) {
          // Track lastSeq for this session
          const lastEvent = events[events.length - 1]
          _sessionLastSeq[sessionId] = (lastEvent.seq as number) ?? _sessionLastSeq[sessionId] ?? 0

          // Start from cached messages when fetching incrementally (after > 0)
          const msgs: ChatMessage[] = after > 0 ? [...(_sessionMessages[sessionId] ?? [])] : []
          for (const event of events) {
            const evt = event.event as string
            if (evt === 'RunContent') {
              const source = event.source as string | undefined
              if (source === 'user' || source === 'system' || source === 'remind') {
                const role = (source === 'system' || source === 'remind') ? 'remind' : 'user'
                msgs.push({
                  role: role as 'remind' | 'user',
                  content: (event.content as string) ?? '',
                  created_at: (event.created_at as number) ?? 0
                })
                msgs.push({
                  role: 'agent',
                  content: '',
                  tool_calls: [],
                  created_at: ((event.created_at as number) ?? 0) + 1
                })
              } else {
                const last = msgs[msgs.length - 1]
                if (last?.role === 'agent') {
                  // Each RunContent carries the full accumulated text — just set it
                  msgs[msgs.length - 1] = { ...last, content: (event.content as string) || '' }
                }
              }
            } else if (evt === 'ToolCallStarted') {
              const last = msgs[msgs.length - 1]
              const tools = (event.tools as ToolCall[]) || []
              if (last?.role === 'agent' && tools.length > 0) {
                msgs[msgs.length - 1] = {
                  ...last,
                  tool_calls: [...(last.tool_calls || []), ...tools]
                }
              }
            }
          }

          _sessionMessages[sessionId] = msgs
          setMessages(msgs)
          return msgs
        }

        // Already loaded this session before and caught up — no new events, no update needed
        if ((_sessionLastSeq[sessionId] ?? 0) > 0) return null

        // Fallback: runs-based loading for old sessions without events
        const response: SessionResponse = await getSessionAPI(
          selectedEndpoint,
          entityType,
          sessionId,
          dbId ?? '',
          authToken
        )
        if (requestId !== getSessionCounterRef.current) return null
        if (response) {
          if (Array.isArray(response)) {
            const messagesFor = response.flatMap((run) => {
              const filteredMessages: ChatMessage[] = []

              if (run) {
                filteredMessages.push({
                  role: (run.source === 'system' || run.source === 'remind') ? 'remind' : 'user',
                  content: run.run_input ?? '',
                  created_at: run.created_at
                })
              }

              if (run) {
                const toolCalls = [
                  ...(run.tools ?? []),
                  ...(run.extra_data?.reasoning_messages ?? []).reduce(
                    (acc: ToolCall[], msg: ReasoningMessage) => {
                      if (msg.role === 'tool') {
                        acc.push({
                          role: msg.role,
                          content: msg.content,
                          tool_call_id: msg.tool_call_id ?? '',
                          tool_name: msg.tool_name ?? '',
                          tool_args: msg.tool_args ?? {},
                          tool_call_error: msg.tool_call_error ?? false,
                          metrics: msg.metrics ?? { time: 0 },
                          created_at:
                            msg.created_at ?? Math.floor(Date.now() / 1000)
                        })
                      }
                      return acc
                    },
                    []
                  )
                ]

                filteredMessages.push({
                  role: 'agent',
                  content: (run.content as string) ?? '',
                  tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
                  extra_data: run.extra_data,
                  images: run.images,
                  videos: run.videos,
                  audio: run.audio,
                  response_audio: run.response_audio,
                  created_at: run.created_at
                })
              }
              return filteredMessages
            })

            const processedMessages = messagesFor.map(
              (message: ChatMessage) => {
                if (Array.isArray(message.content)) {
                  const textContent = message.content
                    .filter((item: { type: string }) => item.type === 'text')
                    .map((item) => item.text)
                    .join(' ')

                  return {
                    ...message,
                    content: textContent
                  }
                }
                if (typeof message.content !== 'string') {
                  return {
                    ...message,
                    content: getJsonMarkdown(message.content)
                  }
                }
                return message
              }
            )

            setMessages(processedMessages)
            return processedMessages
          }
        }
      } catch {
        return null
      }
    },
    [selectedEndpoint, authToken, setMessages]
  )

  return { getSession, getSessions }
}

export default useSessionLoader
