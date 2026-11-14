import type { StageState } from '../api'
import { PlayingView } from '../components/PlayingView'
import { SetupView } from '../components/SetupView'
import { text } from '../text'

/** /karaoke/stage: the setup, then the song playing. The results have their own route. */
export function StageScreen({ state }: { state: StageState }) {
  if (state.session.state === 'playing') return <PlayingView state={state} />
  if (state.session.state === 'setup') return <SetupView state={state} />
  return <p className="loading">{text.loading}</p>
}
