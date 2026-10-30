import { describe, expect, it } from 'vitest'

import { ALLERGENS, NONE_OF_THESE, levelOf, reviewFromItem, reviewLabel, reviewProblem, reviewSent, sameReview, withLevel, withReason } from './allergens'
import type { Review } from './allergens'

const MILK: Review = { noneOfThese: false, contains: ['milk'], mayContain: [] }

describe('an allergen review', () => {
  it('offers the twelve allergens the backend knows, each once', () => {
    expect(ALLERGENS.map((each) => each.id)).toEqual(['peanut', 'tree_nut', 'sesame', 'milk', 'egg', 'fish', 'shellfish', 'soy', 'wheat', 'gluten', 'mustard', 'sulphite'])
  })

  it('starts with the first tick', () => {
    expect(withLevel(null, 'milk', 'contains')).toEqual(MILK)
  })

  it('goes back to not reviewed when the last tick comes off, never to none', () => {
    expect(withLevel(MILK, 'milk', null)).toBeNull()
    expect(reviewSent(withLevel(MILK, 'milk', null))).toBeNull()
  })

  it('keeps each allergen at one level', () => {
    const moved = withLevel(MILK, 'milk', 'may_contain')

    expect(moved).toEqual({ noneOfThese: false, contains: [], mayContain: [{ allergen: 'milk', because: '' }] })
    expect(levelOf(moved, 'milk')).toBe('may_contain')
  })

  it('leaves none of these once something is ticked', () => {
    expect(withLevel(NONE_OF_THESE, 'egg', 'contains')).toEqual({ noneOfThese: false, contains: ['egg'], mayContain: [] })
  })

  it('starts the next reason from the last one', () => {
    const one = withReason(withLevel(null, 'milk', 'may_contain')!, 'milk', 'Shared fryer')

    expect(withLevel(one, 'egg', 'may_contain')?.mayContain).toEqual([
      { allergen: 'milk', because: 'Shared fryer' },
      { allergen: 'egg', because: 'Shared fryer' },
    ])
  })

  it('needs a reason for may contain', () => {
    const blank = withLevel(null, 'milk', 'may_contain')

    expect(reviewProblem(blank, 'Fries')).toBe('Say why Fries may contain milk.')
    expect(reviewProblem(withReason(blank!, 'milk', '   '), 'Fries')).not.toBeNull()
    expect(reviewProblem(withReason(blank!, 'milk', 'Shared fryer'), 'Fries')).toBeNull()
  })

  it('needs nothing for contains, for none, or before a review', () => {
    expect(reviewProblem(MILK, 'Fries')).toBeNull()
    expect(reviewProblem(NONE_OF_THESE, 'Fries')).toBeNull()
    expect(reviewProblem(null, 'Fries')).toBeNull()
  })

  it('sends null for not reviewed and says none of these out loud', () => {
    expect(reviewSent(null)).toBeNull()
    expect(reviewSent(NONE_OF_THESE)).toEqual({ none_of_these: true })
    expect(reviewSent(withReason(withLevel(MILK, 'egg', 'may_contain')!, 'egg', '  Shared fryer '))).toEqual({
      none_of_these: false,
      contains: ['milk'],
      may_contain: [{ allergen: 'egg', because: 'Shared fryer' }],
    })
  })

  it('reads a reviewed item with nothing declared as none of these', () => {
    expect(reviewFromItem({ allergens: { contains: [], may_contain: [] } })).toEqual(NONE_OF_THESE)
    expect(reviewFromItem({ allergens: null })).toBeNull()
  })

  it('tells a change from no change', () => {
    expect(sameReview(MILK, { ...MILK })).toBe(true)
    expect(sameReview(MILK, null)).toBe(false)
    expect(sameReview(NONE_OF_THESE, null)).toBe(false)
  })

  it('reads out in menu order', () => {
    const review: Review = { noneOfThese: false, contains: ['wheat', 'fish'], mayContain: [{ allergen: 'sesame', because: 'Buns' }] }

    expect(reviewLabel(review)).toBe('Contains fish, wheat. May contain sesame.')
    expect(reviewLabel(MILK)).toBe('Contains milk.')
    expect(reviewLabel(NONE_OF_THESE)).toBe('None of these')
    expect(reviewLabel(null)).toBe('Not reviewed')
  })
})
