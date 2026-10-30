/**
 * An item's allergen review as a manager makes it, on the menu page and when
 * onboarding. No React here, so it is tested on its own.
 *
 * The rule everything here keeps: null means not reviewed, and a review only
 * exists once someone has said something. Taking the last tick off goes back
 * to null, not to an empty review, because a reviewed item with nothing
 * declared is offered to guests avoiding an allergen. Only `noneOfThese`, which
 * takes a choice of its own, says that.
 */
import type { AdminItem, Allergen, AllergenReview } from '../api/client'

export const ALLERGENS: { id: Allergen; label: string }[] = [
  { id: 'peanut', label: 'Peanuts' },
  { id: 'tree_nut', label: 'Tree nuts' },
  { id: 'sesame', label: 'Sesame' },
  { id: 'milk', label: 'Milk' },
  { id: 'egg', label: 'Eggs' },
  { id: 'fish', label: 'Fish' },
  { id: 'shellfish', label: 'Shellfish' },
  { id: 'soy', label: 'Soy' },
  { id: 'wheat', label: 'Wheat' },
  { id: 'gluten', label: 'Gluten' },
  { id: 'mustard', label: 'Mustard' },
  { id: 'sulphite', label: 'Sulphites' },
]

export type AllergenLevel = 'contains' | 'may_contain'

export interface Review {
  noneOfThese: boolean
  contains: Allergen[]
  mayContain: { allergen: Allergen; because: string }[]
}

export const NONE_OF_THESE: Review = { noneOfThese: true, contains: [], mayContain: [] }

export function allergenLabel(allergen: Allergen): string {
  return ALLERGENS.find((each) => each.id === allergen)?.label ?? allergen
}

export function levelOf(review: Review | null, allergen: Allergen): AllergenLevel | null {
  if (review?.contains.includes(allergen)) return 'contains'
  return review?.mayContain.some((each) => each.allergen === allergen) ? 'may_contain' : null
}

/** One allergen moved to a level, or off the list. */
export function withLevel(review: Review | null, allergen: Allergen, level: AllergenLevel | null): Review | null {
  const contains = (review?.contains ?? []).filter((each) => each !== allergen)
  const mayContain = (review?.mayContain ?? []).filter((each) => each.allergen !== allergen)
  // Often the same reason for several: a shared fryer. Start from the last one.
  const because = mayContain.at(-1)?.because ?? ''
  if (level === 'contains') contains.push(allergen)
  if (level === 'may_contain') mayContain.push({ allergen, because })
  return contains.length === 0 && mayContain.length === 0 ? null : { noneOfThese: false, contains, mayContain }
}

export function withReason(review: Review, allergen: Allergen, because: string): Review {
  return { ...review, mayContain: review.mayContain.map((each) => (each.allergen === allergen ? { ...each, because } : each)) }
}

/** What stops this review from being saved, or null. */
export function reviewProblem(review: Review | null, itemName: string): string | null {
  const unexplained = review?.mayContain.find((each) => !each.because.trim())
  return unexplained ? `Say why ${itemName} may contain ${allergenLabel(unexplained.allergen).toLowerCase()}.` : null
}

export function reviewSent(review: Review | null): AllergenReview | null {
  if (!review) return null
  if (review.noneOfThese) return { none_of_these: true }
  return {
    none_of_these: false,
    contains: review.contains,
    may_contain: review.mayContain.map((each) => ({ allergen: each.allergen, because: each.because.trim() })),
  }
}

/** As the backend has it. Both lists empty on a reviewed item is its "none". */
export function reviewFromItem(item: Pick<AdminItem, 'allergens'>): Review | null {
  if (!item.allergens) return null
  const { contains, may_contain: mayContain } = item.allergens
  if (contains.length === 0 && mayContain.length === 0) return NONE_OF_THESE
  return { noneOfThese: false, contains, mayContain }
}

export function sameReview(a: Review | null, b: Review | null): boolean {
  return JSON.stringify(reviewSent(a)) === JSON.stringify(reviewSent(b))
}

function inMenuOrder(allergens: Allergen[]): string[] {
  return ALLERGENS.filter((each) => allergens.includes(each.id)).map((each) => each.label.toLowerCase())
}

/** "Contains fish, wheat. May contain sesame." */
export function reviewLabel(review: Review | null): string {
  if (!review) return 'Not reviewed'
  if (review.noneOfThese) return 'None of these'
  const contains = inMenuOrder(review.contains)
  const mayContain = inMenuOrder(review.mayContain.map((each) => each.allergen))
  return [contains.length > 0 && `Contains ${contains.join(', ')}.`, mayContain.length > 0 && `May contain ${mayContain.join(', ')}.`].filter(Boolean).join(' ')
}
