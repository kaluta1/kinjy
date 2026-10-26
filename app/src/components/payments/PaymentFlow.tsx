import { motion } from 'framer-motion'
import { BookLock, CreditCard, Lock, Receipt, Wallet } from 'lucide-react'
import { EASE, useReducedMotion } from '@/components/creators/motion-utils'
import { FEATURES } from '@/lib/features'
import { cn } from '@/lib/utils'
import { KL_CARD } from '@/components/landing/kl-classes'

const NODES = [
  {
    icon: CreditCard,
    title: 'You pay',
    sub: 'through Sonco Pay, on Kinjy’s own page, with the methods offered in your country',
    mono: 'payment confirmed',
  },
  {
    icon: Receipt,
    title: 'Kaluta Pay records it',
    sub: 'the invoice is read back from the provider before anything moves, and the split is recorded',
    mono: 'invoice + split',
  },
  {
    icon: BookLock,
    title: 'Kinjy’s ledger',
    sub: 'every amount is a double-entry posting; corrections are new entries, never edits',
    mono: 'journal posted',
  },
  ...(FEATURES.marketplace
    ? [
        {
          icon: Lock,
          title: 'Held when it is a marketplace order',
          sub: 'the payment waits until the purchase is final — never released by a deadline or a seller’s note',
          mono: 'held until final',
        },
      ]
    : []),
  {
    icon: Wallet,
    title: 'Balances you can withdraw',
    sub: 'commissions and settled sales reach your balance; withdraw from $10 in USDT on BSC',
    mono: 'withdraw on request',
  },
]

/** Section 2 — the path of one payment, from the checkout to a balance. */
export default function PaymentFlow() {
  const reduced = useReducedMotion()
  const last = NODES.length - 1

  return (
    <section id="payment-flow" className="kl-pad-x scroll-mt-24 border-t border-[var(--kl-paper-2)] py-[clamp(72px,9vw,120px)]">
      <div>
        <h2 className="kl-h2 max-w-[900px]">
          One payment. <span className="text-[var(--kl-gold-deep)]">One record, end to end.</span>
        </h2>
        <p className="kl-lead mt-6 !max-w-[680px]">
          Sonco Pay takes the payment. Kaluta Pay connects to Sonco Pay and is the base of Kinjy’s
          accounting: it confirms the invoice, keeps the split of the payment and holds what is owed
          until it is due. Kinjy’s ledger records every amount, so what a member sees can always be
          traced back to the payment it came from.
        </p>

        <div className={cn('mt-14 grid gap-4', NODES.length === 5 ? 'md:grid-cols-5' : 'md:grid-cols-4')}>
          {NODES.map((n, i) => (
            <motion.div
              key={n.title}
              className={cn(KL_CARD, 'h-full p-5', i === last && '!border-[var(--kl-gold)]')}
              initial={reduced ? false : { opacity: 0, y: 28 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-15%' }}
              transition={{ delay: i * 0.2, duration: 0.6, ease: EASE }}
            >
              <div className="flex items-center gap-3">
                <span
                  className="flex h-9 w-9 items-center justify-center rounded-full"
                  style={{ background: i === last ? 'var(--grad-gold-sheen)' : 'var(--grad-orb)' }}
                >
                  <n.icon size={16} className="text-ink" aria-hidden="true" />
                </span>
                <span className="mono-data text-xs text-text-low">0{i + 1}</span>
              </div>
              <h3 className="mt-4 font-sans text-base font-semibold text-text-hi">{n.title}</h3>
              <p className="caption mt-1.5">{n.sub}</p>
              <p className="mono-data mt-3 rounded-card-sm border border-white/10 bg-[var(--kl-paper)] px-2.5 py-1.5 text-[0.7rem] text-gold-soft">
                {n.mono}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}
