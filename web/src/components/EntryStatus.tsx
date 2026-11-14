import type { Entry } from '../api'
import { entryStateText, errorMessage } from '../text'

/** How far an entry is from playable, its warning if any, and the English detail. */
export function EntryStatus({ entry }: { entry: Entry }) {
  return (
    <div className={`entry-status entry-${entry.state}`}>
      <span>{entryStateText(entry)}</span>
      {entry.error === 'studio_unreachable' && (
        <span className="entry-warning">{errorMessage(entry.error)}</span>
      )}
      {entry.detail && <span className="detail">{entry.detail}</span>}
    </div>
  )
}
