'use client'
import { useState, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { TextArea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { useStore } from '@/store'
import useAIChatStreamHandler from '@/hooks/useAIStreamHandler'
import { useQueryState } from 'nuqs'
import Icon from '@/components/ui/icon'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'

const ChatInput = () => {
  const { chatInputRef } = useStore()

  const { handleStreamResponse } = useAIChatStreamHandler()
  const [selectedAgent] = useQueryState('agent')
  const [teamId] = useQueryState('team')
  const [sessionId] = useQueryState('session')
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const [inputMessage, setInputMessage] = useState('')
  const isStreaming = useStore((state) => state.isStreaming)
  // Synchronous guard to prevent duplicate streams (React state is async)
  const streamActiveRef = useRef(false)
  // Buffer for messages sent before sessionId is ready
  const pendingMessagesRef = useRef<string[]>([])

  // Flush pending messages when sessionId becomes available
  useEffect(() => {
    if (sessionId && pendingMessagesRef.current.length > 0) {
      const msgs = pendingMessagesRef.current.splice(0)
      const endpointUrl = constructEndpointUrl(selectedEndpoint)
      for (const msg of msgs) {
        const formData = new FormData()
        formData.append('message', msg)
        formData.append('source', 'user')
        fetch(`${endpointUrl}/sessions/${sessionId}/message`, {
          method: 'POST',
          body: formData,
        }).catch(() => {})
      }
    }
  }, [sessionId, selectedEndpoint])

  const handleSubmit = async () => {
    if (!inputMessage.trim()) return

    const currentMessage = inputMessage
    setInputMessage('')

    try {
      if (isStreaming || streamActiveRef.current) {
        if (!sessionId) {
          // Session not ready — buffer silently, flush when sessionId arrives
          // Don't add local bubbles — the server source marker will create them
          pendingMessagesRef.current.push(currentMessage)
          return
        }
        // During active stream: push to queue. Server source marker creates bubble.
        const endpointUrl = constructEndpointUrl(selectedEndpoint)
        const formData = new FormData()
        formData.append('message', currentMessage)
        formData.append('source', 'user')
        await fetch(`${endpointUrl}/sessions/${sessionId}/message`, {
          method: 'POST',
          body: formData,
        }).catch(() => {})
      } else {
        // No active stream: create new stream
        streamActiveRef.current = true
        try {
          await handleStreamResponse(currentMessage)
        } finally {
          streamActiveRef.current = false
        }
      }
    } catch (error) {
      toast.error(
        `Error in handleSubmit: ${
          error instanceof Error ? error.message : String(error)
        }`
      )
    }
  }

  return (
    <div className="relative mx-auto mb-1 flex w-full max-w-2xl items-end justify-center gap-x-2 font-geist">
      <TextArea
        placeholder={'Ask anything'}
        value={inputMessage}
        onChange={(e) => setInputMessage(e.target.value)}
        onKeyDown={(e) => {
          if (
            e.key === 'Enter' &&
            !e.nativeEvent.isComposing &&
            !e.shiftKey
          ) {
            e.preventDefault()
            handleSubmit()
          }
        }}
        className="w-full border border-accent bg-primaryAccent px-4 text-sm text-primary focus:border-accent"
        disabled={!(selectedAgent || teamId)}
        ref={chatInputRef}
      />
      <Button
        onClick={handleSubmit}
        disabled={
          !(selectedAgent || teamId) || !inputMessage.trim()
        }
        size="icon"
        className="rounded-xl bg-primary p-5 text-primaryAccent"
      >
        <Icon type="send" color="primaryAccent" />
      </Button>
    </div>
  )
}

export default ChatInput
