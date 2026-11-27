import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, type StageState } from '../api'
import { currentEntry, sessionFailure } from '../session'
import { useAction } from '../calls'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'
import { KeyControl } from './KeyControl'
import { LyricsView } from './LyricsView'
import { PlaylistBar } from './PlaylistBar'
import { ReadinessBar } from './ReadinessBar'
import { StemToggles } from './StemToggles'
import { useSongPosition } from './useSongPosition'
import { VolumeControl } from './VolumeControl'

/**
 * Stage mode before the song: readiness, stems, key, volumes, the excerpt,
 * the start button and the playlist. While the excerpt plays, its lyrics
 * follow it beside the settings, which the Stage applies to it at once.
 */
export function SetupView({ state }: { state: StageState }) {
  const { session } = state
  const entry = currentEntry(state)
  const settings = useAction()
  const start = useAction()
  const volumes = useAction()
  const preview = useAction()
  const position = useSongPosition(session.position_s, session.previewing)
  // The view is keyed by the entry, so this is whether this song's excerpt played.
  const [heard, setHeard] = useState(false)

  useEffect(() => {
    if (session.previewing) setHeard(true)
  }, [session.previewing])

  function toggleStem(stem: string) {
    const muted = session.muted.includes(stem)
      ? session.muted.filter((name) => name !== stem)
      : [...session.muted, stem]
    settings.run(() => api.settings(muted, session.key))
  }

  function changeKey(key: number) {
    settings.run(() => api.settings(session.muted, key))
  }

  function previewLabel(): string {
    if (preview.busy) {
      return session.previewing ? text.setup.previewStopping : text.setup.previewStarting
    }
    if (session.previewing) return text.setup.previewStop
    return heard ? text.setup.previewAgain : text.setup.preview
  }

  const failure =
    settings.error ?? volumes.error ?? preview.error ?? start.error ?? sessionFailure(session)
  return (
    <div className="screen-with-bar">
      <main className={session.previewing ? 'setup setup-previewing' : 'setup'}>
        <div className="setup-controls">
          <nav>
            <Link to="/karaoke">{text.setup.backHome}</Link>
          </nav>
          {entry === null ? (
            <div className="setup-empty">
              <p>{text.setup.noSong}</p>
              <Link className="button button-primary" to="/karaoke">
                {text.setup.pickSong}
              </Link>
            </div>
          ) : (
            <>
              <p className="eyebrow">{text.setup.title}</p>
              <h1>{entry.song.title}</h1>
              <p className="artist">{entry.song.artist}</p>
              {entry.state !== 'ready' && <ReadinessBar entry={entry} />}
              <section>
                <h2>{text.setup.stems}</h2>
                {session.stems.length > 0 && <p className="muted">{text.setup.stemsHint}</p>}
                <StemToggles
                  stems={session.stems}
                  muted={session.muted}
                  disabled={settings.busy}
                  onToggle={toggleStem}
                />
              </section>
              <section>
                <h2>{text.setup.key}</h2>
                <KeyControl value={session.key} disabled={settings.busy} onChange={changeKey} />
              </section>
              <section>
                <h2>{text.setup.volumes}</h2>
                <VolumeControl
                  volumes={session.volumes}
                  disabled={volumes.busy}
                  onChange={(next) => volumes.run(() => api.setVolumes(next))}
                />
              </section>
              {failure && <ErrorNote code={failure.code} detail={failure.detail} />}
              <div className="setup-actions">
                <button
                  type="button"
                  className="button-primary button-start"
                  disabled={entry.state !== 'ready' || start.busy}
                  onClick={() => start.run(() => api.act('start'))}
                >
                  {start.busy ? text.setup.starting : text.setup.start}
                </button>
                <button
                  type="button"
                  className="button-preview"
                  aria-pressed={session.previewing}
                  disabled={entry.state !== 'ready' || preview.busy || start.busy}
                  onClick={() =>
                    preview.run(() => api.act(session.previewing ? 'preview/stop' : 'preview'))
                  }
                >
                  {previewLabel()}
                </button>
              </div>
              <p className="muted">{text.setup.previewHint}</p>
            </>
          )}
        </div>
        {session.previewing && entry !== null && (
          <div className="setup-lyrics">
            <LyricsView
              songId={entry.song.song_id}
              position={position}
              duration={session.duration_s}
            />
          </div>
        )}
      </main>
      <PlaylistBar state={state} canSkip={entry !== null} />
    </div>
  )
}
