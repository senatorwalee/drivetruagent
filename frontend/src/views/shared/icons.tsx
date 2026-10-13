/** The few icons the screen uses, drawn inline so they take the text colour. */

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const

export function MicrophoneIcon() {
  return (
    <svg aria-hidden="true" width="20" height="20" viewBox="0 0 20 20" {...stroke}>
      <rect x="7" y="2.5" width="6" height="10" rx="3" />
      <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5" />
    </svg>
  )
}

export function PauseIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
      <rect x="3" y="2.5" width="3.5" height="11" rx="1" />
      <rect x="9.5" y="2.5" width="3.5" height="11" rx="1" />
    </svg>
  )
}

export function PlayIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
      <path d="M4 2.6v10.8a.6.6 0 0 0 .9.5l9-5.4a.6.6 0 0 0 0-1L4.9 2.1a.6.6 0 0 0-.9.5z" />
    </svg>
  )
}

export function CheckIcon({ size = 14 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth={2}>
      <path d="M3 8.5l3.2 3L13 4.5" />
    </svg>
  )
}

export function CrossIcon({ size = 14 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth={2}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  )
}

export function AlertIcon({ size = 14 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth={2}>
      <path d="M8 3v6M8 12.2v.3" />
    </svg>
  )
}

export function HandoffIcon({ size = 14 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth={1.8}>
      <circle cx="8" cy="5" r="2.5" />
      <path d="M3 13.5c.6-2.6 2.6-4 5-4s4.4 1.4 5 4" />
    </svg>
  )
}

export function ClockIcon({ size = 14 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" {...stroke}>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5V8l2.3 1.6" />
    </svg>
  )
}

export function EyeIcon({ size = 14 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth={1.6}>
      <path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" />
      <circle cx="8" cy="8" r="1.8" />
    </svg>
  )
}
