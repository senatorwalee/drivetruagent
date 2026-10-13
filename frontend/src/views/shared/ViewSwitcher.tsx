import { VIEWS, type ViewId } from './viewTypes'

interface ViewSwitcherProps {
  view: ViewId
  onChange: (view: ViewId) => void
  /**
   * Class names from the host view's stylesheet. The switcher has no look of
   * its own, so it matches whichever of the three views it sits in.
   */
  classes: { group: string; option: string; selected: string }
}

/** Chooses between the three looks of the screen. */
export function ViewSwitcher({ view, onChange, classes }: ViewSwitcherProps) {
  return (
    <div role="group" aria-label="View" className={classes.group}>
      {VIEWS.map((option) => (
        <button
          key={option.id}
          type="button"
          aria-pressed={option.id === view}
          className={option.id === view ? `${classes.option} ${classes.selected}` : classes.option}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
