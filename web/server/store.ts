// The store of docs/PLAN.md section 5.9: Upstash Redis, reached through its
// REST API. This is the only module that reads the store's credentials. It
// lives outside api/ because Vercel turns every file in api/ into a function.

export const keys = {
  snapshot: 'karaoke:snapshot',
  library: 'karaoke:library',
  requests: 'karaoke:requests',
  answer: (id: string) => `karaoke:answer:${id}`,
}

/** The store could not be asked: missing credentials, no answer or an error answer. */
export class StoreError extends Error {}

/** Runs one Redis command and gives its result, as Upstash's REST API answers it. */
export async function command(...args: string[]): Promise<unknown> {
  const url = process.env.KARAOKE_STORE_URL
  const token = process.env.KARAOKE_STORE_TOKEN
  if (!url || !token) {
    throw new StoreError('KARAOKE_STORE_URL and KARAOKE_STORE_TOKEN are not set')
  }
  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify(args),
    })
  } catch (error) {
    throw new StoreError(`${args[0]}: ${error instanceof Error ? error.message : String(error)}`)
  }
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok || typeof payload !== 'object' || payload === null || !('result' in payload)) {
    throw new StoreError(`${args[0]}: HTTP ${response.status} ${JSON.stringify(payload)}`)
  }
  return payload.result
}
