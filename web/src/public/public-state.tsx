import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Entry, Lyrics } from '../api'
import { CallsContext } from '../calls'
import { clockOffset, isOffline } from './phone-clock'
import { publicApi, type PublicSession, type Snapshot } from './public-api'

const POLL_MS = 2000
const CLOCK_SYNC_MS = 60_000

/** The snapshot as the phone's screens read it. */
export interface PhoneState {
  published_at_ms: number
  session: PublicSession
  playlist: Entry[]
  lyrics: Lyrics | null
}

interface PublicContextValue {
  /** The last snapshot, null until the Stage published one. */
  state: PhoneState | null
  /** Whether the first poll answered or failed: until then nothing is known. */
  loaded: boolean
  /** No snapshot, or the last one is too old (phone-clock.ts). */
  offline: boolean
  /** The phone's clock corrected to the store's, in milliseconds. */
  storeNow: () => number
}

const PublicContext = createContext<PublicContextValue | null>(null)

function toPhoneState(snapshot: Snapshot): PhoneState {
  return {
    published_at_ms: snapshot.published_at_ms,
    session: snapshot.session,
    playlist: snapshot.playlist.map((entry) => ({ ...entry, detail: null })),
    lyrics: snapshot.lyrics,
  }
}

/**
 * Keeps the offset between the store's clock and the phone's, from the time
 * route at start and every minute. Until the first answer the phone's own
 * clock is used as it is.
 */
function useStoreClock(): () => number {
  const offset = useRef(0)

  useEffect(() => {
    async function sync() {
      const sent = Date.now()
      try {
        const { now_ms } = await publicApi.time()
        offset.current = clockOffset(now_ms, sent, Date.now())
      } catch {
        // The last offset stays; the snapshot poll shows whether the Stage is reachable.
      }
    }
    sync()
    const timer = setInterval(sync, CLOCK_SYNC_MS)
    return () => clearInterval(timer)
  }, [])

  return () => Date.now() + offset.current
}

/** Polls the snapshot every POLL_MS for the phone's screens, one request at a time. */
export function PublicStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PhoneState | null>(null)
  const [loaded, setLoaded] = useState(false)
  // Set after every poll, so the offline notice follows the clock between snapshots.
  const [, setPolledAt] = useState(0)
  const storeNow = useStoreClock()
  // A poll older than the snapshot shown is dropped: an action polls at once,
  // next to the regular poll.
  const sent = useRef(0)
  const shown = useRef(0)

  async function poll() {
    const request = ++sent.current
    try {
      const snapshot = await publicApi.state()
      if (request > shown.current) {
        shown.current = request
        setState(snapshot === null ? null : toPhoneState(snapshot))
      }
    } catch {
      // The last snapshot stays, and ages into the offline notice.
    }
    setLoaded(true)
    setPolledAt(Date.now())
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

  /** Runs a request, then polls at once rather than waiting for the next poll. */
  async function perform(call: () => Promise<unknown>) {
    await call()
    await poll()
  }

  const offline = loaded && isOffline(state?.published_at_ms ?? null, storeNow())
  const calls = {
    search: publicApi.search,
    library: publicApi.library,
    add: publicApi.add,
    remove: publicApi.remove,
    perform,
  }
  return (
    <PublicContext.Provider value={{ state, loaded, offline, storeNow }}>
      <CallsContext.Provider value={calls}>{children}</CallsContext.Provider>
    </PublicContext.Provider>
  )
}

export function usePublicState(): PublicContextValue {
  const value = useContext(PublicContext)
  if (value === null) throw new Error('usePublicState outside PublicStateProvider')
  return value
}
