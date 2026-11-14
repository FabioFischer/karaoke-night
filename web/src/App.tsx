import { useEffect, useRef, type ReactElement } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router'
import type { SessionState, StageState } from './api'
import { HomeScreen } from './screens/HomeScreen'
import { ResultsScreen } from './screens/ResultsScreen'
import { StageScreen } from './screens/StageScreen'
import { StageStateProvider, useStage } from './stage-state'
import { text } from './text'

const HOME = '/karaoke'
const STAGE = '/karaoke/stage'
const RESULTS = '/karaoke/stage/results'

function App() {
  return (
    <BrowserRouter>
      <StageStateProvider>
        <SessionFollower />
        <OfflineBanner />
        <Routes>
          <Route path={HOME} element={<WithState screen={HomeScreen} />} />
          <Route path={STAGE} element={<WithState screen={StageScreen} />} />
          <Route path={RESULTS} element={<WithState screen={ResultsScreen} />} />
          <Route path="*" element={<Navigate to={HOME} replace />} />
        </Routes>
      </StageStateProvider>
    </BrowserRouter>
  )
}

/**
 * Moves the screen with the session: a song that ends shows the results,
 * from any route; the stage route never sits on the results; and the results
 * route goes back to the stage once the session leaves the results.
 */
function SessionFollower() {
  const { state } = useStage()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const previous = useRef<SessionState | null>(null)
  const current = state?.session.state ?? null

  useEffect(() => {
    const ended = previous.current === 'playing' && current === 'results'
    previous.current = current
    if (current === null) return
    // Replace, not push: the back button must not lead to a screen the session left.
    if (current === 'results' && (ended || pathname === STAGE) && pathname !== RESULTS) {
      navigate(RESULTS, { replace: true })
    } else if (current !== 'results' && pathname === RESULTS) {
      navigate(STAGE, { replace: true })
    }
  }, [current, pathname, navigate])

  return null
}

function OfflineBanner() {
  const { offline } = useStage()
  if (!offline) return null
  return (
    <div className="offline-banner" role="alert">
      {text.offline}
    </div>
  )
}

/** A screen once the first state arrived. */
function WithState({ screen: Screen }: { screen: (props: { state: StageState }) => ReactElement }) {
  const { state } = useStage()
  if (state === null) return <p className="loading">{text.loading}</p>
  return <Screen state={state} />
}

export default App
