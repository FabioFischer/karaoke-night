import { useEffect, useState, type FormEvent } from 'react'
import { toApiError, type ApiError, type SearchAnswer, type Song } from '../api'
import { useCalls } from '../calls'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'
import { SongItem } from './SongItem'

type Tab = 'search' | 'library'

/** Search (library first, then YouTube Music) and the library, each song with its add button. */
export function SongFinder() {
  const [tab, setTab] = useState<Tab>('search')
  return (
    <section className="finder">
      <div className="tabs" role="tablist">
        <TabButton active={tab === 'search'} onClick={() => setTab('search')}>
          {text.finder.searchTab}
        </TabButton>
        <TabButton active={tab === 'library'} onClick={() => setTab('library')}>
          {text.finder.libraryTab}
        </TabButton>
      </div>
      {/* The search stays mounted to keep its results; the library reloads each time it opens. */}
      <div hidden={tab !== 'search'}>
        <SearchView />
      </div>
      {tab === 'library' && <LibraryView />}
    </section>
  )
}

function TabButton(props: { active: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={props.active}
      className={props.active ? 'tab tab-active' : 'tab'}
      onClick={props.onClick}
    >
      {props.children}
    </button>
  )
}

export function SearchView() {
  const calls = useCalls()
  const [query, setQuery] = useState('')
  const [answer, setAnswer] = useState<SearchAnswer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  async function search(event: FormEvent) {
    event.preventDefault()
    if (!query.trim()) return
    setBusy(true)
    setError(null)
    try {
      setAnswer(await calls.search(query))
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <form className="search-form" onSubmit={search}>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={text.finder.searchPlaceholder}
          autoFocus
        />
        <button type="submit" disabled={busy}>
          {busy ? text.finder.searching : text.finder.searchButton}
        </button>
      </form>
      {error && <ErrorNote code={error.code} detail={error.detail} />}
      {answer?.notice === 'offline' && (
        <div className="note note-warning">
          <p>{text.finder.offlineNotice}</p>
          {answer.detail && <p className="detail">{answer.detail}</p>}
        </div>
      )}
      {answer && <SongList songs={answer.results} empty={text.finder.noResults} />}
    </>
  )
}

export function LibraryView() {
  const calls = useCalls()
  const [songs, setSongs] = useState<Song[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  useEffect(() => {
    let cancelled = false
    calls
      .library()
      .then((answer) => !cancelled && setSongs(answer.songs))
      .catch((caught) => !cancelled && setError(toApiError(caught)))
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return <ErrorNote code={error.code} detail={error.detail} />
  if (songs === null) return <p>{text.loading}</p>
  return <SongList songs={songs} empty={text.finder.emptyLibrary} />
}

function SongList({ songs, empty }: { songs: Song[]; empty: string }) {
  if (songs.length === 0) return <p className="muted">{empty}</p>
  return (
    <ul className="song-list">
      {songs.map((song, index) => (
        <SongItem key={song.song_id ?? song.video_id ?? String(index)} song={song} />
      ))}
    </ul>
  )
}
