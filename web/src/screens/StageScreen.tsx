import type { StageState } from '../api'
import { PlayingView } from '../components/PlayingView'
import { SetupView } from '../components/SetupView'
import { text } from '../text'

/** /karaoke/stage: the setup, then the song playing. The results have their own route. */
export function StageScreen({ state }: { state: StageState }) {
  if (state.session.state === 'playing') return <PlayingView state={state} />
  // Keyed by the entry, so the errors of a song never show on the next one.
  if (state.session.state === 'setup') {
    return <SetupView key={state.session.entry_id ?? ''} state={state} />
  }
  return <p className="loading">{text.loading}</p>
}
