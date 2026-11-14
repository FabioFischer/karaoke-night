import { api, type Entry } from '../api'
import { useAction } from '../stage-state'
import { text } from '../text'
import { EntryStatus } from './EntryStatus'
import { ErrorNote } from './ErrorNote'

/** One upcoming song: title, artist, its state and a remove button. */
export function PlaylistItem({ entry }: { entry: Entry }) {
  const remove = useAction()
  return (
    <li className="playlist-item">
      <div className="playlist-item-song">
        <strong>{entry.song.title}</strong>
        <span>{entry.song.artist}</span>
        <EntryStatus entry={entry} />
      </div>
      <button
        type="button"
        className="button-quiet"
        disabled={remove.busy}
        onClick={() => remove.run(() => api.remove(entry.id))}
      >
        {text.home.remove}
      </button>
      {remove.error && <ErrorNote code={remove.error.code} detail={remove.error.detail} />}
    </li>
  )
}
