import type { Entry, MoveBy } from '../api'
import { useAction, useCalls } from '../calls'
import type { MoveOptions } from '../session'
import { text } from '../text'
import { EntryStatus } from './EntryStatus'
import { ErrorNote } from './ErrorNote'

interface Props {
  entry: Entry
  /** Null for an entry that never moves: the song of the session. */
  moves: MoveOptions | null
}

/** One song of the playlist: title, artist, its state, up and down when it can move, and remove. */
export function PlaylistItem({ entry, moves }: Props) {
  const calls = useCalls()
  const remove = useAction()
  const move = useAction()
  const failure = remove.error ?? move.error

  function moveBy(by: MoveBy) {
    move.run(() => calls.move(entry.id, by))
  }

  return (
    <li className="playlist-item">
      <div className="playlist-item-song">
        <strong>{entry.song.title}</strong>
        <span>{entry.song.artist}</span>
        <EntryStatus entry={entry} />
      </div>
      <div className="playlist-item-actions">
        {moves && (
          <>
            <button
              type="button"
              className="button-quiet"
              aria-label={text.home.moveUp}
              title={text.home.moveUp}
              disabled={move.busy || !moves.up}
              onClick={() => moveBy(-1)}
            >
              {text.home.up}
            </button>
            <button
              type="button"
              className="button-quiet"
              aria-label={text.home.moveDown}
              title={text.home.moveDown}
              disabled={move.busy || !moves.down}
              onClick={() => moveBy(1)}
            >
              {text.home.down}
            </button>
          </>
        )}
        <button
          type="button"
          className="button-quiet"
          disabled={remove.busy}
          onClick={() => remove.run(() => calls.remove(entry.id))}
        >
          {text.home.remove}
        </button>
      </div>
      {failure && <ErrorNote code={failure.code} detail={failure.detail} />}
    </li>
  )
}
