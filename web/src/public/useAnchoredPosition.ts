import { useEffect, useState } from 'react'
import { anchoredPosition } from './phone-clock'
import type { PublicSession } from './public-api'

const TICK_MS = 100

/**
 * The song position now, worked out again every TICK_MS from the snapshot's
 * anchor (docs/PLAN.md section 5.9). The phone moves the lines on its own, so
 * a slow poll delays only the start and the end.
 */
export function useAnchoredPosition(session: PublicSession, storeNow: () => number): number | null {
  const [, setTick] = useState(0)
  const playing = session.state === 'playing' && session.anchor !== null

  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => setTick((tick) => tick + 1), TICK_MS)
    return () => clearInterval(timer)
  }, [playing])

  return anchoredPosition(session, storeNow())
}
