import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type StageState } from './api'
import { CallsContext } from './calls'

const POLL_MS = 250

interface StageContextValue {
  /** The last state of the Stage, null until the first answer. */
  state: StageState | null
  /** The last poll failed: the Stage server cannot be reached. */
  offline: boolean
}

const StageContext = createContext<StageContextValue | null>(null)

function isStageState(value: unknown): value is StageState {
  return typeof value === 'object' && value !== null && 'session' in value && 'playlist' in value
}

/** Polls GET /api/state for every screen, one request at a time. */
export function StageStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StageState | null>(null)
  const [offline, setOffline] = useState(false)
  // Each poll takes a number when it is sent, and a poll older than the state
  // shown is dropped. GET /api/state does not wait for an action in flight, so
  // a poll sent before an action's answer arrived may carry the state before
  // the action: the answer is always shown and drops every poll sent so far.
  const sent = useRef(0)
  const shown = useRef(0)

  async function poll() {
    const request = ++sent.current
    try {
      const next = await api.state()
      if (request > shown.current) {
        shown.current = request
        setState(next)
      }
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function loop() {
      await poll()
      if (!stopped) timer = setTimeout(loop, POLL_MS)
    }
    loop()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
    // Runs once: poll only touches refs and state setters, which never change.
  }, [])

  /** Runs a call to the API, then shows the state it answered, or polls at once. */
  async function perform(call: () => Promise<unknown>) {
    const answer = await call()
    if (isStageState(answer)) {
      shown.current = sent.current
      setState(answer)
    } else {
      await poll()
    }
  }

  const calls = {
    search: api.search,
    library: api.library,
    add: api.add,
    remove: api.remove,
    perform,
  }
  return (
    <StageContext.Provider value={{ state, offline }}>
      <CallsContext.Provider value={calls}>{children}</CallsContext.Provider>
    </StageContext.Provider>
  )
}

export function useStage(): StageContextValue {
  const value = useContext(StageContext)
  if (value === null) throw new Error('useStage outside StageStateProvider')
  return value
}

