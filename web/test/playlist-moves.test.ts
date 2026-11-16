// Which entries the playlist offers to move, on the TV and on a phone (docs/PLAN.md section 5.8, Reordering).

import { describe, expect, it } from 'vitest'
import type { Entry } from '../src/api'
import { moveOptions, type PlaylistView } from '../src/session'

function entry(id: string): Entry {
  return {
    id,
    state: 'ready',
    step: null,
    progress: null,
    error: null,
    detail: null,
    song: {
      title: 'Placeholder Song',
      artist: 'Placeholder Artist',
      origin: 'library',
      video_id: null,
      album: null,
      duration_s: 200,
      thumbnail_url: null,
      song_id: `placeholder-${id}`,
    },
  }
}

function view(state: 'setup' | 'playing' | 'results', ids: string[]): PlaylistView {
  return { session: { state, entry_id: ids[0] ?? null }, playlist: ids.map(entry) }
}

describe('moving an entry', () => {
  it.each(['setup', 'playing', 'results'] as const)(
    'never offers to move the session entry in %s',
    (state) => {
      expect(moveOptions(view(state, ['e1', 'e2', 'e3']), 'e1')).toBeNull()
    },
  )

  it('offers only down right behind the session entry, and only up at the end', () => {
    const state = view('playing', ['e1', 'e2', 'e3', 'e4'])
    expect(moveOptions(state, 'e2')).toEqual({ up: false, down: true })
    expect(moveOptions(state, 'e3')).toEqual({ up: true, down: true })
    expect(moveOptions(state, 'e4')).toEqual({ up: true, down: false })
  })

  it('offers neither way to the only entry behind the session entry', () => {
    expect(moveOptions(view('setup', ['e1', 'e2']), 'e2')).toEqual({ up: false, down: false })
  })

  it('offers nothing for an entry no longer in the playlist', () => {
    expect(moveOptions(view('setup', ['e1', 'e2']), 'gone')).toBeNull()
  })
})
