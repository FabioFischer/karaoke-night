import { Link } from 'react-router'
import { api, type StageState } from '../api'
import { currentEntry, listedEntries, sessionFailure } from '../session'
import { useAction } from '../calls'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'
import { KeyControl } from './KeyControl'
import { PlaylistBar } from './PlaylistBar'
import { ReadinessBar } from './ReadinessBar'
import { StemToggles } from './StemToggles'

/** Stage mode before the song: readiness, stems, key, the start button and the playlist. */
export function SetupView({ state }: { state: StageState }) {
  const { session } = state
  const entry = currentEntry(state)
  const settings = useAction()
  const start = useAction()

  function toggleStem(stem: string) {
    const muted = session.muted.includes(stem)
      ? session.muted.filter((name) => name !== stem)
      : [...session.muted, stem]
    settings.run(() => api.settings(muted, session.key))
  }

  function changeKey(key: number) {
    settings.run(() => api.settings(session.muted, key))
  }

  const failure = settings.error ?? start.error ?? sessionFailure(session)
  return (
    <div className="screen-with-bar">
      <main className="setup">
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
            {failure && <ErrorNote code={failure.code} detail={failure.detail} />}
            <button
              type="button"
              className="button-primary button-start"
              disabled={entry.state !== 'ready' || start.busy}
              onClick={() => start.run(() => api.act('start'))}
            >
              {start.busy ? text.setup.starting : text.setup.start}
            </button>
          </>
        )}
      </main>
      <PlaylistBar entries={listedEntries(state)} canSkip={entry !== null} />
    </div>
  )
}
