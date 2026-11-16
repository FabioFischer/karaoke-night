import { api, type StageState } from '../api'
import { currentEntry, formatTime, sessionFailure } from '../session'
import { useAction } from '../calls'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'
import { LyricsView } from './LyricsView'
import { useSongPosition } from './useSongPosition'
import { VolumeControl } from './VolumeControl'

/** Stage mode while the song plays: the lyrics, then a bar with pause, skip, the time and the volumes. */
export function PlayingView({ state }: { state: StageState }) {
  const { session } = state
  const entry = currentEntry(state)
  // While paused the position stays where it paused, so the lyrics hold still.
  const position = useSongPosition(session.position_s, session.state === 'playing' && !session.paused)
  const stop = useAction()
  const pause = useAction()
  const volumes = useAction()

  function pauseLabel(): string {
    if (pause.busy) return session.paused ? text.playing.resuming : text.playing.pausing
    return session.paused ? text.playing.resume : text.playing.pause
  }

  const failure = stop.error ?? pause.error ?? volumes.error ?? sessionFailure(session)
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
          className="button-pause"
          aria-pressed={session.paused}
          disabled={pause.busy}
          onClick={() => pause.run(() => api.act(session.paused ? 'resume' : 'pause'))}
        >
          {pauseLabel()}
        </button>
        {/* The skip of a song that plays: it stops the song and shows the results. */}
        <button
          type="button"
          className="button-stop"
          disabled={stop.busy}
          onClick={() => stop.run(() => api.act('stop'))}
        >
          {stop.busy ? text.playing.skipping : text.playing.skip}
        </button>
        <div className="playing-song">
          <strong>{entry?.song.title}</strong>
          <span>{entry?.song.artist}</span>
        </div>
        <VolumeControl
          volumes={session.volumes}
          disabled={volumes.busy}
          onChange={(next) => volumes.run(() => api.setVolumes(next))}
        />
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
