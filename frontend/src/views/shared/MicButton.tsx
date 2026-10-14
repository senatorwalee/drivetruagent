import type { Voice } from '../../voice/useVoiceCall'
import { MicrophoneIcon } from './icons'

/**
 * Starts and ends a voice call. Each view gives it its own class; while a
 * call is on, `aria-pressed` is true, which the views style.
 */
export function MicButton({ voice, className }: { voice: Voice; className: string }) {
  const label = voice.active ? 'Hang up' : voice.available ? 'Talk to the agent' : 'Voice is not available'
  return (
    <button
      type="button"
      className={className}
      aria-label={label}
      title={label}
      aria-pressed={voice.active}
      data-status={voice.status}
      disabled={!voice.active && !voice.available}
      onClick={voice.active ? voice.stop : voice.start}
    >
      <MicrophoneIcon />
    </button>
  )
}
