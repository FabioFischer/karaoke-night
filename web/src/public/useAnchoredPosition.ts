import { useEffect, useRef, useState } from 'react'
import { shownPosition, type ShownPosition } from './phone-clock'
import type { PublicSession } from './public-api'

const TICK_MS = 100

/**
 * The song position now, worked out again every TICK_MS from the snapshot's
 * anchor (docs/PLAN.md section 5.9). The phone moves the lines on its own, so
 * a slow poll delays only the start and the end. While paused nothing ticks:
 * the position is where the song paused (phone-clock.ts).
 */
export function useAnchoredPosition(session: PublicSession, storeNow: () => number): number | null {
  const [, setTick] = useState(0)
  const lastShown = useRef<ShownPosition | null>(null)
  const playing = session.state === 'playing' && !session.paused && session.anchor !== null

  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => setTick((tick) => tick + 1), TICK_MS)
    return () => clearInterval(timer)
  }, [playing])

  const position = shownPosition(session, storeNow(), lastShown.current)
  lastShown.current = { entry_id: session.entry_id, position }
  return position
}
