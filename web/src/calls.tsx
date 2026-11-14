import { createContext, useContext, useState } from 'react'
import { toApiError, type ApiError, type SearchAnswer, type Song } from './api'

/**
 * How the shared components reach the Stage. The TV calls the Stage API
 * itself (docs/PLAN.md section 5.8); a phone leaves requests the Stage
 * answers through Vercel (section 5.9).
 */
export interface StageCalls {
  search: (query: string) => Promise<SearchAnswer>
  library: () => Promise<{ songs: Song[] }>
  add: (song: Song) => Promise<unknown>
  remove: (entryId: string) => Promise<unknown>
  /** Runs a call, then shows its outcome: the state it answered, or a fresh poll. */
  perform: (call: () => Promise<unknown>) => Promise<void>
}

export const CallsContext = createContext<StageCalls | null>(null)

export function useCalls(): StageCalls {
  const value = useContext(CallsContext)
  if (value === null) throw new Error('useCalls outside CallsContext')
  return value
}

/** One button's call to the Stage: whether it runs, and its refusal if any. */
export function useAction() {
  const { perform } = useCalls()
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
