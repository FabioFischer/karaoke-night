// The phone's clock rules of docs/PLAN.md section 5.9: the offset, the position and offline.

import { describe, expect, it } from 'vitest'
import { anchoredPosition, clockOffset, isOffline } from '../src/public/phone-clock'

describe('the clock offset', () => {
  it('compares the store time with the middle of the round trip', () => {
    expect(clockOffset(100_000, 39_000, 41_000)).toBe(60_000)
    expect(clockOffset(40_000, 99_000, 101_000)).toBe(-60_000)
  })
})

describe('the position while playing', () => {
  const playing = {
    state: 'playing' as const,
    entry_id: 'e1',
    duration_s: 95,
    anchor: { position_s: 30, at_ms: 1_000_000 },
  }

  it('moves on from the anchor with the corrected clock', () => {
    expect(anchoredPosition(playing, 1_000_000)).toBe(30)
    expect(anchoredPosition(playing, 1_002_500)).toBe(32.5)
  })

  it('never goes past the duration', () => {
    expect(anchoredPosition(playing, 1_000_000 + 600_000)).toBe(95)
  })

  it('is unbounded without a duration', () => {
    expect(anchoredPosition({ ...playing, duration_s: null }, 1_100_000)).toBe(130)
  })

  it('is null without an anchor or outside playing', () => {
    expect(anchoredPosition({ ...playing, anchor: null }, 1_000_000)).toBeNull()
    expect(anchoredPosition({ ...playing, state: 'results' }, 1_000_000)).toBeNull()
  })
})

describe('offline', () => {
  it('is offline without a snapshot', () => {
    expect(isOffline(null, 1_000_000)).toBe(true)
  })

  it('is offline once the snapshot is more than 10 s old in the corrected clock', () => {
    expect(isOffline(1_000_000, 1_010_000)).toBe(false)
    expect(isOffline(1_000_000, 1_010_001)).toBe(true)
  })
})
