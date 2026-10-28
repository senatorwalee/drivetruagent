/**
 * The new home page design's extra sections: how Frontdesk works, why it was
 * built, and the questions people ask first. Plain words, no live data.
 */
import styles from './Home.module.css'

const STEPS = [
  {
    name: 'A guest arrives',
    text: 'A car pulls up to the lane, or the phone rings. Frontdesk greets them by voice.',
  },
  {
    name: 'The agent takes the order',
    text: 'It talks the order through: answers menu questions, suggests today’s special and reads the order back.',
  },
  {
    name: 'The code checks every step',
    text: 'Prices, stock and rules are decided in code. Anything unusual goes to staff with the whole story.',
  },
]

const QUESTIONS = [
  {
    question: 'What happens when it mishears?',
    answer:
      'It reads the order back and asks the guest to confirm before anything is placed. If it gets confused three times, it hands the order to a person with the whole conversation.',
  },
  {
    question: 'Can the agent change a price or give a discount?',
    answer:
      'No. The model proposes and the code decides: prices, discounts, stock and combos all come from the backend, and the agent can only say what the backend allows.',
  },
  {
    question: 'Which languages does it speak?',
    answer: 'English and Canadian French.',
  },
  {
    question: 'Can it take phone orders?',
    answer: 'Yes. It answers calls, remembers a caller’s last orders, and staff can follow every call live.',
  },
  {
    question: 'What does a conversation cost?',
    answer: 'A few cents with OpenAI’s voice model. Each conversation shows its own cost, and the scripted demo costs nothing.',
  },
]

export function HowItWorks() {
  return (
    <section className={styles.section} aria-labelledby="how-title">
      <p className={styles.eyebrow}>How it works</p>
      <h2 id="how-title" className={styles.sectionTitle}>
        From hello to a checked order, in three steps.
      </h2>
      <ol className={styles.steps}>
        {STEPS.map((step, index) => (
          <li key={step.name} className={styles.step}>
            <span className={styles.stepNumber} aria-hidden="true">
              {String(index + 1).padStart(2, '0')}
            </span>
            <h3 className={styles.stepName}>{step.name}</h3>
            <p className={styles.stepText}>{step.text}</p>
          </li>
        ))}
      </ol>
    </section>
  )
}

/** Why Frontdesk exists, in the builder's words. */
export function WhyIBuiltThis() {
  return (
    <section className={styles.section} aria-labelledby="why-title">
      <p className={styles.eyebrow}>Why I built this</p>
      <h2 id="why-title" className={styles.sectionTitle}>
        So the cook can stay in the kitchen.
      </h2>
      <div className={styles.letter}>
        <p>
          I built Frontdesk for a friend who is opening his own small restaurant. He makes every order himself, and he also
          has to answer the phone and take the orders. At the busiest times of day, that is too much for one person.
        </p>
        <p>
          Frontdesk takes that part off his hands. It answers guests, takes and checks their orders, and passes anything
          unusual to him with the whole conversation, so he can keep his attention on the food.
        </p>
        <p>
          It is made for small and mid-size restaurants like his, where the team is small and the budget is tight: each
          conversation costs a few cents.
        </p>
        <p className={styles.signature}>Olawale Tijani · built Frontdesk</p>
      </div>
    </section>
  )
}

export function CommonQuestions() {
  return (
    <section className={styles.section} aria-labelledby="questions-title">
      <p className={styles.eyebrow}>Common questions</p>
      <h2 id="questions-title" className={styles.sectionTitle}>
        What people ask first.
      </h2>
      <div className={styles.questions}>
        {QUESTIONS.map((item) => (
          <details key={item.question} className={styles.question}>
            <summary>{item.question}</summary>
            <p>{item.answer}</p>
          </details>
        ))}
      </div>
    </section>
  )
}
