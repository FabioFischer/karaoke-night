// The public routes of docs/PLAN.md section 5.9, as the phone uses them.

import {
  ApiError,
  request,
  type Entry,
  type Lyrics,
  type SearchAnswer,
  type SessionState,
  type Song,
} from '../api'

const ANSWER_POLL_MS = 1000
const ANSWER_TIMEOUT_MS = 15_000

/** The song's position at one instant, in the store's clock. */
export interface Anchor {
  position_s: number
  at_ms: number
}

export interface PublicSession {
  state: SessionState
  entry_id: string | null
  duration_s: number | null
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
  | { op: 'search'; q: string }

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Leaves a request for the Stage and waits for its answer, polling every
 * second. The request may still be applied after the phone gives up.
 */
async function ask(body: PublicRequest): Promise<Answer> {
  const { id } = await request<{ id: string }>('POST', '/api/public/requests', body)
  const deadline = Date.now() + ANSWER_TIMEOUT_MS
  while (Date.now() < deadline) {
    await sleep(ANSWER_POLL_MS)
    try {
      return await request<Answer>('GET', `/api/public/requests/${encodeURIComponent(id)}`)
    } catch (error) {
      // No answer yet, or a poll lost on the way: the request is in the store, so keep asking.
      if (error instanceof ApiError && (error.code === 'pending' || error.code === 'network')) continue
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
    const answer = await request<StateAnswer>('GET', '/api/public/state')
    return 'published_at_ms' in answer ? answer : null
  },
  library: () => request<{ songs: Song[] }>('GET', '/api/public/library'),
  time: () => request<{ now_ms: number }>('GET', '/api/public/time'),
  add: (song: Song) => askOk({ op: 'add', song }),
  remove: (entryId: string) => askOk({ op: 'remove', entry_id: entryId }),
  search: async (query: string): Promise<SearchAnswer> => {
    const answer = await askOk({ op: 'search', q: query })
    return { results: answer.results ?? [], notice: answer.notice ?? null, detail: null }
  },
}
