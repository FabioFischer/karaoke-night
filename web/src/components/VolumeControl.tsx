import type { Volumes } from '../api'
import { text, volumeText } from '../text'

// The API takes any number; the screen keeps the range (docs/PLAN.md section 5.8, Volumes).
export const VOLUME_MIN_DB = -30
export const VOLUME_MAX_DB = 6
const STEP_DB = 2

interface Props {
  /** Null until the Stage read them from REAPER: the controls wait, disabled. */
  volumes: Volumes | null
  disabled: boolean
  onChange: (volumes: Volumes) => void
}

/** The music's and the microphones' volumes, each with a minus and a plus. */
export function VolumeControl({ volumes, disabled, onChange }: Props) {
  return (
    <div className="volume-control">
      <VolumeStepper
        label={text.volumes.music}
        downLabel={text.volumes.musicDown}
        upLabel={text.volumes.musicUp}
        value={volumes?.music_db ?? null}
        disabled={disabled}
        onChange={(music_db) => volumes && onChange({ ...volumes, music_db })}
      />
      <VolumeStepper
        label={text.volumes.voice}
        downLabel={text.volumes.voiceDown}
        upLabel={text.volumes.voiceUp}
        value={volumes?.voice_db ?? null}
        disabled={disabled}
        onChange={(voice_db) => volumes && onChange({ ...volumes, voice_db })}
      />
      {volumes === null && <p className="muted">{text.volumes.waiting}</p>}
    </div>
  )
}

interface StepperProps {
  label: string
  downLabel: string
  upLabel: string
  value: number | null
  disabled: boolean
  onChange: (db: number) => void
}

/** A step from a volume REAPER may hold outside the range lands back inside it. */
function step(value: number, by: number): number {
  return Math.min(Math.max(Math.round(value) + by, VOLUME_MIN_DB), VOLUME_MAX_DB)
}

function VolumeStepper({ label, downLabel, upLabel, value, disabled, onChange }: StepperProps) {
  return (
    <div className="volume">
      <span className="volume-label">{label}</span>
      <button
        type="button"
        aria-label={downLabel}
        disabled={disabled || value === null || value <= VOLUME_MIN_DB}
        onClick={() => value !== null && onChange(step(value, -STEP_DB))}
      >
        {text.setup.minus}
      </button>
      <output aria-live="polite">{value === null ? '' : volumeText(value)}</output>
      <button
        type="button"
        aria-label={upLabel}
        disabled={disabled || value === null || value >= VOLUME_MAX_DB}
        onClick={() => value !== null && onChange(step(value, STEP_DB))}
      >
        {text.setup.plus}
      </button>
    </div>
  )
}
