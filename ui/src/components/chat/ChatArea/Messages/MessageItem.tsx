import Icon from '@/components/ui/icon'
import MarkdownRenderer from '@/components/ui/typography/MarkdownRenderer'
import { useStore } from '@/store'
import type { ChatMessage } from '@/types/os'
import Videos from './Multimedia/Videos'
import Images from './Multimedia/Images'
import Audios from './Multimedia/Audios'
import { memo } from 'react'
import AgentThinkingLoader from './AgentThinkingLoader'

interface MessageProps {
  message: ChatMessage
}

const AgentMessage = ({ message }: MessageProps) => {
  const { streamingErrorMessage } = useStore()
  let messageContent
  if (message.streamingError) {
    messageContent = (
      <p className="text-destructive">
        Oops! Something went wrong while streaming.{' '}
        {streamingErrorMessage ? (
          <>{streamingErrorMessage}</>
        ) : (
          'Please try refreshing the page or try again later.'
        )}
      </p>
    )
  } else if (message.content) {
    messageContent = (
      <div className="flex w-full flex-col gap-4">
        <MarkdownRenderer>{message.content}</MarkdownRenderer>
        {message.videos && message.videos.length > 0 && (
          <Videos videos={message.videos} />
        )}
        {message.images && message.images.length > 0 && (
          <Images images={message.images} />
        )}
        {message.audio && message.audio.length > 0 && (
          <Audios audio={message.audio} />
        )}
      </div>
    )
  } else if (message.response_audio) {
    if (!message.response_audio.transcript) {
      messageContent = (
        <div className="mt-2 flex items-start">
          <AgentThinkingLoader />
        </div>
      )
    } else {
      messageContent = (
        <div className="flex w-full flex-col gap-4">
          <MarkdownRenderer>
            {message.response_audio.transcript}
          </MarkdownRenderer>
          {message.response_audio.content && message.response_audio && (
            <Audios audio={[message.response_audio]} />
          )}
        </div>
      )
    }
  } else {
    messageContent = (
      <div className="mt-2">
        <AgentThinkingLoader />
      </div>
    )
  }

  const timeStr = message.created_at
    ? new Date(message.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : ''

  return (
    <div className="flex flex-row items-start gap-4 font-geist">
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <Icon type="agent" size="sm" />
        {message.member_name && (
          <span className="text-[10px] font-dmmono text-muted-foreground uppercase">
            {message.member_name}
          </span>
        )}
      </div>
      {messageContent}
    </div>
  )
}

const UserMessage = memo(({ message }: MessageProps) => {
  const timeStr = message.created_at
    ? new Date(message.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : ''

  return (
    <div className="flex items-start gap-4 pt-4 text-start max-md:break-words">
      <div className="flex-shrink-0">
        <Icon type="user" size="sm" />
      </div>
      <div className="text-sm rounded-lg font-geist text-secondary">
        {message.content}
        {timeStr && <span className="text-xs text-muted-foreground ml-2 whitespace-nowrap">{timeStr}</span>}
      </div>
    </div>
  )
})

const RemindMessage = memo(({ message }: MessageProps) => {
  const timeStr = message.created_at
    ? new Date(message.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : ''

  return (
    <div className="flex items-start gap-4 pt-4 text-start max-md:break-words">
      <div className="flex items-center gap-1 flex-shrink-0">
        <div className="flex h-6 w-6 items-center justify-center rounded-[4px] bg-[#6366f1] text-[10px] font-bold text-white">
          R
        </div>
        {timeStr && <span className="text-xs text-muted-foreground ml-2 whitespace-nowrap">{timeStr}</span>}
      </div>
      <div
        className="text-md rounded-lg font-geist text-secondary italic"
        title={message.content}
      >
        {message.content && message.content.length > 100
          ? message.content.slice(0, 100) + '...'
          : message.content}
      </div>
    </div>
  )
})
RemindMessage.displayName = 'RemindMessage'

AgentMessage.displayName = 'AgentMessage'
UserMessage.displayName = 'UserMessage'
export { AgentMessage, UserMessage, RemindMessage }
