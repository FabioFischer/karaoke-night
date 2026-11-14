// The Stage API of docs/PLAN.md section 5.8, as the screens use it.

export type Origin = 'library' | 'ytmusic' | 'yt-dlp'

export interface Song {
  title: string
  artist: string
  origin: Origin
  video_id: string | null
  album: string | null
  duration_s: number | null
  thumbnail_url: string | null
  song_id: string | null
}

export type EntryState =
  | 'queued'
  | 'starting'
  | 'downloading'
  | 'separating'
  | 'lyrics'
  | 'packaging'
  | 'fetching'
  | 'ready'
  | 'failed'

export interface Progress {
  files_done: number
  files_total: number
  bytes_done: number
}

export interface Entry {
  id: string
  state: EntryState
  step: string | null
  progress: Progress | null
  error: string | null
  detail: string | null
  song: Song
}

export type SessionState = 'setup' | 'playing' | 'results'

export interface Session {
  state: SessionState
  entry_id: string | null
  muted: string[]
  key: number
  position_s: number | null
  duration_s: number | null
  error: string | null
  detail: string | null
  stems: string[]
}

export interface StageState {
  session: Session
  playlist: Entry[]
}

export interface SearchAnswer {
  results: Song[]
  notice: 'offline' | null
  detail: string | null
}

export interface LyricsLine {
  time_s: number | null
  text: string
}

export interface Lyrics {
  status: 'synced' | 'unsynced' | 'none'
  lines: LyricsLine[]
}

export type SessionAction = 'start' | 'stop' | 'play_next' | 'setup_next' | 'skip'

/** A refusal of the Stage: a stable code for the screen, an English detail. */
export class ApiError extends Error {
  code: string
  detail: string

  constructor(code: string, detail: string) {
    super(`${code}: ${detail}`)
    this.code = code
    this.detail = detail
  }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError('network', error instanceof Error ? error.message : String(error))
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    throw toApiError(error)
  }
  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    if (payload && typeof payload.error === 'string') {
      throw new ApiError(payload.error, String(payload.detail ?? ''))
    }
    throw new ApiError('http_error', `${method} ${path}: HTTP ${response.status}`)
  }
  return payload as T
}

export const api = {
  state: () => request<StageState>('GET', '/api/state'),
  search: (query: string) =>
    request<SearchAnswer>('GET', `/api/search?q=${encodeURIComponent(query)}`),
  library: () => request<{ songs: Song[] }>('GET', '/api/library'),
  lyrics: (songId: string) =>
    request<Lyrics>('GET', `/api/library/${encodeURIComponent(songId)}/lyrics`),
  add: (song: Song) => request<Entry>('POST', '/api/playlist', song),
  remove: (entryId: string) =>
    request<void>('DELETE', `/api/playlist/${encodeURIComponent(entryId)}`),
  settings: (muted: string[], key: number) =>
    request<StageState>('PUT', '/api/session/settings', { muted, key }),
  act: (action: SessionAction) => request<StageState>('POST', `/api/session/${action}`),
}
