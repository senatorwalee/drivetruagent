import { Fragment } from 'react'

import type { Discount } from '../../api/client'
import { discountLabel, money } from '../../session/presentation'

interface DiscountRowsProps {
  discounts: readonly Discount[] | undefined
  /** Applied to both the label and the amount, so each view styles a saving its own way. */
  className?: string
  format?: (cents: number) => string
}

/**
 * One row per saving, for a totals list: the name and the amount taken off.
 * Renders nothing when there are none, so a plain order looks as it always did.
 */
export function DiscountRows({ discounts = [], className, format = money }: DiscountRowsProps) {
  return discounts.map((discount) => (
    <Fragment key={`${discount.kind}:${discount.code}`}>
      <dt className={className}>{discountLabel(discount)}</dt>
      <dd className={className}>−{format(discount.amount_cents)}</dd>
    </Fragment>
  ))
}
