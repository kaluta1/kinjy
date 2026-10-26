import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, Play, RotateCcw, Send, ShieldCheck, Users, Wallet } from 'lucide-react'
import { FEATURES } from '@/lib/features'
import { cn } from '@/lib/utils'
import { EASE, useReducedMotion } from '@/components/creators/motion-utils'
import { KlButton } from '@/components/landing/PageKit'
import { KL_CARD, KL_CARD_GOLD } from '@/components/landing/kl-classes'

/* ------------------------------------------------------------------ */
/* The direct split: a $120 agency purchase                            */
/* ------------------------------------------------------------------ */
/* This used to be a ten-rung ladder. It is three rows now, and that is the
   point of the programme rather than a simplification of the diagram: the
   seller's money is not Kinjy's to give away, and only one person is paid a
   commission on the buyer's activity. */

const SELLER = { label: 'Seller', amount: 100, note: 'their own asking price, untouched' }

/* While the marketplace is switched off (lib/features.ts) the same split is
   shown on an ad purchase instead. An ad bought from Kinjy is its revenue in
   full, so there is no seller row and the $20 splits directly — the rates are
   the same, only the example changes. */
const PURCHASE = FEATURES.marketplace
  ? {
      title: 'Service purchase · photography session',
      caption: 'seller asks $100 · Kinjy adds a 20% markup · held until the purchase is final',
      total: 120,
      seller: SELLER,
      revenue: 'the $20 markup — Kinjy’s revenue — splits three ways',
      sponsor: 'The buyer’s sponsor',
    }
  : {
      title: 'Ad purchase · local campaign',
      caption: 'an ad bought from Kinjy is its revenue in full — there is no seller to pay',
      total: 20,
      seller: null,
      revenue: 'the $20 — all of it Kinjy’s revenue — splits three ways',
      sponsor: 'The advertiser’s sponsor',
    }

const SPLIT = [
  {
    key: 'sponsor',
    label: PURCHASE.sponsor,
    pct: 20,
    amount: 4,
    note: '20% of Kinjy’s revenue — the one and only commission level',
    tone: 'gold' as const,
  },
  {
    key: 'leaders',
    label: 'Kinjy Leaders',
    pct: 5,
    amount: 1,
    note: 'set aside as it is earned, shared monthly by the top 10,000',
    tone: 'sky' as const,
  },
  {
    key: 'platform',
    label: 'Kinjy platform',
    pct: 75,
    amount: 15,
    note: 'moderation, infrastructure — and it funds the pool',
    tone: 'muted' as const,
  },
]

