'use client'
import { useEffect, useRef } from 'react'
import { useStore } from '@/store'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'

export default function useTeamStream() {
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const readerRef = useRef<ReadableStreamDefaultReader | null>(null)

  useEffect(() => {
    if (!selectedEndpoint) return
    const endpointUrl = constructEndpointUrl(selectedEndpoint)
    let cancelled = false

    async function connect() {
      try {
        const resp = await fetch(`${endpointUrl}/teams/default/events`)
        if (!resp.body) return
        const reader = resp.body.getReader()
        readerRef.current = reader
        const decoder = new TextDecoder()
        let buffer = ''

        while (!cancelled) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          // Extract complete JSON objects
          let startIdx = buffer.indexOf('{')
          while (startIdx >= 0) {
            let depth = 0, endIdx = -1
            for (let i = startIdx; i < buffer.length; i++) {
              if (buffer[i] === '{') depth++
              else if (buffer[i] === '}') { depth--; if (depth === 0) { endIdx = i; break } }
            }
            if (endIdx === -1) break
            try {
              const event = JSON.parse(buffer.slice(startIdx, endIdx + 1))
              window.dispatchEvent(new CustomEvent('team-sse-event', { detail: event }))
            } catch {}
            buffer = buffer.slice(endIdx + 1)
            startIdx = buffer.indexOf('{')
          }
        }
      } catch {
        if (!cancelled) setTimeout(connect, 3000)
      }
    }

    connect()
    return () => { cancelled = true; readerRef.current?.cancel() }
  }, [selectedEndpoint])
}
