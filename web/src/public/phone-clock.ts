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

/** No snapshot, or one published more than OFFLINE_AFTER_MS ago in the store's clock. */
export function isOffline(publishedAtMs: number | null, storeNowMs: number): boolean {
  return publishedAtMs === null || storeNowMs - publishedAtMs > OFFLINE_AFTER_MS
}
