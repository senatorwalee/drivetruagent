import { describe, expect, it } from 'vitest'

import { agentInfo, week } from '../test/fixtures'
import type { ConversationSummary } from '../api/client'
import { agentNote, conversationsStat, insightsStat, liveStat, menuStat, staffStat } from './words'

describe('what the home page cards say', () => {
  it('names the model when a real one answers', () => {
    expect(agentNote({ ...agentInfo, scripted: false, model: 'gpt-6-luna', voice_available: true })).toBe(
      'Answering with gpt-6-luna, with voice',
    )
    expect(agentNote({ ...agentInfo, scripted: false, available: false })).toMatch(/add a key/)
  })

  it('says when there is no special today', () => {
    const quiet = { ...week, days: week.days.map((day) => ({ ...day, specials: [] })) }

    expect(liveStat(quiet)).toEqual({ figure: 'No special', caption: 'today' })
  })

  it('says all clear when no handoff is waiting', () => {
    expect(staffStat([])).toEqual({ figure: '0', caption: 'handoffs waiting. All clear.' })
  })

  it('counts one time in the singular', () => {
    expect(insightsStat([{ request: 'poutine', times: 1, match: 'none', last_asked: '' }]).caption).toBe(
      'most asked this week, 1 time',
    )
    expect(insightsStat([]).figure).toBe('Nothing yet')
  })

  it('says when every item has a photo', () => {
    expect(menuStat([]).caption).toBe('items on the menu, all with photos')
  })

  it('counts the notes to follow up, or the notes', () => {
    const note = (followUp: string | null) => ({ follow_up: followUp }) as ConversationSummary

    expect(conversationsStat([note('Call back'), note('Check stock'), note(null)])).toEqual({ figure: '2', caption: 'notes to follow up' })
    expect(conversationsStat([note(null)])).toEqual({ figure: '1', caption: 'recent conversation, nothing to follow up' })
    expect(conversationsStat([])).toEqual({ figure: 'None yet', caption: 'a note follows each conversation' })
  })
})
