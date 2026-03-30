import { useCallback, useEffect } from 'react'

import { useStore } from '../store'
import { RunEvent, RunResponseContent, type RunResponse } from '@/types/os'
import { ToolCall } from '@/types/os'
import { useQueryState } from 'nuqs'
import { getJsonMarkdown } from '@/lib/utils'

const TOOL_STATUS_MAP: Record<string, (args: Record<string, string>) => string> = {
  read: (a) => `Reading ${a.file_path || a.path || 'file'}...`,
  read_file: (a) => `Reading ${a.file_path || a.path || 'file'}...`,
  write: (a) => `Writing ${a.file_path || a.path || 'file'}...`,
  write_file: (a) => `Writing ${a.file_path || a.path || 'file'}...`,
  edit: (a) => `Editing ${a.file_path || a.path || 'file'}...`,
  edit_file: (a) => `Editing ${a.file_path || a.path || 'file'}...`,
  bash: () => 'Running bash command...',
  glob: (a) => `Searching for ${a.pattern || 'files'}...`,
  grep: (a) => `Searching for ${a.pattern || 'pattern'}...`,
  web_search: (a) => `Searching the web${a.query ? ` for "${a.query}"` : ''}...`,
  websearch: (a) => `Searching the web${a.query ? ` for "${a.query}"` : ''}...`,
  web_fetch: () => 'Fetching web page...',
  webfetch: () => 'Fetching web page...',
}

function buildToolStatus(toolName: string, toolArgs: Record<string, string> = {}): string {
  const key = toolName?.toLowerCase()
  const fn = TOOL_STATUS_MAP[key]
  if (fn) return fn(toolArgs)
  const display = toolName.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return `${display}...`
}

