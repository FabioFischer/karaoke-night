import { useEffect, useState } from 'react'

const TICK_MS = 50
// The Stage reads REAPER about every 250 ms. A position that stops moving
// for longer is a REAPER paused by hand, so the clock stops guessing.
const MAX_AHEAD_S = 1

interface Anchor {
  position: number
  at: number
}

/**
 * The song position now, between two polls.
 *
 * The anchor is the polled position and the moment it first arrived. A poll
 * that repeats the same position keeps the older anchor, since that value was
 * read at the earlier moment; from the anchor, the browser clock moves the
 * position on, every TICK_MS, while the song plays.
 */
export function useSongPosition(positionS: number | null, playing: boolean): number | null {
  const [anchor, setAnchor] = useState<Anchor | null>(null)
  const [now, setNow] = useState(() => performance.now())

  useEffect(() => {
    if (positionS !== null) setAnchor({ position: positionS, at: performance.now() })
  }, [positionS])

  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => setNow(performance.now()), TICK_MS)
    return () => clearInterval(timer)
  }, [playing])

  if (!playing || anchor === null) return positionS
  const ahead = Math.min(Math.max(now - anchor.at, 0) / 1000, MAX_AHEAD_S)
  return anchor.position + ahead
}
