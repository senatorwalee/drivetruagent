import { useState } from 'react'

import { HomeLink } from '../app/HomeLink'
import { LIVE_PATH, ONBOARDING_PATH, STAFF_PATH } from '../app/navigation'
import { PageLink } from '../app/PageLink'
import { HOME_DESIGNS } from './useHomeDesign'
import type { HomeDesign } from './useHomeDesign'
import styles from './BusinessHome.module.css'

const EXAMPLES = {
  phone: {
    label: 'Phone orders',
    title: 'The phone rings. Service carries on.',
    description: 'Give guests a way to order without pulling someone away from the counter. Frontdesk talks through the menu, takes their order, and reads it back for confirmation.',
    guest: 'Could I get two chicken sandwiches for pickup?',
    reply: 'Of course. Would you like to make either one a combo?',
    detail: 'Order details stay together, ready for your team.',
  },
  lane: {
    label: 'Drive-through',
    title: 'A warm welcome at the lane.',
    description: 'Help guests choose, ask about options, and confirm their order in English or French. Prices, specials, and availability come from your restaurant’s menu.',
    guest: 'Bonjour, est-ce que vous avez un spécial aujourd’hui?',
    reply: 'Bonjour! Je peux vous présenter les spéciaux du jour.',
    detail: 'English and Canadian French, with menu checks at every step.',
  },
  handoff: {
    label: 'Staff handoffs',
    title: 'Your team steps in with the whole story.',
    description: 'When a request needs a person, Frontdesk passes along the conversation and the order so far. Your team can pick up where the guest left off.',
    guest: 'I’d like to speak with someone about my order.',
    reply: 'I’ll bring in a team member and share the details so far.',
    detail: 'The conversation, the cart, and the reason for the handoff.',
  },
} as const

type Example = keyof typeof EXAMPLES

const QUESTIONS = [
  ['Can I use my own menu?', 'Yes. Add your items, prices, options, and specials in the menu workspace. Frontdesk checks orders against those details and your current availability.'],
  ['What if a guest needs a person?', 'Your team can take over. Frontdesk sends the conversation and the order so far to the staff workspace, with the reason help is needed.'],
  ['Which languages does Frontdesk support?', 'Frontdesk supports English and Canadian French for phone and drive-through ordering.'],
  ['How do I get started?', 'Try an order to explore the experience, then use restaurant setup to add your restaurant details and menu. Voice and phone service depend on your restaurant’s configuration.'],
] as const

