// GET /api/public/requests/{id}: the Stage's answer to a request, once it wrote
// one (docs/PLAN.md section 5.9).

import { storeUnavailable, uncachedJson } from '../../../server/responses.js'
import { StoreError, command, keys } from '../../../server/store.js'

export async function GET(request: Request): Promise<Response> {
  // The id is the last segment of the path.
  const id = decodeURIComponent(new URL(request.url).pathname.split('/').pop() ?? '')
  try {
    const answer = await command('GET', keys.answer(id))
    if (typeof answer !== 'string') return uncachedJson(JSON.stringify({ error: 'pending' }), 404)
    return uncachedJson(answer)
  } catch (error) {
    if (error instanceof StoreError) return storeUnavailable(error)
    throw error
  }
}
