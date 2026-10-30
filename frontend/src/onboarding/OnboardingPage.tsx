import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { OnboardingResult } from '../api/client'
import { HomeLink } from '../app/HomeLink'
import staff from '../staff/Staff.module.css'
import { AllergensStep } from './AllergensStep'
import { CombosStep } from './CombosStep'
import { DietStep } from './DietStep'
import { DoneScreen } from './DoneScreen'
import { ItemsStep } from './ItemsStep'
import styles from './Onboarding.module.css'
import { RestaurantStep } from './RestaurantStep'
import { ReviewStep } from './ReviewStep'
import { goLivePayload, goLiveProblems, goLiveRefusal, loadDraft, menuToday, newDraft, saveDraft, stepProblem, STEPS } from './words'
import type { Draft, Step } from './words'

/**
 * A restaurant's first visit, one step at a time: who it is, its menu, its
 * allergens, what is halal or kosher, its combos, and going live. Everything
 * stays on this device until Go live, which saves it all at once.
 */
export function OnboardingPage() {
  const queryClient = useQueryClient()
  const menu = useQuery({ queryKey: ['admin-menu'], queryFn: api.adminMenu, retry: false })
  const combos = useQuery({ queryKey: ['combos', 'en'], queryFn: () => api.combos('en'), retry: false })
  const [draft, setDraft] = useState<Draft>(loadDraft)
  const [problem, setProblem] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [finished, setFinished] = useState<OnboardingResult | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const firstStep = useRef(true)

  useEffect(() => saveDraft(draft), [draft])

  // A new step is a new screen: start reading from its heading.
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false
      return
    }
    heading.current?.focus({ preventScroll: true })
    heading.current?.scrollIntoView?.({ block: 'center' })
  }, [draft.step])

  const today = menuToday(menu.data, combos.data)
  const position = STEPS.findIndex((each) => each.id === draft.step)
  const blockers = goLiveProblems(draft, today)

  function update(change: Partial<Draft>) {
    setProblem(null)
    setRefusal(null)
    setDraft((current) => ({ ...current, ...change }))
  }

  function goTo(step: Step) {
    update({ step })
  }

  function next() {
    const found = stepProblem(draft.step, draft, today)
    if (found) {
      setProblem(found)
      return
    }
    goTo(STEPS[position + 1].id)
  }

  function startOver() {
    if (!window.confirm('Clear everything entered so far?')) return
    setProblem(null)
    setRefusal(null)
    setDraft(newDraft())
  }

  async function goLive() {
    setBusy(true)
    setRefusal(null)
    try {
      setFinished(await api.goLive(goLivePayload(draft)))
      setDraft(newDraft())
      // The menu, the combos and the restaurant's name have all changed.
      void queryClient.invalidateQueries()
    } catch (error) {
      setRefusal(goLiveRefusal(error))
    } finally {
      setBusy(false)
    }
  }

  const steps = { draft, today, update }

  return (
    <div className={staff.screen}>
      <header className={staff.topBar}>
        <div className={staff.titleGroup}>
          <HomeLink />
          <h1 className={staff.title}>
            Onboarding <span className={staff.titleSub}>New restaurant</span>
          </h1>
        </div>
        {!finished && (
          <div className={styles.topEnd}>
            <span className={styles.saved}>Saved on this device</span>
            <button type="button" className={styles.quiet} onClick={startOver}>
              Start over
            </button>
          </div>
        )}
      </header>

      {menu.isError && (
        <p role="alert" className={staff.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      {finished ? (
        <DoneScreen result={finished} />
      ) : (
        <>
          <nav aria-label="Steps" className={styles.railWrap}>
            <ol className={styles.rail}>
              {STEPS.map((step, index) => (
                <li key={step.id} className={styles.railStep} data-state={index === position ? 'current' : index < position ? 'done' : 'ahead'}>
                  <button type="button" className={styles.railButton} aria-current={index === position ? 'step' : undefined} onClick={() => goTo(step.id)}>
                    <span className={styles.railNumber}>{index + 1}</span>
                    <span className={styles.railLabel}>{step.label}</span>
                  </button>
                </li>
              ))}
            </ol>
          </nav>

          <main className={styles.main}>
            <h2 ref={heading} tabIndex={-1} className={styles.stepTitle}>
              {HEADINGS[draft.step].title}
            </h2>
            <p className={styles.stepLede}>{HEADINGS[draft.step].lede}</p>

            {draft.step === 'restaurant' && <RestaurantStep {...steps} onDone={next} />}
            {draft.step === 'menu' && <ItemsStep {...steps} />}
            {draft.step === 'allergens' && <AllergensStep {...steps} />}
            {draft.step === 'diets' && <DietStep {...steps} />}
            {draft.step === 'combos' && <CombosStep {...steps} />}
            {draft.step === 'review' && <ReviewStep {...steps} blockers={blockers} goTo={goTo} />}
          </main>

          <footer className={styles.foot}>
            <div className={styles.footInner}>
              {(problem ?? refusal) && (
                <p role="alert" className={styles.footProblem}>
                  {problem ?? refusal}
                </p>
              )}
              <div className={styles.footActions}>
                {position > 0 ? (
                  <button type="button" className={staff.button} onClick={() => goTo(STEPS[position - 1].id)}>
                    Back
                  </button>
                ) : (
                  <span />
                )}
                {draft.step === 'review' ? (
                  <button type="button" className={`${staff.button} ${staff.buttonPrimary}`} disabled={busy || blockers.length > 0} onClick={() => void goLive()}>
                    {busy ? 'Going live…' : draft.replaceMenu ? 'Replace the menu and go live' : 'Go live'}
                  </button>
                ) : (
                  <button type="button" className={`${staff.button} ${staff.buttonPrimary}`} onClick={next}>
                    Continue
                  </button>
                )}
              </div>
            </div>
          </footer>
        </>
      )}
    </div>
  )
}

const HEADINGS: Record<Step, { title: string; lede: string }> = {
  restaurant: { title: 'Tell us about your restaurant', lede: 'This is what the agent says to your guests. You can change it later.' },
  menu: { title: 'Enter your menu', lede: 'Add one item at a time. Press Enter to add it and start the next one.' },
  allergens: { title: 'Allergens', lede: 'The agent only says what you set here. Whatever you leave, it hands to a person.' },
  diets: { title: 'Halal and kosher', lede: 'The agent only claims what you set here. Skip this if you don’t offer either.' },
  combos: { title: 'Combos', lede: 'One item from each part, bought together, costs less. Skip this if you don’t have any.' },
  review: { title: 'Ready to go live?', lede: 'Nothing has reached the agent yet. It all goes in at once when you press the button.' },
}
