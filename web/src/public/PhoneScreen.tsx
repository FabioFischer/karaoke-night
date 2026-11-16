import { useState } from 'react'
import type { Lyrics } from '../api'
import { EntryStatus } from '../components/EntryStatus'
import { LyricsText } from '../components/LyricsView'
import { PlaylistItem } from '../components/PlaylistItem'
import { LibraryView, SearchView } from '../components/SongFinder'
import { currentEntry, formatTime, listedEntries, moveOptions, upcomingEntries } from '../session'
import { sessionHeadings, text } from '../text'
import { usePublicState, type PhoneState } from './public-state'
import { useAnchoredPosition } from './useAnchoredPosition'

type Tab = 'now' | 'queue' | 'search'

const TABS: { tab: Tab; label: string }[] = [
  { tab: 'now', label: text.public.nowTab },
  { tab: 'queue', label: text.public.queueTab },
  { tab: 'search', label: text.public.searchTab },
]

const NO_LYRICS: Lyrics = { status: 'none', lines: [] }

/** /karaoke on a phone: three tabs at the bottom (docs/PLAN.md section 5.9). */
export function PhoneScreen() {
  const [tab, setTab] = useState<Tab>('now')
  const { state, loaded, offline } = usePublicState()

  return (
    <div className="phone">
      {offline && (
        <div className="offline-banner" role="alert">
          {state === null ? text.public.notLive : text.public.offline}
        </div>
      )}
      <main className="phone-main">
        {tab !== 'search' && !loaded && <p className="loading">{text.loading}</p>}
        {tab === 'now' && state && <NowTab state={state} />}
        {tab === 'queue' && state && <QueueTab state={state} />}
        {/* The search stays mounted to keep its results; the library reloads each time the tab opens. */}
        <div className="phone-search" hidden={tab !== 'search'}>
          <SearchView />
        </div>
        {tab === 'search' && (
          <section className="phone-search">
            <h2>{text.public.library}</h2>
            <LibraryView />
          </section>
        )}
      </main>
      <nav className="phone-tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.tab}
            type="button"
            role="tab"
            aria-selected={tab === item.tab}
            className={tab === item.tab ? 'tab tab-active' : 'tab'}
            onClick={() => setTab(item.tab)}
          >
            {item.label}
          </button>
        ))}
      </nav>
    </div>
  )
}

/** The song playing with its lyrics; otherwise the current song and the next one. */
function NowTab({ state }: { state: PhoneState }) {
  const entry = currentEntry(state)
  if (entry === null) return <p className="muted">{text.home.emptyPlaylist}</p>
  if (state.session.state === 'playing') return <NowPlaying state={state} />

  const next = upcomingEntries(state)[0]
  return (
    <>
      <section className="current-song">
        <p className="eyebrow">{sessionHeadings[state.session.state]}</p>
        <h1>{entry.song.title}</h1>
        <p className="artist">{entry.song.artist}</p>
        {state.session.state === 'setup' && <EntryStatus entry={entry} />}
      </section>
      <section className="current-song">
        <p className="eyebrow">{text.results.next}</p>
        {next ? (
          <>
            <strong>{next.song.title}</strong>
            <span className="muted">{next.song.artist}</span>
            <EntryStatus entry={next} />
          </>
        ) : (
          <p className="muted">{text.results.noNext}</p>
        )}
      </section>
    </>
  )
}

function NowPlaying({ state }: { state: PhoneState }) {
  const { storeNow } = usePublicState()
  const position = useAnchoredPosition(state.session, storeNow)
  const entry = currentEntry(state)
  return (
    <div className="phone-playing">
      <div className="playing-song">
        <p className="eyebrow" role="status">
          {state.session.paused ? text.public.paused : text.home.nowPlaying}
        </p>
        <strong>{entry?.song.title}</strong>
        <span>
          {entry?.song.artist}
          {text.finder.separator}
          {formatTime(position)}
          {text.playing.timeOf}
          {formatTime(state.session.duration_s)}
        </span>
      </div>
      <LyricsText
        lyrics={state.lyrics ?? NO_LYRICS}
        position={position}
        duration={state.session.duration_s}
      />
    </div>
  )
}

/** The playlist: the song playing first, without remove, then every other entry with it and up and down. */
function QueueTab({ state }: { state: PhoneState }) {
  const entry = currentEntry(state)
  const entries = listedEntries(state)
  return (
    <section className="phone-queue">
      {state.session.state !== 'setup' && entry && (
        <div className="playlist-item">
          <div className="playlist-item-song">
            <p className="eyebrow">{sessionHeadings[state.session.state]}</p>
            <strong>{entry.song.title}</strong>
            <span>{entry.song.artist}</span>
          </div>
        </div>
      )}
      {entries.length === 0 ? (
        <p className="muted">{entry ? text.home.noUpcoming : text.home.emptyPlaylist}</p>
      ) : (
        <ul className="playlist">
          {entries.map((item) => (
            <PlaylistItem key={item.id} entry={item} moves={moveOptions(state, item.id)} />
          ))}
        </ul>
      )}
    </section>
  )
}
