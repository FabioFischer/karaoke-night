import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, toApiError, type ApiError, type StageState } from './api'

const POLL_MS = 250

interface StageContextValue {
  /** The last state of the Stage, null until the first answer. */
  state: StageState | null
  /** The last poll failed: the Stage server cannot be reached. */
  offline: boolean
  /** Runs a call to the API, then shows the state it answered, or polls at once. */
  perform: (call: () => Promise<unknown>) => Promise<void>
}

const StageContext = createContext<StageContextValue | null>(null)

function isStageState(value: unknown): value is StageState {
  return typeof value === 'object' && value !== null && 'session' in value && 'playlist' in value
}

/** Polls GET /api/state for every screen, one request at a time. */
export function StageStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StageState | null>(null)
  const [offline, setOffline] = useState(false)
  // Each request takes a number when it is sent; an answer older than the one
  // shown is dropped, so a slow poll never undoes an action.
  const sent = useRef(0)
  const shown = useRef(0)

  function show(request: number, next: StageState) {
    if (request <= shown.current) return
    shown.current = request
    setState(next)
  }

  async function poll() {
    const request = ++sent.current
    try {
      show(request, await api.state())
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

  async function perform(call: () => Promise<unknown>) {
    const request = ++sent.current
    const answer = await call()
    if (isStageState(answer)) show(request, answer)
    else await poll()
  }

  return (
    <StageContext.Provider value={{ state, offline, perform }}>{children}</StageContext.Provider>
  )
}

export function useStage(): StageContextValue {
  const value = useContext(StageContext)
  if (value === null) throw new Error('useStage outside StageStateProvider')
  return value
}

/** One button's call to the API: whether it runs, and its refusal if any. */
export function useAction() {
  const { perform } = useStage()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  async function run(call: () => Promise<unknown>): Promise<boolean> {
    setBusy(true)
    setError(null)
    try {
      await perform(call)
      return true
    } catch (caught) {
      setError(toApiError(caught))
      return false
    } finally {
      setBusy(false)
    }
  }

  return { run, busy, error }
}
