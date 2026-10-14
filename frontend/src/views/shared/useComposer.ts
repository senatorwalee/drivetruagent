import { useState, type FormEvent } from 'react'

import type { LiveSession } from '../../session/useLiveSession'

/**
 * The behaviour of the box the customer's words are typed into. Each view
 * draws its own form; this keeps what the form does in one place.
 */
export function useComposer(live: LiveSession) {
  const [text, setText] = useState('')

  const change = (value: string) => {
    setText(value)
    live.setCustomerActive(value.trim().length > 0)
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!text.trim() || !live.canSend) return
    live.send(text)
    change('')
  }

  return {
    text,
    change,
    submit,
    disabled: !live.canSend,
    placeholder: placeholderFor(live),
    /** For the scripted demo: fills the box with the next line to say. */
    suggestion: live.suggestedLine,
    useSuggestion: () => live.suggestedLine && change(live.suggestedLine),
  }
}

function placeholderFor(live: LiveSession): string {
  if (live.agentState === 'ended') return 'This session has ended'
  if (live.paused) return 'Paused'
  if (live.voice.status === 'connecting') return 'Connecting the call…'
  if (live.voice.status === 'live') return 'On a call: just speak'
  if (live.agentState === 'working') return 'The agent is working…'
  return 'Type what the customer says'
}
