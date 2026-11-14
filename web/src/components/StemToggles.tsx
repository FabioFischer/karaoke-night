import { stemName, text } from '../text'

interface Props {
  stems: string[]
  muted: string[]
  disabled: boolean
  onToggle: (stem: string) => void
}

/** One switch per stem of the song: lit when it plays, dark when muted. */
export function StemToggles({ stems, muted, disabled, onToggle }: Props) {
  if (stems.length === 0) return <p className="muted">{text.setup.stemsLater}</p>
  return (
    <ul className="stems">
      {stems.map((stem) => {
        const on = !muted.includes(stem)
        return (
          <li key={stem}>
            <button
              type="button"
              className={on ? 'stem stem-on' : 'stem stem-off'}
              aria-pressed={on}
              disabled={disabled}
              onClick={() => onToggle(stem)}
            >
              <span>{stemName(stem)}</span>
              <small>{on ? text.setup.on : text.setup.off}</small>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
