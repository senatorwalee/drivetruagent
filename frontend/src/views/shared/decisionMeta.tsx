import type { ReactNode } from 'react'

import type { DecisionKind } from '../../session/types'
import { AlertIcon, CheckIcon, CrossIcon, EyeIcon, HandoffIcon } from './icons'

/**
 * How each kind of decision is named and marked. Every kind has a word and
 * an icon as well as a colour, so it never depends on colour alone.
 */
export const DECISION_META: Record<DecisionKind, { label: string; meaning: string; icon: ReactNode }> = {
  allow: { label: 'allow', meaning: 'ran', icon: <CheckIcon size={11} /> },
  reject: { label: 'reject', meaning: 'nothing changed', icon: <CrossIcon size={11} /> },
  confirm: { label: 'confirm', meaning: 'ask the customer', icon: <AlertIcon size={11} /> },
  escalate: { label: 'escalate', meaning: 'hand to a person', icon: <HandoffIcon size={11} /> },
  read: { label: 'read', meaning: 'looked something up', icon: <EyeIcon size={11} /> },
}

/** An error or rule code as words: "missing_required_options" reads badly aloud. */
export function readableReason(reason: string | null): string | null {
  return reason ? reason.replaceAll('_', ' ') : null
}
