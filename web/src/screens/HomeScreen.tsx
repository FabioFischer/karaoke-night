import { Link } from 'react-router'
import type { StageState } from '../api'
import mascot from '../assets/mascot.png'
import { EntryStatus } from '../components/EntryStatus'
import { PlaylistItem } from '../components/PlaylistItem'
import { SongFinder } from '../components/SongFinder'
import { currentEntry, listedEntries, moveOptions } from '../session'
import { sessionHeadings, text } from '../text'

/** /karaoke: the current song, the search and the library, and the playlist on the right. */
export function HomeScreen({ state }: { state: StageState }) {
  return (
    <div className="home">
      <main className="home-main">
        <CurrentSong state={state} />
        <SongFinder />
      </main>
      <aside className="home-upcoming">
        <h2>{text.home.upcoming}</h2>
        <PlaylistPanel state={state} />
      </aside>
    </div>
  )
}

function CurrentSong({ state }: { state: StageState }) {
  const entry = currentEntry(state)
  if (entry === null) {
    return (
      <section className="empty-playlist">
        <div className="mascot-card">
          <img src={mascot} alt="" />
        </div>
        <div>
          <p className="empty-playlist-title">{text.home.emptyPlaylist}</p>
          <p className="muted">{text.home.emptyPlaylistHint}</p>
        </div>
      </section>
    )
  }
  return (
    <section className="current-song">
      <p className="eyebrow">{sessionHeadings[state.session.state]}</p>
      <h1>{entry.song.title}</h1>
      <p className="artist">{entry.song.artist}</p>
      {state.session.state === 'setup' && <EntryStatus entry={entry} />}
      <Link className="button button-primary" to="/karaoke/stage">
        {text.home.goToStage}
      </Link>
    </section>
  )
}

function PlaylistPanel({ state }: { state: StageState }) {
  const entries = listedEntries(state)
  if (entries.length === 0) return <p className="muted">{text.home.noUpcoming}</p>
  return (
    <ul className="playlist">
      {entries.map((entry) => (
        <PlaylistItem key={entry.id} entry={entry} moves={moveOptions(state, entry.id)} />
      ))}
    </ul>
  )
}
