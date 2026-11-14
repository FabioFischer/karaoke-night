import { api, type StageState } from '../api'
import { currentEntry, formatTime, sessionFailure } from '../session'
import { useAction } from '../stage-state'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'
import { LyricsView } from './LyricsView'
import { useSongPosition } from './useSongPosition'

/** Stage mode while the song plays: the lyrics, then a bar with stop and the time. */
export function PlayingView({ state }: { state: StageState }) {
  const { session } = state
  const entry = currentEntry(state)
  const position = useSongPosition(session.position_s, session.state === 'playing')
  const stop = useAction()

  const failure = stop.error ?? sessionFailure(session)
  return (
    <div className="screen-with-bar">
      <main className="playing">
        <LyricsView
          songId={entry?.song.song_id ?? null}
          position={position}
          duration={session.duration_s}
        />
      </main>
      <footer className="bottom-bar playing-bar">
        <button
          type="button"
          className="button-stop"
          disabled={stop.busy}
          onClick={() => stop.run(() => api.act('stop'))}
        >
          {stop.busy ? text.playing.stopping : text.playing.stop}
        </button>
        <div className="playing-song">
          <strong>{entry?.song.title}</strong>
          <span>{entry?.song.artist}</span>
        </div>
        <span className="playing-time">
          {formatTime(position)}
          {text.playing.timeOf}
          {formatTime(session.duration_s)}
        </span>
        {failure && <ErrorNote code={failure.code} detail={failure.detail} />}
      </footer>
    </div>
  )
}
