// A fake of Upstash's REST API, in memory: the fetch the public functions see in the tests.

export const STORE_URL = 'http://store.test'
export const STORE_TOKEN = 'test-token'

export interface FakeUpstash {
  values: Map<string, string>
  lists: Map<string, string[]>
  /** Every command received, in order. */
  commands: string[][]
  /** The store's clock in milliseconds, for TIME. */
  timeMs: number
  /** When set, every call fails as a store that does not answer. */
  down: boolean
  fetch: (url: string | URL | Request, init?: RequestInit) => Promise<Response>
}

function answer(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function fakeUpstash(): FakeUpstash {
  const store: FakeUpstash = {
    values: new Map(),
    lists: new Map(),
    commands: [],
    timeMs: 1_700_000_000_123,
    down: false,
    fetch: async (url, init) => {
      if (store.down) throw new TypeError('fetch failed')
      if (String(url) !== STORE_URL || init?.method !== 'POST') return answer(404, { error: 'not found' })
      const headers = new Headers(init.headers)
      if (headers.get('Authorization') !== `Bearer ${STORE_TOKEN}`) {
        return answer(401, { error: 'Unauthorized' })
      }
      const command = JSON.parse(String(init.body)) as string[]
      store.commands.push(command)
      const [name, key, ...rest] = command
      switch (name) {
        case 'GET':
          return answer(200, { result: store.values.get(key) ?? null })
        case 'RPUSH': {
          const list = store.lists.get(key) ?? []
          list.push(...rest)
          store.lists.set(key, list)
          return answer(200, { result: list.length })
        }
        case 'TIME': {
          const seconds = Math.floor(store.timeMs / 1000)
          const microseconds = (store.timeMs % 1000) * 1000 + 456
          return answer(200, { result: [String(seconds), String(microseconds)] })
        }
        default:
          return answer(400, { error: `ERR unknown command '${name}'` })
      }
    },
  }
  return store
}
