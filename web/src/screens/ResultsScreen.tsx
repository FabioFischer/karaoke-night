import { useNavigate } from 'react-router'
import { api, type StageState } from '../api'
import { EntryStatus } from '../components/EntryStatus'
import { ErrorNote } from '../components/ErrorNote'
import { sessionFailure, upcomingEntries } from '../session'
import { useAction } from '../calls'
import { text } from '../text'

/** /karaoke/stage/results: the end of a performance and the way to the next song. */
export function ResultsScreen({ state }: { state: StageState }) {
  const next = upcomingEntries(state)[0] ?? null
  const action = useAction()
  const navigate = useNavigate()
  const { session } = state
  // A stop that REAPER refused still ends in the results, with its error.
  const failure = action.error ?? sessionFailure(session)

  async function backHome() {
    // setup_next takes the song played off the playlist and leaves the results.
    if (await action.run(() => api.act('setup_next'))) navigate('/karaoke')
  }

  return (
    <main className="results">
      <h1>{text.results.ended}</h1>
      {next === null ? (
        <section className="results-next">
          <p className="muted">{text.results.noNext}</p>
          <div className="results-actions">
            <button
              type="button"
              className="button-primary"
              disabled={action.busy}
              onClick={backHome}
            >
              {text.results.backHome}
            </button>
          </div>
        </section>
      ) : (
        <section className="results-next">
          <p className="eyebrow">{text.results.next}</p>
          <h2>{next.song.title}</h2>
          <p className="artist">{next.song.artist}</p>
          {next.state !== 'ready' && <EntryStatus entry={next} />}
          <div className="results-actions">
            <button
              type="button"
              className="button-primary"
              disabled={action.busy || next.state !== 'ready'}
              onClick={() => action.run(() => api.act('play_next'))}
            >
              {text.results.playNow}
            </button>
            <button
              type="button"
              disabled={action.busy}
              onClick={() => action.run(() => api.act('setup_next'))}
            >
              {text.results.setupFirst}
            </button>
          </div>
        </section>
      )}
      {failure && <ErrorNote code={failure.code} detail={failure.detail} />}
    </main>
  )
}
