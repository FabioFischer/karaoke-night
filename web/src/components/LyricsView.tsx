import { useEffect, useRef, useState } from 'react'
import { api, toApiError, type ApiError, type Lyrics, type LyricsLine } from '../api'
import { text } from '../text'
import { ErrorNote } from './ErrorNote'

interface Props {
  songId: string | null
  position: number | null
  duration: number | null
}

/** The lyrics of the song playing, filling the main area. */
export function LyricsView({ songId, position, duration }: Props) {
  const [lyrics, setLyrics] = useState<Lyrics | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  useEffect(() => {
    // A new song never shows the lyrics or the error of the previous one.
    setLyrics(null)
    setError(null)
    if (songId === null) return
    let cancelled = false
    api
      .lyrics(songId)
      .then((answer) => !cancelled && setLyrics(answer))
      .catch((caught) => !cancelled && setError(toApiError(caught)))
    return () => {
      cancelled = true
    }
  }, [songId])

  if (error) {
    return (
      <div className="lyrics lyrics-message">
        <p>{text.playing.lyricsFailed}</p>
        <ErrorNote code={error.code} detail={error.detail} />
      </div>
    )
  }
  if (lyrics === null) return <div className="lyrics lyrics-message" />
  return <LyricsText lyrics={lyrics} position={position} duration={duration} />
}

/** Lyrics already at hand, following the position: line by line when synced. */
export function LyricsText(props: { lyrics: Lyrics; position: number | null; duration: number | null }) {
  const { lyrics, position, duration } = props
  if (lyrics.status === 'none' || lyrics.lines.length === 0) {
    return (
      <div className="lyrics lyrics-message">
        <p>{text.playing.noLyrics}</p>
      </div>
    )
  }
  if (lyrics.status === 'synced') return <SyncedLyrics lines={lyrics.lines} position={position} />
  return <UnsyncedLyrics lines={lyrics.lines} position={position} duration={duration} />
}

function currentLine(lines: LyricsLine[], position: number | null): number {
  if (position === null) return -1
  let current = -1
  lines.forEach((line, index) => {
    if (line.time_s !== null && line.time_s <= position) current = index
  })
  return current
}

/** Line by line, the current one highlighted and kept in the middle of the screen. */
function SyncedLyrics({ lines, position }: { lines: LyricsLine[]; position: number | null }) {
  const current = currentLine(lines, position)
  const currentRef = useRef<HTMLLIElement>(null)

  useEffect(() => {
    currentRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [current])

  return (
    <ol className="lyrics lyrics-synced">
      {lines.map((line, index) => (
        <li
          key={index}
          ref={index === current ? currentRef : undefined}
          className={index === current ? 'line-current' : index < current ? 'line-sung' : ''}
        >
          {line.text || text.playing.instrumental}
        </li>
      ))}
    </ol>
  )
}

/** Without times, the text scrolls evenly from its top to its end as the song plays. */
function UnsyncedLyrics(props: { lines: LyricsLine[]; position: number | null; duration: number | null }) {
  const box = useRef<HTMLDivElement>(null)
  const share = props.position !== null && props.duration ? props.position / props.duration : 0

  useEffect(() => {
    const element = box.current
    if (element === null) return
    element.scrollTop = Math.min(Math.max(share, 0), 1) * (element.scrollHeight - element.clientHeight)
  }, [share])

  return (
    <div ref={box} className="lyrics lyrics-unsynced">
      {props.lines.map((line, index) => (
        <p key={index}>{line.text || ' '}</p>
      ))}
    </div>
  )
}
