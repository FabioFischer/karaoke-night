// The public functions of docs/PLAN.md section 5.9, called as Vercel calls
// them, against a fake Upstash.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GET as getLibrary } from '../api/public/library.js'
import { GET as getAnswer } from '../api/public/requests/[id].js'
import { POST as postRequest } from '../api/public/requests/index.js'
import { GET as getState } from '../api/public/state.js'
import { GET as getTime } from '../api/public/time.js'
import { STORE_TOKEN, STORE_URL, fakeUpstash, type FakeUpstash } from './fake-upstash'

const SONG = {
  title: 'Placeholder Song',
  artist: 'Placeholder Artist',
  origin: 'library',
  video_id: 'abc123',
  album: null,
  duration_s: 200,
  thumbnail_url: null,
  song_id: 'placeholder-song--placeholder-artist--a1b2c3',
}

let store: FakeUpstash

beforeEach(() => {
  store = fakeUpstash()
  vi.stubGlobal('fetch', store.fetch)
  vi.stubEnv('KARAOKE_STORE_URL', STORE_URL)
  vi.stubEnv('KARAOKE_STORE_TOKEN', STORE_TOKEN)
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})

function post(body: string): Promise<Response> {
  return postRequest(
    new Request('https://karaoke.test/api/public/requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    }),
  )
}

function answerFor(id: string): Promise<Response> {
  return getAnswer(new Request(`https://karaoke.test/api/public/requests/${id}`))
}

function storedRequests(): Record<string, unknown>[] {
  return (store.lists.get('karaoke:requests') ?? []).map((text) => JSON.parse(text))
}

async function expectUncached(response: Response, status: number, body: string) {
  expect(response.status).toBe(status)
  expect(await response.text()).toBe(body)
  expect(response.headers.get('Content-Type')).toBe('application/json')
  expect(response.headers.get('Cache-Control')).toBe('no-store')
  expect(response.headers.get('Vercel-CDN-Cache-Control')).toBeNull()
}

describe('GET /api/public/state', () => {
  it('answers the empty snapshot before the first one', async () => {
    const response = await getState()
    expect(response.status).toBe(200)
    expect(await response.text()).toBe('{"version":1,"snapshot":null}')
  })

  it('answers the snapshot as stored, cached 1 s at the edge only', async () => {
    const snapshot = '{"version":1,"published_at_ms":5,"session":null}'
    store.values.set('karaoke:snapshot', snapshot)
    const response = await getState()
    expect(await response.text()).toBe(snapshot)
    expect(response.headers.get('Vercel-CDN-Cache-Control')).toBe('max-age=1')
    expect(response.headers.get('Cache-Control')).toBe('public, max-age=0, must-revalidate')
  })
})

describe('GET /api/public/library', () => {
  it('answers no songs before the first listing', async () => {
    expect(await (await getLibrary()).text()).toBe('{"songs":[]}')
  })

  it('answers the listing as stored, cached 5 s at the edge only', async () => {
    const library = JSON.stringify({ published_at_ms: 3, songs: [SONG] })
    store.values.set('karaoke:library', library)
    const response = await getLibrary()
    expect(await response.text()).toBe(library)
    expect(response.headers.get('Vercel-CDN-Cache-Control')).toBe('max-age=5')
    expect(response.headers.get('Cache-Control')).toBe('public, max-age=0, must-revalidate')
  })
})

describe('GET /api/public/time', () => {
  it('answers the store clock in whole milliseconds, never cached', async () => {
    await expectUncached(await getTime(), 200, `{"now_ms":${store.timeMs}}`)
    expect(store.commands).toEqual([['TIME']])
  })
})

describe('POST /api/public/requests', () => {
  it('stores each request with an id and only the contract fields', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(1_800_000_000_000)
    const add = await post(JSON.stringify({ op: 'add', song: SONG, extra: 'dropped' }))
    const remove = await post(JSON.stringify({ op: 'remove', entry_id: 'e1', song: SONG }))
    const search = await post(JSON.stringify({ op: 'search', q: 'some words' }))
    vi.useRealTimers()

    const ids = []
    for (const response of [add, remove, search]) {
      expect(response.status).toBe(202)
      expect(response.headers.get('Cache-Control')).toBe('no-store')
      ids.push(((await response.json()) as { id: string }).id)
    }
    expect(new Set(ids).size).toBe(3)
    const at_ms = 1_800_000_000_000
    expect(storedRequests()).toEqual([
      { id: ids[0], op: 'add', song: SONG, at_ms },
      { id: ids[1], op: 'remove', entry_id: 'e1', at_ms },
      { id: ids[2], op: 'search', q: 'some words', at_ms },
    ])
    expect(store.commands.every(([name, key]) => name === 'RPUSH' && key === 'karaoke:requests')).toBe(true)
  })

  it.each([
    ['not JSON', '{oops'],
    ['an empty body', ''],
    ['an array', '[1]'],
    ['null', 'null'],
    ['an unknown op', '{"op":"start"}'],
    ['an add without a song', '{"op":"add"}'],
    ['an add with a song that is an array', '{"op":"add","song":[]}'],
    ['a remove with a number id', '{"op":"remove","entry_id":3}'],
    ['a search without text', '{"op":"search"}'],
    ['a song nested too deep to store', `{"op":"add","song":${'{"a":'.repeat(200_000)}1${'}'.repeat(200_000)}}`],
  ])('refuses %s with 422 and stores nothing', async (_name, body) => {
    await expectUncached(await post(body), 422, '{"error":"invalid_request"}')
    expect(store.lists.has('karaoke:requests')).toBe(false)
  })
})

describe('GET /api/public/requests/{id}', () => {
  it('answers pending until the Stage wrote the answer, then the answer as stored', async () => {
    await expectUncached(await answerFor('r1'), 404, '{"error":"pending"}')
    store.values.set('karaoke:answer:r1', '{"ok":true,"entry_id":"e9"}')
    await expectUncached(await answerFor('r1'), 200, '{"ok":true,"entry_id":"e9"}')
  })

  it('decodes the id from the path', async () => {
    await answerFor('a%20b')
    expect(store.commands).toEqual([['GET', 'karaoke:answer:a b']])
  })

  it('refuses an id that is not valid percent encoding with 422', async () => {
    await expectUncached(await answerFor('%E0%A4%A'), 422, '{"error":"invalid_request"}')
    expect(store.commands).toEqual([])
  })
})

describe('a store that fails', () => {
  const routes: [string, () => Promise<Response>][] = [
    ['state', () => getState()],
    ['library', () => getLibrary()],
    ['time', () => getTime()],
    ['requests', () => post('{"op":"search","q":"x"}')],
    ['requests/{id}', () => answerFor('r1')],
  ]

  it.each(routes)('answers 503 on %s without credentials', async (_name, call) => {
    vi.stubEnv('KARAOKE_STORE_TOKEN', '')
    await expectUncached(await call(), 503, '{"error":"store_unavailable"}')
  })

  it.each(routes)('answers 503 on %s when the store refuses the token', async (_name, call) => {
    vi.stubEnv('KARAOKE_STORE_TOKEN', 'wrong')
    await expectUncached(await call(), 503, '{"error":"store_unavailable"}')
  })

  it.each(routes)('answers 503 on %s when the store does not answer', async (_name, call) => {
    store.down = true
    await expectUncached(await call(), 503, '{"error":"store_unavailable"}')
  })
})
