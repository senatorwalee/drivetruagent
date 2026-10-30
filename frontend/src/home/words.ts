/**
 * What the home page's cards say, worked out from what the backend returns.
 * Plain functions, so they are tested without drawing anything.
 */
import type { AdminItem, AgentInfo, ConversationSummary, HandoffSummary, UnmetRequest, WeekOfSpecials } from '../api/client'
import { money } from '../session/presentation'

export interface Stat {
  /** The big figure: a number or a few words. */
  figure: string
  /** What the figure means. */
  caption: string
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/** Which model is answering, in words. */
export function agentNote(agent: AgentInfo): string {
  if (agent.scripted) return 'Scripted demo: no API key needed'
  if (!agent.available) return 'No model set up: add a key to .env'
  const voice = agent.voice_available ? ', with voice' : ''
  return `Answering with ${agent.model ?? 'the model'}${voice}`
}

export function liveStat(week: WeekOfSpecials): Stat {
  const today = week.days.find((day) => day.is_today)
  const special = today?.specials[0]
  if (!special) return { figure: 'No special', caption: 'today' }
  return {
    figure: money(special.special_price_cents),
    caption: `${special.item_name} today, usually ${money(special.regular_price_cents)}`,
  }
}

/** Handoffs nobody has closed yet, picked up or not. */
export function staffStat(handoffs: HandoffSummary[]): Stat {
  const waiting = handoffs.filter((handoff) => handoff.status !== 'resolved').length
  return waiting === 0
    ? { figure: '0', caption: 'handoffs waiting. All clear.' }
    : { figure: String(waiting), caption: `${waiting === 1 ? 'handoff' : 'handoffs'} waiting for a person` }
}

/** Notes with something for staff to do, or how many there are. */
export function conversationsStat(summaries: ConversationSummary[]): Stat {
  const toFollowUp = summaries.filter((summary) => summary.follow_up).length
  if (toFollowUp > 0) return { figure: String(toFollowUp), caption: `${toFollowUp === 1 ? 'note' : 'notes'} to follow up` }
  if (summaries.length === 0) return { figure: 'None yet', caption: 'a note follows each conversation' }
  return { figure: String(summaries.length), caption: `recent ${summaries.length === 1 ? 'conversation' : 'conversations'}, nothing to follow up` }
}

export function insightsStat(requests: UnmetRequest[]): Stat {
  const top = requests[0]
  if (!top) return { figure: 'Nothing yet', caption: 'asked for this week that the menu lacks' }
  return { figure: `“${top.request}”`, caption: `most asked this week, ${plural(top.times, 'time', 'times')}` }
}

export const ONBOARDING_STAT: Stat = { figure: '5', caption: 'steps from a name to going live' }

export function menuStat(items: AdminItem[]): Stat {
  const onMenu = items.filter((item) => item.is_active)
  const noPhoto = onMenu.filter((item) => !item.photo).length
  return {
    figure: String(onMenu.length),
    caption: noPhoto === 0 ? 'items on the menu, all with photos' : `items on the menu, ${noPhoto} without a photo`,
  }
}
