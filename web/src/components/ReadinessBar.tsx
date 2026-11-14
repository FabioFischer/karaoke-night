import type { Entry, EntryState } from '../api'
import { EntryStatus } from './EntryStatus'

// A rough share of a new song's wait, reached when each state begins. The
// separation takes most of it, and S2 measured its steps: vocals and karaoke
// about 40% each, the instruments about 12%.
const REACHED: Record<EntryState, number> = {
  queued: 0,
  starting: 0.03,
  downloading: 0.06,
  separating: 0.1,
  lyrics: 0.82,
  packaging: 0.85,
  fetching: 0.88,
  ready: 1,
  failed: 0,
}
const SEPARATION_STEPS: Record<string, number> = { vocals: 0.1, karaoke: 0.45, instruments: 0.75 }

function reached(entry: Entry): number {
  if (entry.state === 'separating' && entry.step) {
    return SEPARATION_STEPS[entry.step] ?? REACHED.separating
  }
  if (entry.state === 'fetching' && entry.progress && entry.progress.files_total > 0) {
    const share = entry.progress.files_done / entry.progress.files_total
    return REACHED.fetching + (1 - REACHED.fetching) * share
  }
  return REACHED[entry.state]
}

/** The way to a playable song: a bar and the entry's state. */
export function ReadinessBar({ entry }: { entry: Entry }) {
  const percent = Math.round(reached(entry) * 100)
  return (
    <div className="readiness">
      {entry.state !== 'failed' && (
        <div className="progress" role="progressbar" aria-valuenow={percent}>
          <div className="progress-fill" style={{ width: `${percent}%` }} />
        </div>
      )}
      <EntryStatus entry={entry} />
    </div>
  )
}
