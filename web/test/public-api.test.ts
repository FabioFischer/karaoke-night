// The phone's calls to the public routes (src/public/public-api.ts), with a stubbed fetch.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Song } from '../src/api'
import { publicApi } from '../src/public/public-api'

const SONG: Song = {
  title: 'Placeholder Song',
  artist: 'Placeholder Artist',
  origin: 'ytmusic',
  video_id: 'abc123',
  album: null,
  duration_s: 200,
  thumbnail_url: null,
  song_id: null,
}

type Reply = () => Response | Promise<Response>

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fetch that serves the POST with an id, then each answer poll from the replies in order. */
function stubRequests(polls: Reply[]) {
  const calls: string[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`)
    if (init?.method === 'POST') return json(202, { id: 'r1' })
    const reply = polls.shift()
    if (reply === undefined) return json(404, { error: 'pending' })
    return reply()
  })
  return calls
}

/** Settles a promise into a value that can be checked while fake timers run. */
function settle<T>(promise: Promise<T>) {
  const outcome: { value?: T; error?: unknown; done: boolean } = { done: false }
  promise.then(
    (value) => Object.assign(outcome, { value, done: true }),
    (error) => Object.assign(outcome, { error, done: true }),
  )
  return outcome
}

const pending = () => json(404, { error: 'pending' })

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('a request and its answer', () => {
  it('polls every second until the answer arrives', async () => {
    const calls = stubRequests([pending, pending, () => json(200, { ok: true, entry_id: 'e7' })])
    const outcome = settle(publicApi.add(SONG))
    await vi.advanceTimersByTimeAsync(2_000)
    expect(outcome.done).toBe(false)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(outcome).toMatchObject({ done: true, value: { ok: true, entry_id: 'e7' } })
    expect(calls).toEqual([
      'POST /api/public/requests',
      'GET /api/public/requests/r1',
      'GET /api/public/requests/r1',
      'GET /api/public/requests/r1',
    ])
  })

  it('turns a refusal into the error code', async () => {
    stubRequests([() => json(200, { ok: false, error: 'not_addable' })])
    const outcome = settle(publicApi.add(SONG))
    await vi.advanceTimersByTimeAsync(1_000)
    expect(outcome.error).toMatchObject({ code: 'not_addable' })
  })

  it('gives up after 15 s with no_answer', async () => {
    const calls = stubRequests([])
    const outcome = settle(publicApi.remove('e1'))
    await vi.advanceTimersByTimeAsync(14_999)
    expect(outcome.done).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(outcome.error).toMatchObject({ code: 'no_answer' })
    expect(calls.filter((call) => call.startsWith('GET')).length).toBe(15)
  })

  it('keeps polling through a poll lost on the network', async () => {
    stubRequests([
      () => Promise.reject(new TypeError('Failed to fetch')),
      () => json(200, { ok: true }),
    ])
    const outcome = settle(publicApi.remove('e1'))
    await vi.advanceTimersByTimeAsync(2_000)
    expect(outcome).toMatchObject({ done: true, value: { ok: true } })
  })

  it('keeps polling through a store that failed for one poll', async () => {
    stubRequests([() => json(503, { error: 'store_unavailable' }), () => json(200, { ok: true })])
    const outcome = settle(publicApi.remove('e1'))
    await vi.advanceTimersByTimeAsync(2_000)
    expect(outcome).toMatchObject({ done: true, value: { ok: true } })
  })

  it('gives a search that is not answered its own code', async () => {
    stubRequests([])
    const outcome = settle(publicApi.search('words'))
    await vi.advanceTimersByTimeAsync(15_000)
    expect(outcome.error).toMatchObject({ code: 'search_no_answer' })
  })

  it('gives an expired search its own code', async () => {
    stubRequests([() => json(200, { ok: false, error: 'expired' })])
    const outcome = settle(publicApi.search('words'))
    await vi.advanceTimersByTimeAsync(1_000)
    expect(outcome.error).toMatchObject({ code: 'search_no_answer' })
  })

  it('answers a search as the TV search answers', async () => {
    stubRequests([() => json(200, { ok: true, results: [SONG], notice: 'offline' })])
    const outcome = settle(publicApi.search('words'))
    await vi.advanceTimersByTimeAsync(1_000)
    expect(outcome.value).toEqual({ results: [SONG], notice: 'offline', detail: null })
  })
})

describe('a fetch that never answers', () => {
  function stubHanging() {
    vi.stubGlobal(
      'fetch',
      (_url: string, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(init.signal?.reason))
        }),
    )
  }

  const calls: [string, () => Promise<unknown>][] = [
    ['the snapshot', () => publicApi.state()],
    ['the library', () => publicApi.library()],
    ['the time', () => publicApi.time()],
  ]

  it.each(calls)('fails %s as network after 5 s', async (_name, call) => {
    stubHanging()
    const outcome = settle(call())
    await vi.advanceTimersByTimeAsync(5_000)
    expect(outcome.error).toMatchObject({ code: 'network' })
  })

  it('still gives up on an answer after 15 s', async () => {
    let posted = false
    vi.stubGlobal('fetch', (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST' && !posted) {
        posted = true
        return Promise.resolve(json(202, { id: 'r1' }))
      }
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(init.signal?.reason))
      })
    })
    const outcome = settle(publicApi.remove('e1'))
    await vi.advanceTimersByTimeAsync(15_000)
    expect(outcome.error).toMatchObject({ code: 'no_answer' })
  })
})

describe('a request whose POST never answers', () => {
  /** The function stores the request, but its 202 arrives only after the phone gave up. */
  function stubLatePost() {
    vi.stubGlobal('fetch', (_url: string, init?: RequestInit) => {
      if (init?.method !== 'POST') return Promise.resolve(pending())
      return new Promise((resolve, reject) => {
        setTimeout(() => resolve(json(202, { id: 'r1' })), 5_500)
        init.signal?.addEventListener('abort', () => reject(init.signal?.reason))
      })
    })
  }

  it.each([
    ['an add', () => publicApi.add(SONG)],
    ['a remove', () => publicApi.remove('e1')],
  ])('gives %s up after 5 s as no_answer: it may be stored', async (_name, call) => {
    stubLatePost()
    const outcome = settle<unknown>(call())
    await vi.advanceTimersByTimeAsync(5_000)
    expect(outcome.error).toMatchObject({ code: 'no_answer' })
  })

  it('gives a search up after 5 s as network', async () => {
    stubLatePost()
    const outcome = settle(publicApi.search('words'))
    await vi.advanceTimersByTimeAsync(5_000)
    expect(outcome.error).toMatchObject({ code: 'network' })
  })
})

describe('an answer whose body is cut by the timeout', () => {
  /** A 200 whose body never ends, until the call is given up. */
  function stalledBody(init?: RequestInit): Response {
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"ok":'))
        init?.signal?.addEventListener('abort', () => controller.error(init.signal?.reason))
      },
    })
    return new Response(body, { status: 200 })
  }

  it('fails the snapshot as network, not as an empty answer', async () => {
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => stalledBody(init))
    const outcome = settle(publicApi.state())
    await vi.advanceTimersByTimeAsync(5_000)
    expect(outcome.error).toMatchObject({ code: 'network' })
  })

  it('keeps polling an answer through a cut body', async () => {
    let polls = 0
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') return json(202, { id: 'r1' })
      polls += 1
      return polls === 1 ? stalledBody(init) : json(200, { ok: true })
    })
    const outcome = settle(publicApi.remove('e1'))
    await vi.advanceTimersByTimeAsync(7_000)
    expect(outcome).toMatchObject({ done: true, value: { ok: true } })
  })
})

describe('the snapshot', () => {
  it('is null before the Stage published one', async () => {
    vi.stubGlobal('fetch', async () => json(200, { version: 1, snapshot: null }))
    expect(await publicApi.state()).toBeNull()
  })
})
