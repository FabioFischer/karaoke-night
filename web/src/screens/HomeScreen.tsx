import { Link } from 'react-router'
import type { StageState } from '../api'
import { EntryStatus } from '../components/EntryStatus'
import { PlaylistItem } from '../components/PlaylistItem'
import { SongFinder } from '../components/SongFinder'
import { currentEntry, upcomingEntries } from '../session'
import { text } from '../text'

/** /karaoke: the current song, the search and the library, and the upcoming songs on the right. */
export function HomeScreen({ state }: { state: StageState }) {
  return (
    <div className="home">
      <main className="home-main">
        <CurrentSong state={state} />
        <SongFinder />
      </main>
      <aside className="home-upcoming">
        <h2>{text.home.upcoming}</h2>
        <UpcomingList state={state} />
      </aside>
    </div>
  )
}

const HEADINGS = {
  setup: text.home.current,
  playing: text.home.nowPlaying,
  results: text.home.ended,
}

function CurrentSong({ state }: { state: StageState }) {
  const entry = currentEntry(state)
  if (entry === null) {
    return (
      <section className="current-song">
        <p className="muted">{text.home.emptyPlaylist}</p>
      </section>
    )
  }
  return (
    <section className="current-song">
      <p className="eyebrow">{HEADINGS[state.session.state]}</p>
      <h1>{entry.song.title}</h1>
      <p className="artist">{entry.song.artist}</p>
      {state.session.state === 'setup' && <EntryStatus entry={entry} />}
      <Link className="button button-primary" to="/karaoke/stage">
        {text.home.goToStage}
      </Link>
    </section>
  )
}

function UpcomingList({ state }: { state: StageState }) {
  const upcoming = upcomingEntries(state)
  if (upcoming.length === 0) return <p className="muted">{text.home.noUpcoming}</p>
  return (
    <ul className="playlist">
      {upcoming.map((entry) => (
        <PlaylistItem key={entry.id} entry={entry} />
      ))}
    </ul>
  )
}
