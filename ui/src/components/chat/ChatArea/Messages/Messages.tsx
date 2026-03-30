import type { ChatMessage } from '@/types/os'

import { AgentMessage, UserMessage, RemindMessage } from './MessageItem'
import Tooltip from '@/components/ui/tooltip'
import { memo } from 'react'
import {
  ToolCallProps,
  ReasoningStepProps,
  ReasoningProps,
  ReferenceData,
  Reference
} from '@/types/os'
import React, { type FC, useState } from 'react'

import Icon from '@/components/ui/icon'
import ChatBlankState from './ChatBlankState'

interface MessageListProps {
  messages: ChatMessage[]
}

interface MessageWrapperProps {
  message: ChatMessage
  isLastMessage: boolean
}

interface ReferenceProps {
  references: ReferenceData[]
}

interface ReferenceItemProps {
  reference: Reference
}

const ReferenceItem: FC<ReferenceItemProps> = ({ reference }) => (
  <div className="relative flex h-[63px] w-[190px] cursor-default flex-col justify-between overflow-hidden rounded-md bg-background-secondary p-3 transition-colors hover:bg-background-secondary/80">
    <p className="text-sm font-medium text-primary">{reference.name}</p>
    <p className="truncate text-xs text-primary/40">{reference.content}</p>
  </div>
)

const References: FC<ReferenceProps> = ({ references }) => (
  <div className="flex flex-col gap-4">
    {references.map((referenceData, index) => (
      <div
        key={`${referenceData.query}-${index}`}
        className="flex flex-col gap-3"
      >
        <div className="flex flex-wrap gap-3">
          {referenceData.references.map((reference, refIndex) => (
            <ReferenceItem
              key={`${reference.name}-${reference.meta_data.chunk}-${refIndex}`}
              reference={reference}
            />
          ))}
        </div>
      </div>
    ))}
  </div>
)

const AgentMessageWrapper = ({ message }: MessageWrapperProps) => {
  return (
    <div className="flex flex-col gap-y-9">
      {message.member_name && (
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent uppercase font-dmmono">
            {message.member_name}
          </span>
        </div>
      )}
      {message.extra_data?.reasoning_steps &&
        message.extra_data.reasoning_steps.length > 0 && (
          <div className="flex items-start gap-4">
            <Tooltip
              delayDuration={0}
              content={<p className="text-accent">Reasoning</p>}
              side="top"
            >
              <Icon type="reasoning" size="sm" />
            </Tooltip>
            <div className="flex flex-col gap-3">
              <p className="text-xs uppercase">Reasoning</p>
              <Reasonings reasoning={message.extra_data.reasoning_steps} />
            </div>
          </div>
        )}
      {message.extra_data?.references &&
        message.extra_data.references.length > 0 && (
          <div className="flex items-start gap-4">
            <Tooltip
              delayDuration={0}
              content={<p className="text-accent">References</p>}
              side="top"
            >
              <Icon type="references" size="sm" />
            </Tooltip>
            <div className="flex flex-col gap-3">
              <References references={message.extra_data.references} />
            </div>
          </div>
        )}
      {message.tool_calls && message.tool_calls.length > 0 && (
        <div className="flex items-start gap-3">
          <Tooltip
            delayDuration={0}
            content={<p className="text-accent">Tool Calls</p>}
            side="top"
          >
            <Icon
              type="hammer"
              className="rounded-lg bg-background-secondary p-1"
              size="sm"
              color="secondary"
            />
          </Tooltip>

          <div className="flex flex-col gap-1 w-full">
            {message.tool_calls.map((toolCall, index) => (
              <ToolComponent
                key={
                  toolCall.tool_call_id ||
                  `${toolCall.tool_name}-${toolCall.created_at}-${index}`
                }
                tools={toolCall}
              />
            ))}
          </div>
        </div>
      )}
      <AgentMessage message={message} />
    </div>
  )
}
const Reasoning: FC<ReasoningStepProps> = ({ index, stepTitle }) => (
  <div className="flex items-center gap-2 text-secondary">
    <div className="flex h-[20px] items-center rounded-md bg-background-secondary p-2">
      <p className="text-xs">STEP {index + 1}</p>
    </div>
    <p className="text-xs">{stepTitle}</p>
  </div>
)
const Reasonings: FC<ReasoningProps> = ({ reasoning }) => (
  <div className="flex flex-col items-start justify-center gap-2">
    {reasoning.map((title, index) => (
      <Reasoning
        key={`${title.title}-${title.action}-${index}`}
        stepTitle={title.title}
        index={index}
      />
    ))}
  </div>
)

function getArgsSummary(toolName: string, toolArgs: Record<string, string> | null | undefined): string {
  if (!toolArgs || Object.keys(toolArgs).length === 0) return ''
  if (toolArgs.file_path) return toolArgs.file_path
  if (toolArgs.path) return toolArgs.path
  if (toolArgs.command) return String(toolArgs.command).slice(0, 50)
  if (toolArgs.pattern) return String(toolArgs.pattern).slice(0, 50)
  if (toolArgs.query) return String(toolArgs.query).slice(0, 50)
  const firstVal = Object.values(toolArgs)[0]
  return firstVal ? String(firstVal).slice(0, 50) : ''
}

