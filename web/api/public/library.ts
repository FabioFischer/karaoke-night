// GET /api/public/library: the library listing the Stage published (docs/PLAN.md section 5.9).

import { cachedJson, storeUnavailable } from '../../server/responses.js'
import { StoreError, command, keys } from '../../server/store.js'

const NO_LIBRARY = JSON.stringify({ songs: [] })

export async function GET(): Promise<Response> {
  try {
    const stored = await command('GET', keys.library)
    return cachedJson(typeof stored === 'string' ? stored : NO_LIBRARY, 5)
  } catch (error) {
    if (error instanceof StoreError) return storeUnavailable(error)
    throw error
  }
}
