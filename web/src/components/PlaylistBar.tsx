import { api, type Entry } from '../api'
import { useAction } from '../calls'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'
import { PlaylistItem } from './PlaylistItem'

interface Props {
  entries: Entry[]
  canSkip: boolean
}

/** The bottom bar of the setup: the playlist, this song included, and the skip to the next. */
export function PlaylistBar({ entries, canSkip }: Props) {
  const skip = useAction()
  return (
    <footer className="bottom-bar playlist-bar">
      <div className="playlist-bar-head">
        <h2>{text.setup.playlist}</h2>
        <button
          type="button"
          disabled={!canSkip || skip.busy}
          onClick={() => skip.run(() => api.act('skip'))}
        >
          {text.setup.skip}
        </button>
      </div>
      {skip.error && <ErrorNote code={skip.error.code} detail={skip.error.detail} />}
      {entries.length === 0 ? (
        <p className="muted">{text.home.noUpcoming}</p>
      ) : (
        <ul className="playlist playlist-row">
          {entries.map((entry) => (
            <PlaylistItem key={entry.id} entry={entry} />
          ))}
        </ul>
      )}
    </footer>
  )
}
