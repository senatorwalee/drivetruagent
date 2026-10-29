import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { api } from '../api/client'
import { FrontdeskMark, HomeLink } from '../app/HomeLink'
import { CONVERSATIONS_PATH, INSIGHTS_PATH, LIVE_PATH, MENU_PATH, STAFF_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import styles from './Home.module.css'
import { CommonQuestions, HowItWorks, WhyIBuiltThis } from './StorySections'
import { HOME_DESIGNS, useHomeDesign } from './useHomeDesign'
import { agentNote, conversationsStat, insightsStat, liveStat, menuStat, staffStat } from './words'
import type { Stat } from './words'

// Handoffs and requests arrive while the page is open.
const REFRESH_MS = 30_000

/**
 * The front door: what Frontdesk is, and one card for each part of it, each
 * with a live number so the page is worth a glance on its own.
 */
export function HomePage() {
  const agent = useQuery({ queryKey: ['agent'], queryFn: api.agentInfo, retry: false })
  const week = useQuery({ queryKey: ['specials-week', 'en'], queryFn: () => api.weekOfSpecials('en'), retry: false })
  const handoffs = useQuery({ queryKey: ['handoffs'], queryFn: api.handoffs, refetchInterval: REFRESH_MS, retry: false })
  const requests = useQuery({
    queryKey: ['unmet-requests', 7],
    queryFn: () => api.unmetRequests(7),
    refetchInterval: REFRESH_MS,
    retry: false,
  })
  const menu = useQuery({ queryKey: ['admin-menu'], queryFn: api.adminMenu, retry: false })
  const conversations = useQuery({
    queryKey: ['summaries', 'all'],
    queryFn: () => api.summaries(),
    refetchInterval: REFRESH_MS,
    retry: false,
  })

  const restaurant = agent.data?.restaurant_name
  const [design, chooseDesign] = useHomeDesign()
  const isNew = design === 'new'

  const cards = (
    <ul className={styles.cards} aria-label="Parts of Frontdesk">
      <Card to={LIVE_PATH} name="Live lane" stat={week.data && liveStat(week.data)} failed={week.isError} accent>
        Take an order by voice or text, and watch every decision the backend makes as it happens.
      </Card>
      <Card to={STAFF_PATH} name="Staff" stat={handoffs.data && staffStat(handoffs.data)} failed={handoffs.isError}>
        Orders the agent handed to a person, with the cart, the stock it holds and what to do next.
      </Card>
      <Card
        to={CONVERSATIONS_PATH}
        name="Conversations"
        stat={conversations.data && conversationsStat(conversations.data)}
        failed={conversations.isError}
      >
        A note on each conversation once it ends: what was wanted, how it went, and anything to follow up.
      </Card>
      <Card to={INSIGHTS_PATH} name="Insights" stat={requests.data && insightsStat(requests.data)} failed={requests.isError}>
        What guests asked for that the menu couldn't fill: what to add, and what to say isn't there.
      </Card>
      <Card to={MENU_PATH} name="Menu" stat={menu.data && menuStat(menu.data.items)} failed={menu.isError}>
        Add, edit and take off items, each with a photo. The agent sees changes from the next car.
      </Card>
    </ul>
  )

  return (
    <div className={styles.screen}>
      <header className={styles.topBar}>
        <HomeLink />
        <div className={styles.topEnd}>
          {restaurant && <span className={styles.restaurant}>{restaurant} · drive-through and phone</span>}
          <div className={styles.designs} role="group" aria-label="Home page design">
            {HOME_DESIGNS.map((option) => (
              <button
                key={option.id}
                type="button"
                className={styles.design}
                aria-pressed={design === option.id}
                onClick={() => chooseDesign(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {agent.isError && (
        <p role="alert" className={styles.banner}>
          Cannot reach the backend. Start it with `make dev`, then reload.
        </p>
      )}

      <main className={styles.main}>
        <section className={styles.hero} aria-labelledby="home-title">
          <p className={styles.logo}>
            <FrontdeskMark className={styles.logoMark} />
            Frontdesk
          </p>
          {isNew && <p className={styles.chip}>Voice AI · Drive-through and phone orders</p>}
          <h1 id="home-title" className={styles.title}>
            The agent takes the order.
            <br />
            <span className={isNew ? styles.highlight : undefined}>The backend checks every step.</span>
          </h1>
          <p className={styles.lede}>
            Frontdesk takes orders by voice at the drive-through and on the phone, in English or French. Prices, stock,
            specials and every rule are decided in code, and anything it can't handle goes to a person with the whole story.
          </p>
          <div className={styles.actions}>
            <PageLink to={LIVE_PATH} className={styles.primary}>
              Open the live lane
              <span aria-hidden="true">→</span>
            </PageLink>
            {agent.data && <span className={styles.note}>{agentNote(agent.data)}</span>}
          </div>
        </section>

        {isNew && <HowItWorks />}

        {isNew ? (
          <section className={styles.section} aria-labelledby="parts-title">
            <p className={styles.eyebrow}>Try it</p>
            <h2 id="parts-title" className={styles.sectionTitle}>
              Every part of the app, with a live number.
            </h2>
            {cards}
          </section>
        ) : (
          cards
        )}

        {isNew && (
          <>
            <WhyIBuiltThis />
            <CommonQuestions />
          </>
        )}
      </main>
    </div>
  )
}

interface CardProps {
  to: string
  name: string
  stat: Stat | undefined
  failed: boolean
  accent?: boolean
  children: ReactNode
}

function Card({ to, name, stat, failed, accent, children }: CardProps) {
  return (
    <li>
      <PageLink to={to} className={`${styles.card} ${accent ? styles.cardAccent : ''}`}>
        <span className={styles.cardHead}>
          <span className={styles.cardName}>{name}</span>
          <span aria-hidden="true" className={styles.cardArrow}>
            →
          </span>
        </span>
        <span className={styles.stat} aria-live="polite">
          <span className={styles.figure}>{failed ? '—' : (stat?.figure ?? '…')}</span>
          <span className={styles.caption}>{failed ? 'not available' : (stat?.caption ?? 'loading')}</span>
        </span>
        <span className={styles.description}>{children}</span>
      </PageLink>
    </li>
  )
}