const useAIChatStreamHandler = () => {
  const setMessages = useStore((state) => state.setMessages)
  const [sessionId, setSessionId] = useQueryState('session')
  const setStreamingErrorMessage = useStore(
    (state) => state.setStreamingErrorMessage
  )
  const setPendingQueue = useStore((state) => state.setPendingQueue)
  const setSessionsData = useStore((state) => state.setSessionsData)
  const setAgentStatus = useStore((state) => state.setAgentStatus)

  const updateMessagesWithErrorState = useCallback(() => {
    setMessages((prevMessages) => {
      const newMessages = [...prevMessages]
      const lastMessage = newMessages[newMessages.length - 1]
      if (lastMessage && lastMessage.role === 'agent') {
        lastMessage.streamingError = true
      }
      return newMessages
    })
  }, [setMessages])

  /**
   * Processes a new tool call and adds it to the message
   * @param toolCall - The tool call to add
   * @param prevToolCalls - The previous tool calls array
   * @returns Updated tool calls array
   */
  const processToolCall = useCallback(
    (toolCall: ToolCall, prevToolCalls: ToolCall[] = []) => {
      const toolCallId =
        toolCall.tool_call_id || `${toolCall.tool_name}-${toolCall.created_at}`

      const existingToolCallIndex = prevToolCalls.findIndex(
        (tc) =>
          (tc.tool_call_id && tc.tool_call_id === toolCall.tool_call_id) ||
          (!tc.tool_call_id &&
            toolCall.tool_name &&
            toolCall.created_at &&
            `${tc.tool_name}-${tc.created_at}` === toolCallId)
      )
      if (existingToolCallIndex >= 0) {
        const updatedToolCalls = [...prevToolCalls]
        updatedToolCalls[existingToolCallIndex] = {
          ...updatedToolCalls[existingToolCallIndex],
          ...toolCall
        }
        return updatedToolCalls
      } else {
        return [...prevToolCalls, toolCall]
      }
    },
    []
  )

  /**
   * Processes tool calls from a chunk, handling both single tool object and tools array formats
   * @param chunk - The chunk containing tool call data
   * @param existingToolCalls - The existing tool calls array
   * @returns Updated tool calls array
   */
  const processChunkToolCalls = useCallback(
    (
      chunk: RunResponseContent | RunResponse,
      existingToolCalls: ToolCall[] = []
    ) => {
      let updatedToolCalls = [...existingToolCalls]
      // Handle new single tool object format
      if (chunk.tool) {
        updatedToolCalls = processToolCall(chunk.tool, updatedToolCalls)
      }
      // Handle legacy tools array format
      if (chunk.tools && chunk.tools.length > 0) {
        for (const toolCall of chunk.tools) {
          updatedToolCalls = processToolCall(toolCall, updatedToolCalls)
        }
      }

      return updatedToolCalls
    },
    [processToolCall]
  )

  const processChunk = useCallback(
    (chunk: RunResponse) => {
      if (
        chunk.event === RunEvent.RunStarted ||
        chunk.event === RunEvent.TeamRunStarted ||
        chunk.event === RunEvent.ReasoningStarted ||
        chunk.event === RunEvent.TeamReasoningStarted
      ) {
        setSessionId(chunk.session_id as string)
        if (
          (!sessionId || sessionId !== chunk.session_id) &&
          chunk.session_id
        ) {
          const sessionData = {
            session_id: chunk.session_id as string,
            session_name: '', // Will be populated by the server
            created_at: chunk.created_at
          }
          setSessionsData((prevSessionsData) => {
            const sessionExists = prevSessionsData?.some(
              (session) => session.session_id === chunk.session_id
            )
            if (sessionExists) {
              return prevSessionsData
            }
            return [sessionData, ...(prevSessionsData ?? [])]
          })
        }
      } else if (
        (chunk.event as string) === 'AgentRegistered'
      ) {
        // New agent created — refresh session list
        setSessionsData((prev) => prev)
        window.dispatchEvent(new Event('sessions-refresh'))
      } else if (
        (chunk.event as string) === 'MessageQueued'
      ) {
        // Server says a message entered the queue — add to pending display
        setPendingQueue((prev) => [...prev, {
          content: typeof chunk.content === 'string' ? chunk.content : '',
          source: (chunk as any).source || 'user',
          created_at: chunk.created_at ?? Math.floor(Date.now() / 1000),
        }])
      } else if (
        (chunk.event as string) === 'MessageDequeued'
      ) {
        // Server says a message left the queue — remove from pending display
        const content = typeof chunk.content === 'string' ? chunk.content : ''
        const source = (chunk as any).source || 'user'
        setPendingQueue((prev) => {
          const idx = prev.findIndex((p) => p.content === content && p.source === source)
          if (idx >= 0) return [...prev.slice(0, idx), ...prev.slice(idx + 1)]
          return prev
        })
      } else if (
        chunk.event === RunEvent.ToolCallStarted ||
        chunk.event === RunEvent.TeamToolCallStarted ||
        chunk.event === RunEvent.ToolCallCompleted ||
        chunk.event === RunEvent.TeamToolCallCompleted
      ) {
        if (
          chunk.event === RunEvent.ToolCallStarted ||
          chunk.event === RunEvent.TeamToolCallStarted
        ) {
          const tool = chunk.tool
          if (tool) setAgentStatus(buildToolStatus(tool.tool_name, tool.tool_args))
        }
        setMessages((prevMessages) => {
          const newMessages = [...prevMessages]
          const lastMessage = newMessages[newMessages.length - 1]
          if (lastMessage && lastMessage.role === 'agent') {
            lastMessage.tool_calls = processChunkToolCalls(
              chunk,
              lastMessage.tool_calls
            )
          }
          return newMessages
        })
      } else if (
        chunk.event === RunEvent.RunContent ||
        chunk.event === RunEvent.TeamRunContent
      ) {
        setAgentStatus('')
        // Handle source-tagged events: create bubble + agent bubble
        if ((chunk as any).source === 'remind' || (chunk as any).source === 'user') {
          const role = (chunk as any).source === 'remind' ? 'remind' : 'user'
          const content = typeof chunk.content === 'string' ? chunk.content : ''
          setMessages((prevMessages) => {
            const newMessages = [...prevMessages]
            newMessages.push({
              role: role as any,
              content,
              created_at: chunk.created_at ?? Math.floor(Date.now() / 1000),
              member_name: (chunk as any).member_name
            })
            newMessages.push({
              role: 'agent',
              content: '',
              tool_calls: [],
              streamingError: false,
              created_at: (chunk.created_at ?? Math.floor(Date.now() / 1000)) + 1,
              member_name: (chunk as any).member_name
            })
            return newMessages
          })
          return
        }
        setMessages((prevMessages) => {
          const newMessages = [...prevMessages]
          const lastMessage = newMessages[newMessages.length - 1]
          if (
            lastMessage &&
            lastMessage.role === 'agent' &&
            typeof chunk.content === 'string'
          ) {
            const uniqueContent = chunk.content.replace(
              prevMessages[prevMessages.length - 1]?.content || '',
              ''
            )
            lastMessage.content += uniqueContent

            // Handle tool calls streaming
            lastMessage.tool_calls = processChunkToolCalls(
              chunk,
              lastMessage.tool_calls
            )
            if (chunk.extra_data?.reasoning_steps) {
              lastMessage.extra_data = {
                ...lastMessage.extra_data,
                reasoning_steps: chunk.extra_data.reasoning_steps
              }
            }

            if (chunk.extra_data?.references) {
              lastMessage.extra_data = {
                ...lastMessage.extra_data,
                references: chunk.extra_data.references
              }
            }

            lastMessage.created_at =
              chunk.created_at ?? lastMessage.created_at
            if (chunk.images) {
              lastMessage.images = chunk.images
            }
            if (chunk.videos) {
              lastMessage.videos = chunk.videos
            }
            if (chunk.audio) {
              lastMessage.audio = chunk.audio
            }
          } else if (
            lastMessage &&
            lastMessage.role === 'agent' &&
            typeof chunk?.content !== 'string' &&
            chunk.content !== null
          ) {
            const jsonBlock = getJsonMarkdown(chunk?.content)

            lastMessage.content += jsonBlock
          } else if (
            lastMessage &&
            chunk.response_audio?.transcript &&
            typeof chunk.response_audio?.transcript === 'string'
          ) {
            const transcript = chunk.response_audio.transcript
            lastMessage.response_audio = {
              ...lastMessage.response_audio,
              transcript:
                lastMessage.response_audio?.transcript + transcript
            }
          }
          return newMessages
        })
      } else if (
        chunk.event === RunEvent.ReasoningStep ||
        chunk.event === RunEvent.TeamReasoningStep
      ) {
        setMessages((prevMessages) => {
          const newMessages = [...prevMessages]
          const lastMessage = newMessages[newMessages.length - 1]
          if (lastMessage && lastMessage.role === 'agent') {
            const existingSteps =
              lastMessage.extra_data?.reasoning_steps ?? []
            const incomingSteps = chunk.extra_data?.reasoning_steps ?? []
            lastMessage.extra_data = {
              ...lastMessage.extra_data,
              reasoning_steps: [...existingSteps, ...incomingSteps]
            }
          }
          return newMessages
        })
      } else if (
        chunk.event === RunEvent.ReasoningCompleted ||
        chunk.event === RunEvent.TeamReasoningCompleted
      ) {
        setMessages((prevMessages) => {
          const newMessages = [...prevMessages]
          const lastMessage = newMessages[newMessages.length - 1]
          if (lastMessage && lastMessage.role === 'agent') {
            if (chunk.extra_data?.reasoning_steps) {
              lastMessage.extra_data = {
                ...lastMessage.extra_data,
                reasoning_steps: chunk.extra_data.reasoning_steps
              }
            }
          }
          return newMessages
        })
      } else if (
        chunk.event === RunEvent.RunError ||
        chunk.event === RunEvent.TeamRunError ||
        chunk.event === RunEvent.TeamRunCancelled
      ) {
        setAgentStatus('')
        updateMessagesWithErrorState()
        const errorContent =
          (chunk.content as string) ||
          (chunk.event === RunEvent.TeamRunCancelled
            ? 'Run cancelled'
            : 'Error during run')
        setStreamingErrorMessage(errorContent)
      } else if (
        chunk.event === RunEvent.UpdatingMemory ||
        chunk.event === RunEvent.TeamMemoryUpdateStarted ||
        chunk.event === RunEvent.TeamMemoryUpdateCompleted
      ) {
        // No-op for now; could surface a lightweight UI indicator in the future
      } else if (
        chunk.event === RunEvent.RunCompleted ||
        chunk.event === RunEvent.TeamRunCompleted
      ) {
        setAgentStatus('')
        setMessages((prevMessages) => {
          const newMessages = prevMessages.map((message, index) => {
            if (
              index === prevMessages.length - 1 &&
              message.role === 'agent'
            ) {
              // Keep existing content if RunCompleted has empty content
              // (content was already delivered via RunContent events)
              let updatedContent: string = message.content
              if (typeof chunk.content === 'string' && chunk.content) {
                updatedContent = chunk.content
              } else if (chunk.content && typeof chunk.content !== 'string') {
                try {
                  updatedContent = JSON.stringify(chunk.content)
                } catch {
                  // keep existing
                }
              }
              return {
                ...message,
                content: updatedContent,
                tool_calls: processChunkToolCalls(
                  chunk,
                  message.tool_calls
                ),
                images: chunk.images ?? message.images,
                videos: chunk.videos ?? message.videos,
                response_audio: chunk.response_audio,
                created_at: chunk.created_at ?? message.created_at,
                extra_data: {
                  reasoning_steps:
                    chunk.extra_data?.reasoning_steps ??
                    message.extra_data?.reasoning_steps,
                  references:
                    chunk.extra_data?.references ??
                    message.extra_data?.references
                }
              }
            }
            return message
          })
          return newMessages
        })
      }
    },
    [
      setMessages,
      updateMessagesWithErrorState,
      setStreamingErrorMessage,
      setSessionsData,
      sessionId,
      setSessionId,
      processChunkToolCalls,
      setPendingQueue,
      setAgentStatus
    ]
  )

  useEffect(() => {
    const handler = (e: Event) => {
      const chunk = (e as CustomEvent).detail
      // Filter: only process events for the current session, or session-establishing events
      const isSessionEvent = chunk.event === RunEvent.RunStarted ||
        chunk.event === RunEvent.TeamRunStarted ||
        chunk.event === RunEvent.ReasoningStarted ||
        chunk.event === RunEvent.TeamReasoningStarted ||
        (chunk.event as string) === 'AgentRegistered'
      if (!isSessionEvent && chunk.session_id && sessionId && chunk.session_id !== sessionId) return
      processChunk(chunk)
    }
    window.addEventListener('team-sse-event', handler)
    return () => window.removeEventListener('team-sse-event', handler)
  }, [processChunk, sessionId])

  return {}
}

export default useAIChatStreamHandler
