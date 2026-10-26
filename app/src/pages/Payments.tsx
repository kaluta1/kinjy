import { Link } from 'react-router'
import { ArrowRight } from 'lucide-react'
import PaymentsHero from '@/components/payments/PaymentsHero'
import PaymentFlow from '@/components/payments/PaymentFlow'
import MarketplaceFinality from '@/components/payments/MarketplaceFinality'
import CashoutEngine from '@/components/payments/CashoutEngine'
import SecurityStrip from '@/components/payments/SecurityStrip'
import { useReducedMotion } from '@/components/creators/motion-utils'
import PublicShell from '@/components/landing/PublicShell'
import { ClosingStage } from '@/components/landing/PageKit'
import { KL_BTN_GHOST, KL_BTN_GOLD } from '@/components/landing/kl-classes'

/** Section 6 — CTA, on the landing's closing paper stage. */
function PaymentsCta() {
  const reduced = useReducedMotion()
  return (
    <ClosingStage
      eyebrow="Get paid"
      title={
        <>
          Verify your account. Add your wallet. <span className="text-[var(--kl-gold-deep)]">Get paid.</span>
        </>
      }
    >
      <div className="mt-12 flex flex-col items-center justify-center gap-4 sm:flex-row">
        <Link to="/app" className={KL_BTN_GOLD}>
          Open your backoffice <ArrowRight size={17} aria-hidden="true" />
        </Link>
        <button
          type="button"
          className={KL_BTN_GHOST}
          onClick={() => document.getElementById('payment-flow')?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' })}
        >
          See how a payment flows
        </button>
      </div>
      <Link to="/creators" className="mt-8 inline-flex items-center gap-2 font-semibold text-[var(--kl-gold-deep)] transition-all hover:gap-3">
        How commissions are earned <ArrowRight size={16} aria-hidden="true" />
      </Link>
    </ClosingStage>
  )
}

/** /payments — Sonco Pay payments, Kaluta Pay accounting, marketplace finality, withdrawals. */
export default function Payments() {
  return (
    <PublicShell>
      <PaymentsHero />
      <PaymentFlow />
      <MarketplaceFinality />
      <CashoutEngine />
      <SecurityStrip />
      <PaymentsCta />
    </PublicShell>
  )
}
