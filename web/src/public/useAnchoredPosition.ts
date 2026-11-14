import { useEffect, useState } from 'react'
import type { PublicSession } from './public-api'

const TICK_MS = 100

/**
 * The song position now, from the snapshot's anchor and the corrected clock,
 * never past the song's duration (docs/PLAN.md section 5.9). The phone moves
 * the lines on its own, so a slow poll delays only the start and the end.
 */
export function useAnchoredPosition(session: PublicSession, storeNow: () => number): number | null {
  const [, setTick] = useState(0)
  const anchor = session.state === 'playing' ? session.anchor : null

  useEffect(() => {
    if (anchor === null) return
    const timer = setInterval(() => setTick((tick) => tick + 1), TICK_MS)
    return () => clearInterval(timer)
  }, [anchor])

  if (anchor === null) return null
  const position = anchor.position_s + (storeNow() - anchor.at_ms) / 1000
  return session.duration_s === null ? position : Math.min(position, session.duration_s)
}