/** Left: where a purchase actually goes. */
function LevelSplit() {
  const reduced = useReducedMotion()
  // 0 idle · 1 seller paid · 2..4 the markup splits
  const [stage, setStage] = useState(0)
  const [running, setRunning] = useState(false)
  const timers = useRef<number[]>([])

  const clearTimers = () => {
    timers.current.forEach((t) => window.clearTimeout(t))
    timers.current = []
  }
  useEffect(() => clearTimers, [])

  const run = useCallback(() => {
    clearTimers()
    setStage(0)
    setRunning(true)
    for (let s = 1; s <= 4; s += 1) {
      timers.current.push(
        window.setTimeout(
          () => {
            setStage(s)
            if (s === 4) setRunning(false)
          },
          reduced ? 40 * s : 150 + s * 520,
        ),
      )
    }
  }, [reduced])

  return (
    <div className={cn(KL_CARD, 'p-6 sm:p-8')}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h3 className="text-[22px] font-bold tracking-[-0.02em]">One purchase. One commission.</h3>
        <KlButton size="sm" variant={stage >= 4 ? 'ghost' : 'gold'} onClick={run} disabled={running}>
          {stage >= 4 ? (
            <>
              <RotateCcw size={14} aria-hidden="true" /> Replay split
            </>
          ) : (
            <>
              <Play size={14} aria-hidden="true" /> Run the split
            </>
          )}
        </KlButton>
      </div>

      {/* purchase origin */}
      <div className="mt-6 flex items-center gap-4 rounded-card-md border border-gold/30 bg-gold/[0.06] px-4 py-3">
        <span
          className="flex h-10 w-10 items-center justify-center rounded-full"
          style={{ background: 'var(--grad-gold-sheen)' }}
        >
          <Send size={16} className="text-ink" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-text-hi">{PURCHASE.title}</p>
          <p className="caption">{PURCHASE.caption}</p>
        </div>
        <span className="mono-data text-lg font-semibold text-gold-soft">${PURCHASE.total.toFixed(2)}</span>
      </div>

      {/* the seller is paid first, out of their own price */}
      {PURCHASE.seller && (
        <motion.div
          className={cn(
            'mt-4 flex items-center gap-4 rounded-card-md border px-4 py-3 transition-colors duration-300',
            stage >= 1 ? 'border-white/20 bg-white/[0.04]' : 'border-white/10 bg-white/[0.02] opacity-60',
          )}
          initial={false}
          animate={stage >= 1 && !reduced ? { scale: [0.97, 1.01, 1] } : { scale: 1 }}
          transition={{ duration: 0.4, ease: [0.34, 1.56, 0.64, 1] }}
        >
          <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-full border border-white/20 bg-[var(--kl-paper)]">
            <Users size={15} className="text-text-mid" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-text-hi">{PURCHASE.seller.label}</p>
            <p className="caption">{PURCHASE.seller.note}</p>
          </div>
          <span className="mono-data font-semibold text-text-hi">
            {stage >= 1 ? `+$${PURCHASE.seller.amount.toFixed(2)}` : `$${PURCHASE.seller.amount.toFixed(2)}`}
          </span>
        </motion.div>
      )}

      <p className="mono-data mt-5 text-center text-[0.68rem] uppercase tracking-wider text-text-low">
        {PURCHASE.revenue}
      </p>

      <ul className="mt-3 space-y-2.5">
        {SPLIT.map((row, i) => {
          const on = stage > i + 1
          return (
            <motion.li
              key={row.key}
              className={cn(
                'flex items-center gap-4 rounded-card-md border px-4 py-3 transition-colors duration-300',
                on
                  ? row.tone === 'gold'
                    ? 'border-gold/40 bg-gold/[0.06]'
                    : row.tone === 'sky'
                      ? 'border-sky/35 bg-sky/[0.05]'
                      : 'border-white/15 bg-white/[0.03]'
                  : 'border-white/10 bg-white/[0.02] opacity-60',
              )}
              initial={false}
              animate={on && !reduced ? { scale: [0.97, 1.02, 1] } : { scale: 1 }}
              transition={{ duration: 0.4, ease: [0.34, 1.56, 0.64, 1] }}
            >
              <span
                className={cn(
                  'mono-data flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-full border text-[0.7rem] font-bold',
                  on
                    ? row.tone === 'gold'
                      ? 'border-gold bg-gradient-to-br from-gold-soft to-gold text-ink'
                      : row.tone === 'sky'
                        ? 'border-sky bg-sky/25 text-text-hi'
                        : 'border-white/20 bg-[var(--kl-paper)] text-text-mid'
                    : 'border-white/15 bg-[var(--kl-paper)] text-text-low',
                )}
              >
                {row.pct}%
              </span>
              <div className="min-w-0 flex-1">
                <p className={cn('text-sm font-medium', on ? 'text-text-hi' : 'text-text-low')}>{row.label}</p>
                <p className="caption">{row.note}</p>
              </div>
              <AnimatePresence>
                {on && (
                  <motion.span
                    key="amt"
                    className={cn(
                      'mono-data font-semibold',
                      row.tone === 'gold' ? 'text-gold-soft' : row.tone === 'sky' ? 'text-sky' : 'text-text-mid',
                    )}
                    initial={reduced ? false : { opacity: 0, x: 14 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.35, ease: EASE }}
                  >
                    +${row.amount.toFixed(2)}
                  </motion.span>
                )}
              </AnimatePresence>
            </motion.li>
          )
        })}
      </ul>

      <p className="mono-data mt-4 text-xs text-text-low">
        {PURCHASE.seller ? '100 + ' : ''}4 + 1 + 15 = {PURCHASE.total} — every cent allocated in the same
        immutable ledger entry.
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Withdrawals: on request, from $10                                   */
/* ------------------------------------------------------------------ */

const CONDITIONS = [
  { icon: Wallet, title: 'Available balance of $10 or more', text: 'network fee included — below that, it stays in your balance' },
  { icon: ShieldCheck, title: 'Identity verified', text: 'through KinjyKYC, once a year' },
  { icon: Check, title: 'Wallet added and confirmed', text: 'the wallet you want to be paid to, on BSC' },
]

/** Right: how a balance leaves Kinjy. */
function WithdrawalPanel() {
  return (
    <div className={cn(KL_CARD_GOLD, 'p-6 sm:p-8')}>
      <h3 className="text-[22px] font-bold tracking-[-0.02em]">Withdraw when you choose.</h3>
      <p className="caption mt-2">Nothing leaves automatically: you press Withdraw in Payments.</p>
      <ul className="mt-6 space-y-3">
        {CONDITIONS.map((c) => (
          <li key={c.title} className="flex items-start gap-4 rounded-card-md border border-white/10 bg-white/[0.02] px-4 py-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gold/15 text-gold-soft">
              <c.icon size={15} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-text-hi">{c.title}</p>
              <p className="caption mt-0.5">{c.text}</p>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-6 flex items-center gap-4 rounded-card-md border border-gold/30 bg-gold/[0.06] px-4 py-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-full" style={{ background: 'var(--grad-gold-sheen)' }}>
          <Send size={16} className="text-ink" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-text-hi">Sent by Sonco Pay in USDT on BSC</p>
          <p className="caption">The network fee is shown before you confirm and taken from the amount sent.</p>
        </div>
      </div>
    </div>
  )
}

/** Section 4 — one commission level, and withdrawals on request. */
export default function CashoutEngine() {
  const reduced = useReducedMotion()
  return (
    <section className="kl-pad-x scroll-mt-24 border-t border-[var(--kl-paper-2)] py-[clamp(72px,9vw,120px)]">
      <div>
        <h2 className="kl-h2 max-w-[900px]">
          One sponsor, paid from our own margin.{' '}
          <span className="text-[var(--kl-gold-deep)]">Withdraw from $10.</span>
        </h2>
        <p className="kl-lead mt-6 !max-w-[680px]">
          Every purchase pays one commission: 20% of Kinjy’s revenue to the buyer’s sponsor, recorded in
          the ledger as it is earned. Once your available balance reaches $10, network fee included, you
          withdraw it on request.
        </p>
        <motion.div
          className="mt-12 grid items-start gap-8 grid-cols-[minmax(0,1fr)] lg:grid-cols-2"
          initial={reduced ? false : { opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-10%' }}
          transition={{ duration: 0.7, ease: EASE }}
        >
          <LevelSplit />
          <WithdrawalPanel />
        </motion.div>
      </div>
    </section>
  )
}
