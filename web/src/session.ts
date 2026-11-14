import type { Entry, Session, StageState } from './api'

/** The song of the session: the first entry in setup, the one played afterwards. */
export function currentEntry(state: StageState): Entry | null {
  return state.playlist.find((entry) => entry.id === state.session.entry_id) ?? null
}

/** Every entry but the song of the session, in order. */
export function upcomingEntries(state: StageState): Entry[] {
  return state.playlist.filter((entry) => entry.id !== state.session.entry_id)
}

/** m:ss, or an empty string for an unknown time. */
export function formatTime(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return ''
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** The last REAPER error the session keeps, until an action or a position read works. */
export function sessionFailure(session: Session): { code: string; detail: string | null } | null {
  return session.error ? { code: session.error, detail: session.detail } : null
}
