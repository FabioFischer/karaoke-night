import { api } from '../api'
import { useAction } from '../calls'
import { listedEntries, moveOptions, type PlaylistView } from '../session'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'
import { PlaylistItem } from './PlaylistItem'

interface Props {
  state: PlaylistView
  canSkip: boolean
}

/** The bottom bar of the setup: the playlist, this song included, and the skip to the next. */
export function PlaylistBar({ state, canSkip }: Props) {
  const skip = useAction()
  const entries = listedEntries(state)
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
            <PlaylistItem key={entry.id} entry={entry} moves={moveOptions(state, entry.id)} />
          ))}
        </ul>
      )}
    </footer>
  )
}
