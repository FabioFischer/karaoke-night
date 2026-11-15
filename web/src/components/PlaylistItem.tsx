import type { Entry } from '../api'
import { useAction, useCalls } from '../calls'
import { text } from '../text'
import { EntryStatus } from './EntryStatus'
import { ErrorNote } from './ErrorNote'

/** One upcoming song: title, artist, its state and a remove button. */
export function PlaylistItem({ entry }: { entry: Entry }) {
  const calls = useCalls()
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
        onClick={() => remove.run(() => calls.remove(entry.id))}
      >
        {text.home.remove}
      </button>
      {remove.error && <ErrorNote code={remove.error.code} detail={remove.error.detail} />}
    </li>
  )
}
