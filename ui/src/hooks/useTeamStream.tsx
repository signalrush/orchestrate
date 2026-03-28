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

          // Process complete lines (NDJSON)
          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''  // keep incomplete last line
          for (const line of lines) {
            const trimmed = line.trim()
            if (!trimmed) continue
            try {
              const event = JSON.parse(trimmed)
              window.dispatchEvent(new CustomEvent('team-sse-event', { detail: event }))
            } catch {}
          }
        }
        if (!cancelled) setTimeout(connect, 3000)
      } catch {
        if (!cancelled) setTimeout(connect, 3000)
      }
    }

    connect()
    return () => { cancelled = true; readerRef.current?.cancel() }
  }, [selectedEndpoint])
}
