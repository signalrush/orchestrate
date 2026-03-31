'use client'

import ChatInput from './ChatInput'
import MessageArea from './MessageArea'

const ChatArea = () => {
  return (
    <main className="relative m-1.5 flex flex-1 min-h-0 flex-col rounded-xl bg-background overflow-hidden">
      <div className="flex-1 min-h-0 overflow-y-auto">
        <MessageArea />
      </div>
      <div className="flex-shrink-0 ml-9 px-4 pb-2">
        <ChatInput />
      </div>
    </main>
  )
}

export default ChatArea
