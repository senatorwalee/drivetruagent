import type { MouseEvent, ReactNode } from 'react'

import { navigate } from './navigation'

interface PageLinkProps {
  to: string
  className?: string
  children: ReactNode
}

/**
 * A link to another page of the app. It is a real link, so it can be opened
 * in a new tab or copied; an ordinary click changes page without reloading.
 */
export function PageLink({ to, className, children }: PageLinkProps) {
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    // Leave modified clicks alone: they mean "new tab" or "new window".
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
    event.preventDefault()
    navigate(to)
  }
  return (
    <a href={to} className={className} onClick={follow}>
      {children}
    </a>
  )
}
