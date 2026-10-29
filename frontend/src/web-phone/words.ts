/**
 * Pure helpers for the web phone page: reading its link, and the words it
 * shows. No React and no network, so each is tested on its own.
 */
import { ApiError, type WebCall, type WebPhoneLine } from '../api/client'
import type { VoiceState } from '../voice/voiceCall'

/** The key from the page's link, /call#key=…. It sits after the #, so it is
 *  never sent to a server or kept in its logs. */
export function keyFromHash(hash: string): string | null {
  const key = new URLSearchParams(hash.replace(/^#/, '')).get('key')
  return key ? key : null
}

/** The line the link asks for, /call#key=…&line=2, so each phone can have
 *  its own link. Null if it names none. */
export function lineFromHash(hash: string): number | null {
  const line = Number(new URLSearchParams(hash.replace(/^#/, '')).get('line'))
  return Number.isInteger(line) && line >= 1 ? line : null
}

/**
 * The line to call on: the one picked, else the link's, else the first that
 * is free. Lines come from the backend; a pick or a link naming a line that
 * isn't there is passed over.
 */
export function chooseLine(lines: WebPhoneLine[], picked: number | null, fromLink: number | null): number {
  const exists = (line: number | null): line is number => line !== null && lines.some((each) => each.line === line)
  if (exists(picked)) return picked
  if (exists(fromLink)) return fromLink
  return (lines.find((each) => !each.busy) ?? lines[0])?.line ?? 1
}

/** A line as the caller picks it: the number's last four digits. */
export function lineLabel(line: Pick<WebPhoneLine, 'caller_last_four'>): string {
  return line.caller_last_four ? `…${line.caller_last_four}` : 'Withheld'
}

/** How long a call has lasted: "0:07", "12:30". */
export function callDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** Two letters for the restaurant's round picture: "Maple Lane Burgers" → "ML". */
export function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word[0]?.toUpperCase() ?? '')
  return letters.slice(0, 2).join('') || '?'
}

const TURNED_AWAY: Record<NonNullable<WebCall['turned_away']>, string> = {
  blocked: 'This number is blocked from phone orders.',
  too_many_calls: 'Too many calls from this number without an order.',
}

/** Why the call was not put through to the agent, if it wasn't. */
export function turnedAwayLine(turnedAway: WebCall['turned_away'] | undefined): string | null {
  return turnedAway ? TURNED_AWAY[turnedAway] : null
}

/** The line under the restaurant's name. */
export function statusLine(voice: Pick<VoiceState, 'status' | 'problem'>, liveForMs: number | null): string {
  switch (voice.status) {
    case 'idle':
      return 'Tap to call'
    case 'connecting':
      return 'Calling…'
    case 'live':
      return callDuration(liveForMs ?? 0)
    case 'ended':
      return liveForMs === null ? 'Call ended' : `Call ended · ${callDuration(liveForMs)}`
    case 'failed':
      return voice.problem ?? 'The call could not be made.'
  }
}

/** Who is talking, while the call is live. */
export function speakingLine(voice: Pick<VoiceState, 'status' | 'customerSpeaking' | 'agentSpeaking'>): string | null {
  if (voice.status !== 'live') return null
  if (voice.agentSpeaking) return 'Frontdesk is speaking'
  if (voice.customerSpeaking) return 'Listening…'
  return null
}

/** What went wrong with the line or a call, in words for the caller. */
export function describeLineProblem(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'wrong_web_phone_key') return "This link's key is wrong or out of date. Ask for a new link."
    if (error.code === 'web_phone_off') return 'The web phone is switched off.'
    if (error.code === 'web_phone_line_busy') return 'That line is busy. Pick another, or try again in a minute.'
    if (error.code === 'no_such_web_phone_line') return "That line isn't set up. Pick another."
    if (error.code === 'agent_unavailable') return "Frontdesk can't take calls right now."
    return error.message
  }
  return 'The restaurant could not be reached. Check your connection and try again.'
}
