'use client'

import { useStore } from '@/store'
import Messages from './Messages'
import ScrollToBottom from '@/components/chat/ChatArea/ScrollToBottom'
import { StickToBottom } from 'use-stick-to-bottom'
import useTeamStream from '@/hooks/useTeamStream'

const MessageArea = () => {
  useTeamStream()
  const { messages } = useStore()
  const pendingQueue = useStore((state) => state.pendingQueue)

  return (
    <StickToBottom
      className="relative mb-4 flex max-h-[calc(100vh-64px)] min-h-0 flex-grow flex-col"
      resize="smooth"
      initial="smooth"
    >
      <StickToBottom.Content className="flex min-h-full flex-col justify-center">
        <div className="mx-auto w-full max-w-2xl space-y-9 px-4 pb-4">
          <Messages messages={messages} />
          {pendingQueue.length > 0 && (
            <div className="space-y-2 opacity-50">
              {pendingQueue.map((item, i) => (
                <div key={i} className="flex items-start gap-4 pt-2">
                  <div className="flex-shrink-0 h-6 w-6 rounded-[4px] bg-muted flex items-center justify-center">
                    <span className="text-[10px] text-muted-foreground">⏳</span>
                  </div>
                  <div className="text-sm text-muted-foreground italic">{item.content}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </StickToBottom.Content>
      <ScrollToBottom />
    </StickToBottom>
  )
}

export default MessageArea
