import { useState } from 'react'

import styles from './MenuAdmin.module.css'

interface ItemPictureProps {
  /** Where the photo is, or null for none. */
  src: string | null
  categoryCode: string
  /** For the photo's alt text. Leave out where the name is right beside it. */
  label?: string
  size: 'thumb' | 'large'
}

/**
 * An item's photo, or a placeholder drawn for its category when it has none
 * (or its photo will not load). Every item shows something in the same shape.
 */
export function ItemPicture({ src, categoryCode, label, size }: ItemPictureProps) {
  const [failed, setFailed] = useState<string | null>(null)
  const showPhoto = src !== null && failed !== src

  return (
    <span className={styles.picture} data-size={size}>
      {showPhoto ? (
        <img src={src} alt={label ? `Photo of ${label}` : ''} loading="lazy" onError={() => setFailed(src)} />
      ) : (
        <span className={styles.placeholder} data-category={categoryCode} role={label ? 'img' : undefined} aria-label={label ? `No photo of ${label} yet` : undefined}>
          <CategoryIcon categoryCode={categoryCode} />
          {size === 'large' && <span className={styles.placeholderText}>No photo yet</span>}
        </span>
      )}
    </span>
  )
}

function CategoryIcon({ categoryCode }: { categoryCode: string }) {
  const common = {
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
    className: styles.placeholderIcon,
  }
  switch (categoryCode) {
    case 'burgers':
      return (
        <svg {...common}>
          <path d="M4 10a8 5 0 0 1 16 0z" />
          <path d="M3.5 13.5h17" />
          <path d="M4 16.5h16a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3z" />
          <path d="M9 7.5h.01M12 6.5h.01M15 7.5h.01" />
        </svg>
      )
    case 'sides':
      return (
        <svg {...common}>
          <path d="M6 10h12l-1.5 10h-9z" />
          <path d="M8 10 7 4M10.5 10 10 3M13.5 10 14 3.5M16 10l1.5-5.5" />
        </svg>
      )
    case 'drinks':
      return (
        <svg {...common}>
          <path d="M6.5 7h11l-1.3 13.2a1 1 0 0 1-1 .8H8.8a1 1 0 0 1-1-.8z" />
          <path d="M5.5 7h13M13 7l2-4h2.5" />
        </svg>
      )
    case 'desserts':
      return (
        <svg {...common}>
          <path d="M8 11.5 12 21l4-9.5" />
          <path d="M7.5 11.5a4.5 4.5 0 1 1 9 0z" />
        </svg>
      )
    default:
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8" />
          <circle cx="12" cy="12" r="4.5" />
        </svg>
      )
  }
}
