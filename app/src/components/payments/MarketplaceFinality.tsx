import { motion } from 'framer-motion'
import { BadgeCheck, BookOpen, Download, Hand, LifeBuoy, PackageCheck, ShieldOff, Wrench } from 'lucide-react'
import { EASE, useReducedMotion } from '@/components/creators/motion-utils'
import { FEATURES } from '@/lib/features'
import { cn } from '@/lib/utils'
import { KL_CARD, KL_CARD_GOLD } from '@/components/landing/kl-classes'

const PHYSICAL = [
  { icon: Hand, text: 'You pay. The payment is held — the seller is not paid yet.' },
  { icon: PackageCheck, text: 'The seller or courier shows you the product, and you inspect it.' },
  { icon: BadgeCheck, text: 'You confirm receipt in your own Kinjy account. That makes the purchase final and starts the seller’s settlement.' },
  { icon: Hand, text: 'The seller checks your confirmation in their own back office, then hands the product over.' },
]

const EBOOK = [
  { icon: Hand, text: 'You pay. The ebook appears in your library; the payment is held.' },
  { icon: Download, text: 'Your first successful download makes the purchase final and starts the seller’s settlement.' },
]

const NEVER = [
  'a deadline passing',
  'a seller marking an order shipped or delivered',
  'a courier’s delivery scan',
  'a screenshot, an email or a message',
]

/** Section 3 — when a marketplace purchase becomes final, and what that means. */
export default function MarketplaceFinality() {
  const reduced = useReducedMotion()
  if (!FEATURES.marketplace) return null

  const card = (delay: number) =>
    reduced ? {} : { initial: { opacity: 0, y: 24 }, whileInView: { opacity: 1, y: 0 }, viewport: { once: true, margin: '-12%' }, transition: { delay, duration: 0.55, ease: EASE } }

  return (
    <section className="kl-pad-x scroll-mt-24 border-t border-[var(--kl-paper-2)] py-[clamp(72px,9vw,120px)]">
      <div>
        <h2 className="kl-h2 max-w-[900px]">
          Held until <span className="text-[var(--kl-gold-deep)]">you</span> make it final.
        </h2>
        <p className="kl-lead mt-6 !max-w-[680px]">
          On the marketplace, your payment is held until the purchase becomes final — and only you can make
          it final, from your own account. Confirm only when the right product is in front of you, inspected
          and ready to be handed over.
        </p>

        <div className="mt-12 grid items-start gap-6 grid-cols-[minmax(0,1fr)] lg:grid-cols-2">
          <motion.div className={cn(KL_CARD_GOLD, 'p-6 sm:p-8')} {...card(0)}>
            <h3 className="text-[22px] font-bold tracking-[-0.02em]">Products and services</h3>
            <ol className="mt-6 space-y-4">
              {PHYSICAL.map((s, i) => (
                <li key={s.text} className="flex items-start gap-3">
                  <span className="mono-data flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gold/15 text-xs font-bold text-gold-soft">
                    {i + 1}
                  </span>
                  <p className="text-sm leading-relaxed text-text-mid">{s.text}</p>
                </li>
              ))}
            </ol>
            <p className="caption mt-5">
              For a service, you confirm once it has been performed or previewed as described in the listing;
              you are never asked to confirm a service that has not happened. Final files are released after
              the seller checks your confirmation.
            </p>
          </motion.div>

          <div className="grid gap-6">
            <motion.div className={cn(KL_CARD, 'p-6 sm:p-8')} {...card(0.1)}>
              <h3 className="flex items-center gap-2 text-[22px] font-bold tracking-[-0.02em]">
                <BookOpen size={20} className="text-gold-soft" aria-hidden="true" /> Ebooks
              </h3>
              <ol className="mt-5 space-y-4">
                {EBOOK.map((s, i) => (
                  <li key={s.text} className="flex items-start gap-3">
                    <span className="mono-data flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sky/15 text-xs font-bold text-sky">
                      {i + 1}
                    </span>
                    <p className="text-sm leading-relaxed text-text-mid">{s.text}</p>
                  </li>
                ))}
              </ol>
              <p className="caption mt-5">
                A download that fails, a link that is only created, or the book simply appearing in your
                library does not make the purchase final.
              </p>
            </motion.div>

            <motion.div className={cn(KL_CARD, 'p-6 sm:p-8')} {...card(0.2)}>
              <h3 className="flex items-center gap-2 text-lg font-bold">
                <ShieldOff size={18} className="text-gold-soft" aria-hidden="true" /> What never releases the payment
              </h3>
              <ul className="mt-4 space-y-2">
                {NEVER.map((n) => (
                  <li key={n} className="text-sm text-text-mid">— {n}</li>
                ))}
              </ul>
            </motion.div>
          </div>
        </div>

        <div className="mt-6 grid gap-6 grid-cols-[minmax(0,1fr)] lg:grid-cols-2">
          <motion.div className={cn(KL_CARD, 'p-6 sm:p-8')} {...card(0.1)}>
            <h3 className="flex items-center gap-2 text-lg font-bold">
              <Wrench size={18} className="text-gold-soft" aria-hidden="true" /> After the purchase is final
            </h3>
            <p className="mt-3 text-sm leading-relaxed text-text-mid">
              Kinjy does not arbitrate ordinary marketplace disputes. Questions about quality, repair,
              replacement or warranty go to the seller — or to the insurer when the listing includes
              insurance — under the seller’s guarantee or warranty if there is one, and subject to the
              consumer rights that apply where you live. Every listing says whether a guarantee, a warranty or
              insurance exists, and says so plainly when there is none, before you pay.
            </p>
          </motion.div>
          <motion.div className={cn(KL_CARD, 'p-6 sm:p-8')} {...card(0.2)}>
            <h3 className="flex items-center gap-2 text-lg font-bold">
              <LifeBuoy size={18} className="text-gold-soft" aria-hidden="true" /> What Kinjy support still handles
            </h3>
            <p className="mt-3 text-sm leading-relaxed text-text-mid">
              A payment that failed or was taken twice, a product not handed over after you confirmed, fraud
              reports, and anything the law requires of us. A confirmation in an app cannot physically force a
              handover, so confirm only at the moment of handover.
            </p>
          </motion.div>
        </div>
      </div>
    </section>
  )
}
