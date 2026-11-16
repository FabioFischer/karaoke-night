import type { Entry, Session } from './api'

/** What the playlist helpers read: the Stage's own state, or the snapshot a phone sees. */
export interface PlaylistView {
  session: Pick<Session, 'state' | 'entry_id'>
  playlist: Entry[]
}

/** The song of the session: the first entry in setup, the one played afterwards. */
export function currentEntry(state: PlaylistView): Entry | null {
  return state.playlist.find((entry) => entry.id === state.session.entry_id) ?? null
}

/** Every entry but the song of the session, in order. */
export function upcomingEntries(state: PlaylistView): Entry[] {
  return state.playlist.filter((entry) => entry.id !== state.session.entry_id)
}

/**
 * The entries the playlist lists, each with its state and a remove button. In
 * the setup nothing plays, so the current song is listed too: a song that
 * failed can be taken off and the next one comes up.
 */
export function listedEntries(state: PlaylistView): Entry[] {
  return state.session.state === 'setup' ? state.playlist : upcomingEntries(state)
}

/** Which ways an entry can move in the playlist, or null when it cannot move at all. */
export interface MoveOptions {
  up: boolean
  down: boolean
}

/**
 * Only the entries behind the song of the session move, one place at a time,
 * never past it and never past the end (docs/PLAN.md section 5.8, Reordering).
 */
export function moveOptions(state: PlaylistView, entryId: string): MoveOptions | null {
  const current = state.playlist.findIndex((entry) => entry.id === state.session.entry_id)
  const index = state.playlist.findIndex((entry) => entry.id === entryId)
  if (index === -1 || index <= current) return null
  return { up: index > current + 1, down: index < state.playlist.length - 1 }
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