const EditDiffView = ({ filePath, oldString, newString }: { filePath?: string, oldString: string, newString: string }) => {
  const oldLines = oldString.split('\n')
  const newLines = newString.split('\n')

  return (
    <div className="font-dmmono text-[11px]">
      {filePath && (
        <div className="px-3 py-1.5 text-primary/40 text-[10px] uppercase tracking-wide border-b border-white/5">
          {filePath}
        </div>
      )}
      <div className="overflow-auto max-h-64">
        {oldLines.map((line, i) => (
          <div key={`r-${i}`} style={{ backgroundColor: '#3d1f1f' }} className="px-3 py-0 leading-5 whitespace-pre-wrap">
            <span className="text-red-400 select-none mr-2">-</span>{line}
          </div>
        ))}
        {newLines.map((line, i) => (
          <div key={`a-${i}`} style={{ backgroundColor: '#1f3d1f' }} className="px-3 py-0 leading-5 whitespace-pre-wrap">
            <span className="text-green-400 select-none mr-2">+</span>{line}
          </div>
        ))}
      </div>
    </div>
  )
}

const ToolComponent = memo(({ tools }: ToolCallProps) => {
  const [expanded, setExpanded] = useState(false)
  const summary = getArgsSummary(tools.tool_name, tools.tool_args)
  const hasError = tools.tool_call_error
  const timing = tools.metrics?.time

  return (
    <div
      className={`w-full rounded-md border text-xs font-dmmono overflow-hidden ${
        hasError ? 'border-red-500/50' : 'border-white/10'
      }`}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className={`w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors ${
          hasError ? 'bg-red-950/30 hover:bg-red-950/50' : 'bg-white/5 hover:bg-white/10'
        }`}
      >
        {hasError && <span className="text-red-400 flex-shrink-0 text-[10px]">✕</span>}
        <span className={`uppercase flex-shrink-0 ${hasError ? 'text-red-400' : 'text-muted-foreground'}`}>
          {tools.tool_name}
        </span>
        {summary && (
          <span className="text-primary/50 truncate normal-case">{summary}</span>
        )}
        {timing != null && (
          <span className="ml-auto flex-shrink-0 text-primary/30">
            {timing >= 1000 ? `${(timing / 1000).toFixed(1)}s` : `${timing}ms`}
          </span>
        )}
        <span className={`flex-shrink-0 text-primary/40 transition-transform ${expanded ? 'rotate-180' : ''} ${timing != null ? '' : 'ml-auto'}`}>
          ▾
        </span>
      </button>
      {expanded && (
        <div className="border-t border-white/10 bg-[#111113]">
          {tools.tool_name.toLowerCase() === 'edit' && tools.tool_args?.old_string != null && tools.tool_args?.new_string != null ? (
            <EditDiffView
              filePath={tools.tool_args.file_path}
              oldString={tools.tool_args.old_string}
              newString={tools.tool_args.new_string}
            />
          ) : (
            tools.tool_args && Object.keys(tools.tool_args).length > 0 && (
              <div className="p-3 border-b border-white/5">
                <p className="text-primary/40 mb-1 text-[10px] uppercase tracking-wide">Args</p>
                <pre className="text-[#FAFAFA]/70 text-[11px] overflow-auto whitespace-pre-wrap break-all">
                  {JSON.stringify(tools.tool_args, null, 2)}
                </pre>
              </div>
            )
          )}
          {tools.content != null && (
            <div className="p-3">
              <p className="text-primary/40 mb-1 text-[10px] uppercase tracking-wide">Result</p>
              <pre className="text-[#FAFAFA]/70 text-[11px] overflow-auto max-h-48 whitespace-pre-wrap break-all">
                {tools.content}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
})
ToolComponent.displayName = 'ToolComponent'
const Messages = ({ messages }: MessageListProps) => {
  if (messages.length === 0) {
    return <ChatBlankState />
  }

  return (
    <>
      {messages.map((message, index) => {
        const key = `${message.role}-${message.created_at}-${index}`
        const isLastMessage = index === messages.length - 1

        if (message.role === 'agent') {
          return (
            <AgentMessageWrapper
              key={key}
              message={message}
              isLastMessage={isLastMessage}
            />
          )
        }
        if (message.role === 'remind') {
          return (
            <React.Fragment key={key}>
              {index > 0 && <div className="my-4 border-t border-accent/40" />}
              <RemindMessage message={message} />
            </React.Fragment>
          )
        }
        return (
          <React.Fragment key={key}>
            {index > 0 && <div className="my-4 border-t border-accent/40" />}
            <UserMessage message={message} />
          </React.Fragment>
        )
      })}
    </>
  )
}

export default Messages