export function BusinessHome({ design, chooseDesign }: { design: HomeDesign; chooseDesign: (design: HomeDesign) => void }) {
  const [example, setExample] = useState<Example>('phone')
  const selected = EXAMPLES[example]

  return (
    <div className={styles.screen} id="business-top">
      <header className={styles.header}>
        <HomeLink />
        <nav className={styles.navigation} aria-label="Homepage navigation">
          <a href="#business-benefits">Why Frontdesk</a>
          <a href="#business-product">How it works</a>
          <a href="#business-questions">FAQs</a>
        </nav>
        <div className={styles.headerEnd}>
          <div className={styles.designs} role="group" aria-label="Home page design">
            {HOME_DESIGNS.map((option) => (
              <button key={option.id} type="button" aria-pressed={design === option.id} onClick={() => chooseDesign(option.id)}>
                {option.label}
              </button>
            ))}
          </div>
          <PageLink to={STAFF_PATH} className={styles.workspace}>Workspace</PageLink>
        </div>
      </header>

      <main>
        <section className={styles.hero} aria-labelledby="business-title">
          <div className={styles.heroCopy}>
            <p className={styles.eyebrow}>A little help for your busiest hours</p>
            <h1 id="business-title">Keep orders moving.<br /><em>Stay focused<br className={styles.desktopBreak} /> on service.</em></h1>
            <p className={styles.lede}>AI ordering for restaurants with a lot on their plate. Frontdesk handles phone and drive-through orders, so your team can give guests their full attention.</p>
            <div className={styles.actions}>
              <PageLink to={LIVE_PATH} className={styles.primary}>Try an order</PageLink>
              <a href="#business-product" className={styles.secondary}>See how it works</a>
            </div>
            <p className={styles.heroNote}>Your menu. English & French. A person when it matters.</p>
          </div>
          <div className={styles.heroVisual}>
            <img className={styles.heroImage} src="/images/frontdesk-service.jpg" alt="Restaurant owner handing a takeaway order to a guest at the counter" width="1200" height="1000" fetchPriority="high" />
            <div className={styles.imageCaption}>
              <span className={styles.captionLabel}>Made for the way you serve</span>
              <p>More attention for the guest<br />right in front of you.</p>
            </div>
          </div>
        </section>

        <section className={styles.benefits} id="business-benefits" aria-labelledby="benefits-title">
          <div className={styles.sectionIntro}>
            <p className={styles.eyebrow}>Less juggling. More hospitality.</p>
            <h2 id="benefits-title">Good service starts with<br />a team that has time.</h2>
          </div>
          <div className={styles.benefitGrid}>
            <article><span className={styles.number}>01</span><h3>Keep your attention on service</h3><p>Let Frontdesk handle the ordering conversation while your team looks after the kitchen, the counter, and your guests.</p></article>
            <article><span className={styles.number}>02</span><h3>Your menu stays in control</h3><p>Prices, specials, and availability are checked against your menu. Guests confirm the details before an order is placed.</p></article>
            <article><span className={styles.number}>03</span><h3>Bring in a person with context</h3><p>When your team needs to step in, the conversation and order come with them. No starting from scratch.</p></article>
          </div>
        </section>

        <section className={styles.productSection} id="business-product" aria-labelledby="product-title">
          <div className={styles.productHeader}>
            <div><p className={styles.eyebrow}>Frontdesk, on your team</p><h2 id="product-title">Built around a real<br />restaurant shift.</h2></div>
            <p>From the first hello to the final confirmation, give every order the attention it needs.</p>
          </div>
          <div className={styles.tabs} role="group" aria-label="Explore ordering scenarios">
            {(Object.keys(EXAMPLES) as Example[]).map((key) => <button key={key} type="button" aria-pressed={example === key} aria-controls="business-scenario" onClick={() => setExample(key)}>{EXAMPLES[key].label}</button>)}
          </div>
          <div className={styles.productBody} id="business-scenario">
            <div className={styles.productCopy}>
              <h3>{selected.title}</h3>
              <p>{selected.description}</p>
              <PageLink to={example === 'handoff' ? STAFF_PATH : LIVE_PATH} className={styles.textLink}>{example === 'handoff' ? 'Explore staff handoffs' : 'Try an order yourself'}</PageLink>
            </div>
            <div className={styles.conversation} aria-live="polite" aria-atomic="true">
              <div className={styles.conversationHeader}><strong>Frontdesk</strong><span>Illustrative conversation</span></div>
              <div className={styles.guest}><span>Guest</span><p>{selected.guest}</p></div>
              <div className={styles.assistant}><span>Frontdesk</span><p>{selected.reply}</p></div>
              <p className={styles.conversationDetail}>{selected.detail}</p>
            </div>
          </div>
        </section>

        <section className={styles.origin} id="business-story" aria-labelledby="origin-title">
          <figure className={styles.originPhoto}>
            <img src="/images/frontdesk-hands-full-v2.jpg" alt="Restaurant owner seen from behind preparing food, with a ringing phone in the foreground showing Incoming call and visible ringing marks" width="1200" height="800" loading="lazy" />
            <figcaption>The lunch rush doesn’t wait for the phone.</figcaption>
          </figure>
          <div className={styles.originCopy}>
            <p className={styles.eyebrow}>Hands full. Phone ringing.</p>
            <h2 id="origin-title">You started a restaurant.<br /><em>Not a juggling act.</em></h2>
            <p>The food needs finishing. A guest is waiting. And the phone is ringing again. Frontdesk handles the ordering conversation, so you can stay with the work in front of you.</p>
            <p>Frontdesk began with a friend opening a small restaurant, cooking the food and answering the phone himself. It’s built for teams like his: hands full, guests waiting, and every order worth getting right.</p>
          </div>
        </section>

        <section className={styles.faq} id="business-questions" aria-labelledby="business-faq-title">
          <div><p className={styles.eyebrow}>A few things you might be wondering</p><h2 id="business-faq-title">Let’s talk details.</h2></div>
          <div className={styles.questions}>{QUESTIONS.map(([question, answer]) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>)}</div>
        </section>

        <section className={styles.closing} aria-labelledby="business-closing-title">
          <p className={styles.eyebrow}>Meet your restaurant’s next helping hand</p>
          <h2 id="business-closing-title">Hear what a little help sounds like.</h2>
          <p>Try an order and see how Frontdesk fits into your service.</p>
          <div className={styles.actions}><PageLink to={LIVE_PATH} className={styles.primary}>Try an order</PageLink><PageLink to={ONBOARDING_PATH} className={styles.secondary}>Set up your restaurant</PageLink></div>
        </section>
      </main>
      <footer className={styles.footer}><HomeLink /><p>A little help. A lot more hospitality.</p><a href="#business-top">Back to top</a></footer>
    </div>
  )
}
