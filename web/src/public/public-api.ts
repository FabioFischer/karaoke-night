// The public routes of docs/PLAN.md section 5.9, as the phone uses them.

import {
  ApiError,
  request,
  type Entry,
  type Lyrics,
  type MoveBy,
  type SearchAnswer,
  type SessionState,
  type Song,
} from '../api'

const ANSWER_POLL_MS = 1000
const ANSWER_TIMEOUT_MS = 15_000
// A call that hangs must not freeze a poll: past this it fails as `network`.
const CALL_TIMEOUT_MS = 5000
// Codes after which the request is still in the store and may be answered:
// keep polling until ANSWER_TIMEOUT_MS.
const KEEP_POLLING = ['pending', 'network', 'store_unavailable']

/** The song's position at one instant, in the store's clock. */
export interface Anchor {
  position_s: number
  at_ms: number
}

export interface PublicSession {
  state: SessionState
  entry_id: string | null
  duration_s: number | null
  paused: boolean
  /** Null outside playing; while paused, where the song paused. */
  anchor: Anchor | null
}

/** The snapshot as the Stage publishes it: entries come without their English detail. */
export interface Snapshot {
  version: 1
  published_at_ms: number
  session: PublicSession
  playlist: Omit<Entry, 'detail'>[]
  lyrics: (Lyrics & { song_id: string }) | null
}

type StateAnswer = Snapshot | { version: 1; snapshot: null }

type Answer =
  | { ok: true; entry_id?: string; results?: Song[]; notice?: 'offline' | null }
  | { ok: false; error: string }

type PublicRequest =
  | { op: 'add'; song: Song }
  | { op: 'remove'; entry_id: string }
  | { op: 'move'; entry_id: string; by: MoveBy }
  | { op: 'search'; q: string }

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** A signal that gives up on a call after `ms`. */
function timeout(ms: number): AbortSignal {
  const controller = new AbortController()
  setTimeout(() => controller.abort(new DOMException('no answer in time', 'TimeoutError')), ms)
  return controller.signal
}

/** Leaves the request in the store and gives its id. */
async function leave(body: PublicRequest): Promise<string> {
  try {
    const { id } = await request<{ id: string }>(
      'POST',
      '/api/public/requests',
      body,
      timeout(CALL_TIMEOUT_MS),
    )
    return id
  } catch (error) {
    // An add or a remove lost on the way may have been stored all the same:
    // read as no_answer, so the phone says to check the playlist before trying again.
    if (error instanceof ApiError && error.code === 'network' && body.op !== 'search') {
      throw new ApiError('no_answer', error.detail)
    }
    throw error
  }
}

/**
 * Leaves a request for the Stage and waits for its answer, polling every
 * second. The request may still be applied after the phone gives up.
 */
async function ask(body: PublicRequest): Promise<Answer> {
  const id = await leave(body)
  const deadline = Date.now() + ANSWER_TIMEOUT_MS
  while (Date.now() < deadline) {
    await sleep(ANSWER_POLL_MS)
    const left = Math.min(CALL_TIMEOUT_MS, deadline - Date.now())
    try {
      return await request<Answer>(
        'GET',
        `/api/public/requests/${encodeURIComponent(id)}`,
        undefined,
        timeout(left),
      )
    } catch (error) {
      if (error instanceof ApiError && KEEP_POLLING.includes(error.code)) continue
      throw error
    }
  }
  throw new ApiError('no_answer', `no answer to request ${id} in ${ANSWER_TIMEOUT_MS / 1000} s`)
}

async function askOk(body: PublicRequest): Promise<Answer & { ok: true }> {
  const answer = await ask(body)
  if (!answer.ok) throw new ApiError(answer.error, '')
  return answer
}

export const publicApi = {
  /** The snapshot, or null before the Stage published one. */
  state: async (): Promise<Snapshot | null> => {
    const answer = await request<StateAnswer>(
      'GET',
      '/api/public/state',
      undefined,
      timeout(CALL_TIMEOUT_MS),
    )
    return 'published_at_ms' in answer ? answer : null
  },
  library: () =>
    request<{ songs: Song[] }>('GET', '/api/public/library', undefined, timeout(CALL_TIMEOUT_MS)),
  time: () =>
    request<{ now_ms: number }>('GET', '/api/public/time', undefined, timeout(CALL_TIMEOUT_MS)),
  add: (song: Song) => askOk({ op: 'add', song }),
  remove: (entryId: string) => askOk({ op: 'remove', entry_id: entryId }),
  move: (entryId: string, by: MoveBy) => askOk({ op: 'move', entry_id: entryId, by }),
  /** A search that got no answer, or one too old for the Stage, is just a search to try again. */
  search: async (query: string): Promise<SearchAnswer> => {
    try {
      const answer = await askOk({ op: 'search', q: query })
      return { results: answer.results ?? [], notice: answer.notice ?? null, detail: null }
    } catch (error) {
      if (error instanceof ApiError && (error.code === 'no_answer' || error.code === 'expired')) {
        throw new ApiError('search_no_answer', error.detail)
      }
      throw error
    }
  },
}
