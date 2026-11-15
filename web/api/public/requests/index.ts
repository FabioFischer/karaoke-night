// POST /api/public/requests: leaves a request of the public for the Stage
// (docs/PLAN.md section 5.9). Only the contract's fields are stored, with the
// time it arrived, so the Stage can refuse it as expired; the Stage checks the
// rest as its own API would.

import { invalidRequest, storeUnavailable, uncachedJson } from '../../../server/responses.js'
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

/** The request as JSON text, or null for one nested too deep to write back. */
function serialize(stored: object): string | null {
  try {
    return JSON.stringify(stored)
  } catch {
    return null
  }
}

export async function POST(request: Request): Promise<Response> {
  const body: unknown = await request.json().catch(() => null)
  const read = readRequest(body)
  if (read === null) return invalidRequest()
  const id = crypto.randomUUID()
  const stored = serialize({ id, ...read, at_ms: Date.now() })
  if (stored === null) return invalidRequest()

  try {
    await command('RPUSH', keys.requests, stored)
  } catch (error) {
    if (error instanceof StoreError) return storeUnavailable(error)
    throw error
  }
  return uncachedJson(JSON.stringify({ id }), 202)
}
