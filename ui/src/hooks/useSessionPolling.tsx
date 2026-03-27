import { useCallback, useEffect, useRef } from 'react'
import { useQueryState } from 'nuqs'
import { getSessionAPI } from '@/api/os'
import { useStore } from '@/store'
import type { ChatMessage, ToolCall, ReasoningMessage } from '@/types/os'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'
import { getJsonMarkdown } from '@/lib/utils'

const POLL_INTERVAL = 3000

const useSessionPolling = () => {
  const [sessionId] = useQueryState('session')
  const [dbId] = useQueryState('db_id')
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const authToken = useStore((state) => state.authToken)
  const messages = useStore((state) => state.messages)
  const setMessages = useStore((state) => state.setMessages)
  const isStreaming = useStore((state) => state.isStreaming)
  const lastRunCount = useRef(0)

  // Reset run count when session changes
  useEffect(() => {
    lastRunCount.current = 0
  }, [sessionId])

  // Track current message count as baseline
  useEffect(() => {
    const runCount = Math.floor(messages.length / 2)
    if (runCount > lastRunCount.current) {
      lastRunCount.current = runCount
    }
  }, [messages.length])

  const poll = useCallback(async () => {
    if (!selectedEndpoint || !sessionId || isStreaming) return

    try {
      const endpointUrl = constructEndpointUrl(selectedEndpoint)
      const response = await getSessionAPI(
        endpointUrl,
        'agent',
        sessionId,
        dbId ?? undefined,
        authToken ?? undefined
      )

      if (!Array.isArray(response)) return

      const currentRunCount = response.length
      if (currentRunCount <= lastRunCount.current) return

      // New runs appeared — rebuild messages from all runs
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const newMessages: ChatMessage[] = response.flatMap((run: any) => {
        const msgs: ChatMessage[] = []

        if (run) {
          msgs.push({
            role: run.source === 'remind' ? 'remind' : 'user',
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

          let content: string
          if (typeof run.content === 'string') {
            content = run.content
          } else if (run.content !== undefined && run.content !== null) {
            content = getJsonMarkdown(run.content)
          } else {
            content = ''
          }

          msgs.push({
            role: 'agent',
            content,
            tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
            extra_data: run.extra_data,
            images: run.images,
            videos: run.videos,
            audio: run.audio,
            response_audio: run.response_audio,
            created_at: run.created_at
          })
        }

        return msgs
      })

      lastRunCount.current = currentRunCount
      setMessages(newMessages)
    } catch {
      // Silently ignore polling errors
    }
  }, [selectedEndpoint, sessionId, dbId, authToken, isStreaming, setMessages])

  useEffect(() => {
    const interval = setInterval(poll, POLL_INTERVAL)
    return () => clearInterval(interval)
  }, [poll])
}

export default useSessionPolling
