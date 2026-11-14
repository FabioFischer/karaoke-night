// The answers of the public functions: JSON text, with the caching each route allows.

import type { StoreError } from './store.js'

/** Vercel's own CDN header; Vercel consumes it, so browsers never see it. */
const EDGE_CACHE = 'Vercel-CDN-Cache-Control'
/** Vercel's default for functions: the browser asks again every time. */
const BROWSER_REVALIDATES = 'public, max-age=0, must-revalidate'

/** JSON text cached at Vercel's edge for the given seconds, never in the browser. */
export function cachedJson(text: string, edgeSeconds: number): Response {
  return new Response(text, {
    headers: {
      'Content-Type': 'application/json',
      [EDGE_CACHE]: `max-age=${edgeSeconds}`,
      'Cache-Control': BROWSER_REVALIDATES,
    },
  })
}

/** JSON text cached nowhere. */
export function uncachedJson(text: string, status = 200): Response {
  return new Response(text, {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  })
}

/** The store failed: logged for Vercel's logs, 503 for the phone. */
export function storeUnavailable(error: StoreError): Response {
  console.error(error.message)
  return uncachedJson(JSON.stringify({ error: 'store_unavailable' }), 503)
}
