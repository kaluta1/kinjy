import { ShieldCheck } from 'lucide-react'
import type { ProductProtection } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * "Before you buy": what the seller declared, and what Kinjy does not do.
 *
 * A guarantee or insurance is shown only when the seller declared one; when
 * they did not, that is said in so many words, never left to be assumed.
 */
export default function ProtectionSummary({
  protection,
  compact = false,
}: {
  protection: ProductProtection
  /** Checkout: collapsed behind a summary line, so the policy stays in view. */
  compact?: boolean
}) {
  const { guarantee, insurance, service_terms: serviceTerms, final_when: finalWhen } = protection
  const body = (
    <div className="space-y-3 text-sm leading-relaxed">
      <div>
        <p className="font-semibold text-text-hi">Seller guarantee or warranty</p>
        {guarantee ? (
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-text-mid">
            <dt className="text-text-low">Covers</dt>
            <dd>{guarantee.scope}</dd>
            <dt className="text-text-low">For</dt>
            <dd>{guarantee.duration}</dd>
            {guarantee.exclusions && (
              <>
                <dt className="text-text-low">Not covered</dt>
                <dd>{guarantee.exclusions}</dd>
              </>
            )}
            <dt className="text-text-low">Claims</dt>
            <dd className="break-words">{guarantee.claim_contact}</dd>
          </dl>
        ) : (
          <p className="mt-1 text-text-mid">No seller guarantee or warranty is offered.</p>
        )}
      </div>

      <div>
        <p className="font-semibold text-text-hi">Insurance</p>
        {insurance ? (
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-text-mid">
            <dt className="text-text-low">Insurer</dt>
            <dd>{insurance.insurer}</dd>
            <dt className="text-text-low">Covers</dt>
            <dd>{insurance.coverage}</dd>
            {insurance.exclusions && (
              <>
                <dt className="text-text-low">Not covered</dt>
                <dd>{insurance.exclusions}</dd>
              </>
            )}
            <dt className="text-text-low">Claims</dt>
            <dd>{insurance.claim_process}</dd>
            <dt className="text-text-low">Premium</dt>
            <dd>{insurance.premium_included ? 'Included in the price' : 'Not included in the price'}</dd>
          </dl>
        ) : (
          <p className="mt-1 text-text-mid">No insurance is included.</p>
        )}
      </div>

      {serviceTerms && (
        <div>
          <p className="font-semibold text-text-hi">When the service counts as completed</p>
          <p className="mt-1 whitespace-pre-line text-text-mid">{serviceTerms}</p>
        </div>
      )}

      <div>
        <p className="font-semibold text-text-hi">When the purchase becomes final</p>
        <p className="mt-1 text-text-mid">
          {finalWhen === 'first_download'
            ? 'At your first successful download of the ebook.'
            : 'When you confirm receipt in your own Kinjy account, after inspecting it.'}{' '}
          Your payment is held until then.
        </p>
      </div>

      <p className="text-text-mid">
        Kinjy does not arbitrate ordinary marketplace disputes. After the purchase is final, questions go to the
        seller{insurance ? ' or the insurer' : ''}, subject to the rights the law gives you.
      </p>
    </div>
  )

  if (compact) {
    return (
      <details className="group rounded-2xl border border-white/10 bg-ink-2/60 p-3">
        <summary className="flex min-h-[32px] cursor-pointer list-none items-center gap-2 text-sm font-semibold text-text-hi">
          <ShieldCheck size={15} className="shrink-0 text-gold-soft" aria-hidden="true" />
          Before you buy: {guarantee ? 'guarantee' : 'no guarantee'}, {insurance ? 'insurance' : 'no insurance'}
          <span className="ms-auto text-xs font-normal text-text-low group-open:hidden">Details</span>
        </summary>
        <div className="mt-3">{body}</div>
      </details>
    )
  }

  return (
    <section
      aria-labelledby="before-you-buy"
      className={cn('rounded-3xl border border-white/10 bg-ink-2/40 p-5')}
    >
      <h2 id="before-you-buy" className="mb-3 flex items-center gap-2 text-base font-semibold text-text-hi">
        <ShieldCheck size={17} className="text-gold-soft" aria-hidden="true" />
        Before you buy
      </h2>
      {body}
    </section>
  )
}
