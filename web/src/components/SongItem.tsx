import { useState } from 'react'
import type { Song } from '../api'
import { useAction, useCalls } from '../calls'
import { formatTime } from '../session'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'

/** A search or library result with its add button. */
export function SongItem({ song }: { song: Song }) {
  const calls = useCalls()
  const add = useAction()
  const [added, setAdded] = useState(false)

  async function addToPlaylist() {
    if (await add.run(() => calls.add(song))) setAdded(true)
  }

  const details = [song.artist, song.album, formatTime(song.duration_s)].filter(Boolean)
  return (
    <li className="song-item">
      {song.thumbnail_url ? (
        <img className="thumbnail" src={song.thumbnail_url} alt="" loading="lazy" />
      ) : (
        <div className="thumbnail thumbnail-empty" aria-hidden="true">
          {text.finder.noThumbnail}
        </div>
      )}
      <div className="song-item-text">
        <strong>{song.title}</strong>
        <span>{details.join(text.finder.separator)}</span>
        {song.song_id && <span className="ready-mark">{text.finder.ready}</span>}
      </div>
      <button type="button" disabled={add.busy || added} onClick={addToPlaylist}>
        {add.busy ? text.finder.adding : added ? text.finder.added : text.finder.add}
      </button>
      {add.error && <ErrorNote code={add.error.code} detail={add.error.detail} />}
    </li>
  )
}
