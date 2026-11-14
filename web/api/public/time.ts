// GET /api/public/time: the store's clock, which the phone corrects its own against
// (docs/PLAN.md section 5.9).

import { storeUnavailable, uncachedJson } from '../../server/responses.js'
import { StoreError, command } from '../../server/store.js'

export async function GET(): Promise<Response> {
  try {
    // Redis TIME answers the seconds and the microseconds, as strings.
    const [seconds, microseconds] = (await command('TIME')) as [string, string]
    const nowMs = Number(seconds) * 1000 + Math.floor(Number(microseconds) / 1000)
    return uncachedJson(JSON.stringify({ now_ms: nowMs }))
  } catch (error) {
    if (error instanceof StoreError) return storeUnavailable(error)
    throw error
  }
}
