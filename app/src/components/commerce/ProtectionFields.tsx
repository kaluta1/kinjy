import type { ProductGuarantee, ProductInsurance } from '@/lib/api'
import type { Problem, ProtectionDraft } from '@/components/commerce/protectionDraft'
import { cn } from '@/lib/utils'

/** The seller's guarantee, insurance and, for a service, completion terms. */
export default function ProtectionFields({
  draft,
  onChange,
  isService,
  prefix,
  fieldClass,
  invalidClass,
  problemFor,
}: {
  draft: ProtectionDraft
  onChange: (next: ProtectionDraft) => void
  isService: boolean
  /** Keeps ids unique when two forms are on the page. */
  prefix: string
  fieldClass: string
  invalidClass: string
  /** The gap to show next to a field, once the seller has tried to save. */
  problemFor: (id: string) => Problem | undefined
}) {
  const setG = (key: keyof ProductGuarantee) => (value: string) =>
    onChange({ ...draft, guarantee: { ...draft.guarantee, [key]: value } })
  const setI = (key: Exclude<keyof ProductInsurance, 'premium_included'>) => (value: string) =>
    onChange({ ...draft, insurance: { ...draft.insurance, [key]: value } })

  const input = (
    id: string,
    label: string,
    value: string,
    onValue: (v: string) => void,
    opts: { max: number; placeholder?: string; multiline?: boolean },
  ) => {
    const problem = problemFor(id)
    const props = {
      id,
      value,
      maxLength: opts.max,
      placeholder: opts.placeholder,
      'aria-invalid': problem ? true : undefined,
      'aria-describedby': problem ? `${id}-error` : undefined,
      className: cn(fieldClass, opts.multiline && 'resize-y', problem && invalidClass),
    }
    return (
      <div>
        <label htmlFor={id} className="caption mb-1.5 block">
          {label}
        </label>
        {opts.multiline ? (
          <textarea {...props} rows={3} onChange={(e) => onValue(e.target.value)} />
        ) : (
          <input {...props} onChange={(e) => onValue(e.target.value)} />
        )}
        {problem && (
          <p id={`${id}-error`} className="mt-1 text-xs text-red-200">
            {problem.message}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {isService &&
        input(
          `${prefix}-service-terms`,
          'What completing this service means and how the buyer can check it',
          draft.serviceTerms,
          (v) => onChange({ ...draft, serviceTerms: v }),
          {
            max: 3000,
            multiline: true,
            placeholder: 'Three logo proposals, then the chosen one in vector formats. The buyer checks the files open.',
          },
        )}

      <fieldset className="rounded-2xl border border-white/10 p-4">
        <legend className="px-1 text-sm font-semibold text-text-hi">Guarantee or warranty</legend>
        <label className="flex cursor-pointer items-start gap-2.5 text-sm text-text-hi">
          <input
            type="checkbox"
            checked={draft.offerGuarantee}
            onChange={(e) => onChange({ ...draft, offerGuarantee: e.target.checked })}
            className="mt-0.5 h-5 w-5 shrink-0 accent-gold"
          />
          <span>I offer a guarantee or warranty</span>
        </label>
        <p className="mt-1 text-xs text-text-low">
          {draft.offerGuarantee
            ? 'You honour it yourself. Buyers see it exactly as written here.'
            : 'Buyers are told that no guarantee or warranty is offered.'}
        </p>
        {draft.offerGuarantee && (
          <div className="mt-3 space-y-3">
            {input(`${prefix}-g-scope`, 'What it covers', draft.guarantee.scope, setG('scope'), {
              max: 1000,
              multiline: true,
              placeholder: 'Stitching and zips that come apart in normal use',
            })}
            {input(`${prefix}-g-duration`, 'How long', draft.guarantee.duration, setG('duration'), {
              max: 120,
              placeholder: '6 months from receipt',
            })}
            {input(`${prefix}-g-exclusions`, 'Not covered (optional)', draft.guarantee.exclusions, setG('exclusions'), {
              max: 1000,
              multiline: true,
              placeholder: 'Wear, washing above 30 °C',
            })}
            {input(`${prefix}-g-contact`, 'How to claim', draft.guarantee.claim_contact, setG('claim_contact'), {
              max: 300,
              placeholder: 'Message me on Kinjy with a photo of the problem',
            })}
          </div>
        )}
      </fieldset>

      <fieldset className="rounded-2xl border border-white/10 p-4">
        <legend className="px-1 text-sm font-semibold text-text-hi">Insurance</legend>
        <label className="flex cursor-pointer items-start gap-2.5 text-sm text-text-hi">
          <input
            type="checkbox"
            checked={draft.offerInsurance}
            onChange={(e) => onChange({ ...draft, offerInsurance: e.target.checked })}
            className="mt-0.5 h-5 w-5 shrink-0 accent-gold"
          />
          <span>Insurance comes with this purchase</span>
        </label>
        <p className="mt-1 text-xs text-text-low">
          {draft.offerInsurance
            ? 'Only if an insurer actually covers it. Kinjy does not insure purchases.'
            : 'Buyers are told that no insurance is included.'}
        </p>
        {draft.offerInsurance && (
          <div className="mt-3 space-y-3">
            {input(`${prefix}-i-insurer`, 'Insurer', draft.insurance.insurer, setI('insurer'), { max: 200 })}
            {input(`${prefix}-i-coverage`, 'What it covers', draft.insurance.coverage, setI('coverage'), {
              max: 1000,
              multiline: true,
            })}
            {input(`${prefix}-i-exclusions`, 'Not covered (optional)', draft.insurance.exclusions, setI('exclusions'), {
              max: 1000,
              multiline: true,
            })}
            {input(`${prefix}-i-claims`, 'How to claim', draft.insurance.claim_process, setI('claim_process'), {
              max: 1000,
              multiline: true,
            })}
            <label className="flex cursor-pointer items-start gap-2.5 text-sm text-text-hi">
              <input
                type="checkbox"
                checked={draft.insurance.premium_included}
                onChange={(e) =>
                  onChange({ ...draft, insurance: { ...draft.insurance, premium_included: e.target.checked } })
                }
                className="mt-0.5 h-5 w-5 shrink-0 accent-gold"
              />
              <span>The premium is included in my price</span>
            </label>
          </div>
        )}
      </fieldset>
    </div>
  )
}
