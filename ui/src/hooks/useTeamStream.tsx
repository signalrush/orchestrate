'use client'
import { useEffect, useRef } from 'react'
import { useStore } from '@/store'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'

export default function useTeamStream() {
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const readerRef = useRef<ReadableStreamDefaultReader | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activeListenersRef = useRef<{ controller: AbortController } | null>(null)

  useEffect(() => {
    if (!selectedEndpoint) return
    const endpointUrl = constructEndpointUrl(selectedEndpoint)
    let cancelled = false
    let backoffMs = 1000

    async function connect() {
      const controller = new AbortController()
      activeListenersRef.current = { controller }

      try {
        const resp = await fetch(`${endpointUrl}/teams/default/events`, {
          signal: controller.signal,
        })
        if (!resp.body) return
        const reader = resp.body.getReader()
        readerRef.current = reader
        const decoder = new TextDecoder()
        let buffer = ''

        // Reset backoff on successful connection
        backoffMs = 1000

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
        if (!cancelled) {
          timeoutRef.current = setTimeout(connect, backoffMs)
        }
      } catch {
        if (!cancelled) {
          timeoutRef.current = setTimeout(connect, backoffMs)
          backoffMs = Math.min(backoffMs * 2, 30000)
        }
      }
    }

    connect()
    return () => {
      cancelled = true
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
      activeListenersRef.current?.controller.abort()
      activeListenersRef.current = null
      readerRef.current?.cancel()
    }
  }, [selectedEndpoint])
}
