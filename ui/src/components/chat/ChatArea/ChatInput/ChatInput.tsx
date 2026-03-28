'use client'
import { useState } from 'react'
import { toast } from 'sonner'
import { TextArea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { useStore } from '@/store'
import { useQueryState } from 'nuqs'
import Icon from '@/components/ui/icon'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'

const ChatInput = () => {
  const { chatInputRef } = useStore()

  const [selectedAgent] = useQueryState('agent')
  const [teamId] = useQueryState('team')
  const [sessionId] = useQueryState('session')
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const [inputMessage, setInputMessage] = useState('')

  const handleSubmit = async () => {
    if (!inputMessage.trim()) return
    const currentMessage = inputMessage
    setInputMessage('')

    try {
      const endpointUrl = constructEndpointUrl(selectedEndpoint)
      const agentId = selectedAgent || 'orchestrator'

      if (sessionId) {
        // Existing session: fire-and-forget
        const formData = new FormData()
        formData.append('message', currentMessage)
        formData.append('source', 'user')
        fetch(`${endpointUrl}/agents/${agentId}/message`, {
          method: 'POST',
          body: formData,
        }).catch(() => {})
      } else {
        // New session: create via /runs (don't read stream — team SSE delivers events)
        const formData = new FormData()
        formData.append('message', currentMessage)
        formData.append('stream', 'false')
        fetch(`${endpointUrl}/agents/${agentId}/runs`, {
          method: 'POST',
          body: formData,
        }).catch(() => {})
      }
    } catch (error) {
      toast.error(`Error: ${error instanceof Error ? error.message : String(error)}`)
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
