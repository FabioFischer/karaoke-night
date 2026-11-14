// POST /api/public/requests: leaves a request of the public for the Stage
// (docs/PLAN.md section 5.9). Only the contract's fields are stored; the
// Stage checks the rest as its own API would.

import { storeUnavailable, uncachedJson } from '../../../server/responses.js'
import { StoreError, command, keys } from '../../../server/store.js'

type PublicRequest =
  | { op: 'add'; song: object }
  | { op: 'remove'; entry_id: string }
  | { op: 'search'; q: string }

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readRequest(body: unknown): PublicRequest | null {
  if (!isObject(body)) return null
  if (body.op === 'add' && isObject(body.song)) return { op: 'add', song: body.song }
  if (body.op === 'remove' && typeof body.entry_id === 'string') {
    return { op: 'remove', entry_id: body.entry_id }
  }
  if (body.op === 'search' && typeof body.q === 'string') return { op: 'search', q: body.q }
  return null
}

export async function POST(request: Request): Promise<Response> {
  const body: unknown = await request.json().catch(() => null)
  const read = readRequest(body)
  if (read === null) return uncachedJson(JSON.stringify({ error: 'invalid_request' }), 422)

  const id = crypto.randomUUID()
  try {
    await command('RPUSH', keys.requests, JSON.stringify({ id, ...read }))
  } catch (error) {
    if (error instanceof StoreError) return storeUnavailable(error)
    throw error
  }
  return uncachedJson(JSON.stringify({ id }), 202)
}
