// The phone's clock rules of docs/PLAN.md section 5.9. Times are milliseconds.

import type { PublicSession } from './public-api'

// The Stage publishes at least every 3 s.
const OFFLINE_AFTER_MS = 10_000

/** How far the store's clock is ahead of the phone's, from one call to the time route. */
export function clockOffset(storeNowMs: number, sentMs: number, receivedMs: number): number {
  return storeNowMs - (sentMs + receivedMs) / 2
}

/** The song position at `storeNowMs`, from the anchor, never past the duration; null outside playing. */
export function anchoredPosition(session: PublicSession, storeNowMs: number): number | null {
  if (session.state !== 'playing' || session.anchor === null) return null
  const position = session.anchor.position_s + (storeNowMs - session.anchor.at_ms) / 1000
  return session.duration_s === null ? position : Math.min(position, session.duration_s)
}

/** A position the phone showed, and the entry it belongs to. */
export interface ShownPosition {
  entry_id: string | null
  position: number | null
}

/**
 * The position the phone shows. While paused, the anchor holds where REAPER
 * paused, and the phone shows it without moving it on. A snapshot playing
 * without an anchor has no position: the phone holds the last one it showed
 * for the same entry, never another song's.
 */
export function shownPosition(
  session: PublicSession,
  storeNowMs: number,
  lastShown: ShownPosition | null,
): number | null {
  if (session.state !== 'playing') return null
  if (session.anchor === null) {
    return lastShown?.entry_id === session.entry_id ? lastShown.position : null
  }
  if (session.paused) return anchoredPosition(session, session.anchor.at_ms)
  return anchoredPosition(session, storeNowMs)
}

/** No snapshot, or one published more than OFFLINE_AFTER_MS ago in the store's clock. */
export function isOffline(publishedAtMs: number | null, storeNowMs: number): boolean {
  return publishedAtMs === null || storeNowMs - publishedAtMs > OFFLINE_AFTER_MS
}
