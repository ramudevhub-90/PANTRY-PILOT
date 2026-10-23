import { ArrowRight, Camera, ChefHat, Code2, Languages, MessageCircle, RefreshCw, ShoppingBag } from 'lucide-react';

const FEATURES = [
  {
    icon: ChefHat,
    title: 'Meals from what you have',
    text: 'Ask what to cook and Amazon Nova suggests dishes for the time of day, your city and your home cuisine, using the food that expires first.',
  },
  {
    icon: Camera,
    title: 'Snap a bill or your fridge',
    text: 'Photos, PDF bills, order screenshots or a 10-second fridge video. Nova lists the items, quantities and shelf life; you check before saving.',
  },
  {
    icon: RefreshCw,
    title: 'Orders sync themselves',
    text: 'Connect Swiggy Instamart and Zepto and your deliveries land in the pantry, without cancelled orders or non-food items.',
  },
  {
    icon: ShoppingBag,
    title: 'Compare and order, with your OK',
    text: 'Live prices across your apps, the cart read back to you, and an order only after you say “place the order”. Cash on delivery by default.',
  },
  {
    icon: MessageCircle,
    title: 'On WhatsApp too',
    text: 'Ask what’s expiring, send a photo of your shopping, and get order updates and payment links where you already chat.',
  },
  {
    icon: Languages,
    title: 'Speaks your language',
    text: 'Ask in English, Hindi or Bengali and hear the answer back in the same language, with hands-free “Alexa, …” in any browser.',
  },
];

const STEPS = [
  { title: 'You ask Alexa+', text: '“Alexa, what can I make for dinner?”' },
  { title: 'Pantry Pilot answers', text: 'An MCP server on AWS that knows your pantry, plans and shops.' },
  { title: 'Nova and your apps do the work', text: 'Amazon Nova reasons and reads; Zepto, Swiggy and Kroger fill the cart.' },
];

const WORKS_WITH = ['Alexa+', 'Claude', 'WhatsApp', 'Swiggy Instamart', 'Zepto', 'BigBasket', 'Kroger'];

/** What signed-out visitors see first; "Sign in" opens the email-code sign-in. */
export function Landing({ onSignIn }: { onSignIn: () => void }) {
  return (
    <div className="landing">
      <nav className="landing__nav">
        <div className="auth-brand">
          <span className="brand__mark">
            <ChefHat size={20} />
          </span>
          <div>
            <strong>Pantry Pilot</strong>
            <span>Kitchen agent for Alexa+</span>
          </div>
        </div>
        <button type="button" className="btn btn--secondary btn--sm" onClick={onSignIn}>
          Sign in
        </button>
      </nav>

      <header className="landing__hero">
        <div className="landing__copy">
          <p className="landing__eyebrow">Kitchen agent for Alexa+ · Powered by Amazon Nova</p>
          <h1>Alexa+ finally knows what’s in your kitchen.</h1>
          <p className="landing__lead">
            Pantry Pilot tracks the food you have and when it goes bad, plans meals around it, and shops on Zepto, Swiggy
            Instamart and Kroger. Every order waits for your OK.
          </p>
          <div className="landing__ctas">
            <button type="button" className="btn" onClick={onSignIn}>
              Get started <ArrowRight size={16} aria-hidden="true" />
            </button>
            <a className="btn btn--secondary" href="https://github.com/ramudevhub-90/PANTRY-PILOT" target="_blank" rel="noreferrer">
              <Code2 size={16} aria-hidden="true" /> View on GitHub
            </a>
          </div>
          <p className="landing__note">Free to try. Sign in with a 6-digit email code, no password.</p>
        </div>

        <div className="landing__demo" aria-label="Example conversation">
          <div className="landing__bubble landing__bubble--you">Alexa, what should I cook for lunch?</div>
          <div className="landing__bubble landing__bubble--alexa">
            Palak paneer. Your spinach and paneer expire tomorrow, and you have everything except cream. Should I add cream to
            your Zepto cart?
          </div>
          <div className="landing__chips">
            <span className="landing__chip landing__chip--urgent">🥬 Spinach · 1 day</span>
            <span className="landing__chip landing__chip--soon">🧀 Paneer · 2 days</span>
            <span className="landing__chip">🧅 Onions · 3 weeks</span>
          </div>
          <div className="landing__bubble landing__bubble--you">Yes, and compare prices first.</div>
          <div className="landing__bubble landing__bubble--alexa">Zepto has your whole list for ₹131, Swiggy is ₹146. Want me to fill the Zepto cart?</div>
        </div>
      </header>

      <section className="landing__section" aria-labelledby="features-title">
        <h2 id="features-title">Everything your kitchen needs to remember</h2>
        <div className="landing__grid">
          {FEATURES.map(({ icon: Icon, title, text }) => (
            <article key={title} className="landing__card">
              <span className="landing__icon">
                <Icon size={20} aria-hidden="true" />
              </span>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing__section" aria-labelledby="how-title">
        <h2 id="how-title">How it works</h2>
        <ol className="landing__steps">
          {STEPS.map((s, i) => (
            <li key={s.title}>
              <span className="landing__step-num">{i + 1}</span>
              <div>
                <strong>{s.title}</strong>
                <p>{s.text}</p>
              </div>
            </li>
          ))}
        </ol>
        <div className="landing__works">
          <span>Works with</span>
          {WORKS_WITH.map((w) => (
            <span key={w} className="landing__chip">
              {w}
            </span>
          ))}
        </div>
      </section>

      <section className="landing__final">
        <h2>Waste less food. Decide dinner faster.</h2>
        <button type="button" className="btn" onClick={onSignIn}>
          Get started <ArrowRight size={16} aria-hidden="true" />
        </button>
      </section>

      <footer className="landing__footer">
        <span>
          <strong>Pantry Pilot</strong> · Built for the Amazon Build, Ship, Shape hackathon
        </span>
        <span>
          <a href="/privacy.html">Privacy policy</a> · <a href="https://github.com/ramudevhub-90/PANTRY-PILOT">GitHub</a> ·{' '}
          <a href="mailto:ramu.devhub@gmail.com">ramu.devhub@gmail.com</a>
        </span>
      </footer>
    </div>
  );
}
