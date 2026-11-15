// GET /api/public/state: the snapshot the Stage published (docs/PLAN.md section 5.9).

import { cachedJson, storeUnavailable } from '../../server/responses.js'
import { StoreError, command, keys } from '../../server/store.js'

const NO_SNAPSHOT = JSON.stringify({ version: 1, snapshot: null })

export async function GET(): Promise<Response> {
  try {
    const stored = await command('GET', keys.snapshot)
    return cachedJson(typeof stored === 'string' ? stored : NO_SNAPSHOT, 1)
  } catch (error) {
    if (error instanceof StoreError) return storeUnavailable(error)
    throw error
  }
}
