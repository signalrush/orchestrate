'use client'

import { FC, useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/cjs/styles/prism'

interface CodeBlockProps {
  language: string
  code: string
}

const CodeBlock: FC<CodeBlockProps> = ({ language, code }) => {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="group relative my-2 overflow-hidden rounded-md border border-border">
      <div className="flex items-center justify-between bg-background-secondary/50 px-3 py-1.5 text-xs text-muted">
        <span>{language}</span>
        <button
          onClick={handleCopy}
          className="opacity-0 transition-opacity group-hover:opacity-100"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <SyntaxHighlighter
        language={language}
        style={oneDark}
        customStyle={{
          margin: 0,
          borderRadius: 0,
          fontSize: '0.85em',
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}

export default CodeBlock
