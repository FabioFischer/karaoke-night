import { keyText, text } from '../text'

// The API does not limit the key; the screen does.
export const KEY_MIN = -6
export const KEY_MAX = 6

interface Props {
  value: number
  disabled: boolean
  onChange: (key: number) => void
}

/** The key in semitones, with a minus and a plus. */
export function KeyControl({ value, disabled, onChange }: Props) {
  return (
    <div className="key-control">
      <button
        type="button"
        aria-label={text.setup.keyDown}
        disabled={disabled || value <= KEY_MIN}
        onClick={() => onChange(value - 1)}
      >
        {text.setup.minus}
      </button>
      <output aria-live="polite">{keyText(value)}</output>
      <button
        type="button"
        aria-label={text.setup.keyUp}
        disabled={disabled || value >= KEY_MAX}
        onClick={() => onChange(value + 1)}
      >
        {text.setup.plus}
      </button>
    </div>
  )
}
