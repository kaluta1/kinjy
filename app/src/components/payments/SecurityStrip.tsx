import { motion } from 'framer-motion'
import { BookLock, Fingerprint, KeyRound, Network, Wallet } from 'lucide-react'
import { EASE, useReducedMotion } from '@/components/creators/motion-utils'
import { KL_CARD } from '@/components/landing/kl-classes'
import { cn } from '@/lib/utils'

const ITEMS = [
  {
    icon: Fingerprint,
    title: 'Signed notifications',
    text: 'Sonco Pay and Kaluta Pay notifications are trusted only when their signature checks out — and the payment is read back from the provider before anything moves.',
  },
  {
    icon: Network,
    title: 'Exact amounts',
    text: 'A payment opens an order or a fee only when it matches, to the cent, the amount Kinjy’s server set.',
  },
  {
    icon: KeyRound,
    title: 'Verified identity',
    text: 'Withdrawals are for members whose identity is verified.',
  },
  {
    icon: Wallet,
    title: 'Confirmed wallets',
    text: 'Money is sent only to the wallet you added and confirmed for your account.',
  },
  {
    icon: BookLock,
    title: 'Append-only ledger',
    text: 'Every movement is a ledger entry. A correction is a new entry, never an edit.',
  },
]

/** Section 5 — Security strip. */
export default function SecurityStrip() {
  const reduced = useReducedMotion()
  return (
    <section className="kl-pad-x scroll-mt-24 border-t border-[var(--kl-paper-2)] py-[clamp(72px,9vw,120px)]">
      <div>
        <h2 className="kl-h2 max-w-[900px]">
          Money moves on <span className="text-[var(--kl-gold-deep)]">five locks.</span>
        </h2>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {ITEMS.map((it, i) => (
            <motion.div
              key={it.title}
              className={cn(KL_CARD, 'transition-transform hover:-translate-y-0.5 p-5')}
              initial={reduced ? false : { opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-12%' }}
              transition={{ delay: i * 0.09, duration: 0.5, ease: EASE }}
            >
              <span className="flex h-10 w-10 items-center justify-center rounded-full" style={{ background: 'var(--grad-orb)' }}>
                <it.icon size={16} className="text-ink" aria-hidden="true" />
              </span>
              <h3 className="mt-4 text-sm font-semibold text-text-hi">{it.title}</h3>
              <p className="caption mt-1.5">{it.text}</p>
            </motion.div>
          ))}
        </div>
        <p className="mono-data mt-8 text-center text-xs text-text-low">
          USDT on BSC · minimum withdrawal $10, network fee included
        </p>
      </div>
    </section>
  )
}
