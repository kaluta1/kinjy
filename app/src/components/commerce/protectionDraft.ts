import type { ProductGuarantee, ProductInsurance } from '@/lib/api'

/**
 * What a seller declares to buyers: a guarantee or warranty, insurance, and
 * for a service what "completed" means. Shared by the sell form and the
 * listing edit sheet; both send null when a toggle is off.
 */
export interface ProtectionDraft {
  offerGuarantee: boolean
  guarantee: ProductGuarantee
  offerInsurance: boolean
  insurance: ProductInsurance
  serviceTerms: string
}

const EMPTY_GUARANTEE: ProductGuarantee = { scope: '', duration: '', exclusions: '', claim_contact: '' }
const EMPTY_INSURANCE: ProductInsurance = {
  insurer: '',
  coverage: '',
  exclusions: '',
  claim_process: '',
  premium_included: true,
}

export function protectionDraft(from?: {
  guarantee?: ProductGuarantee | null
  insurance?: ProductInsurance | null
  service_terms?: string | null
} | null): ProtectionDraft {
  return {
    offerGuarantee: Boolean(from?.guarantee),
    guarantee: { ...EMPTY_GUARANTEE, ...(from?.guarantee ?? {}) },
    offerInsurance: Boolean(from?.insurance),
    insurance: { ...EMPTY_INSURANCE, ...(from?.insurance ?? {}) },
    serviceTerms: from?.service_terms ?? '',
  }
}

/** The body for the API: null where nothing is offered. */
export function protectionPayload(draft: ProtectionDraft, isService: boolean) {
  const trim = <T extends object>(value: T) =>
    Object.fromEntries(Object.entries(value).map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v])) as T
  return {
    guarantee: draft.offerGuarantee ? trim(draft.guarantee) : null,
    insurance: draft.offerInsurance ? trim(draft.insurance) : null,
    service_terms: isService ? draft.serviceTerms.trim() : null,
  }
}

export type Problem = { field: string; message: string; short: string }

/** The same minimums as the server, so a gap is shown next to its field. */
export function protectionProblems(draft: ProtectionDraft, isService: boolean, prefix: string): Problem[] {
  const problems: Problem[] = []
  const short = (value: string, min: number) => value.trim().length < min
  if (isService && !draft.serviceTerms.trim())
    problems.push({
      field: `${prefix}-service-terms`,
      message: 'What completing this service means and how the buyer can check it',
      short: 'the service terms',
    })
  if (draft.offerGuarantee) {
    const g = draft.guarantee
    if (short(g.scope, 3))
      problems.push({ field: `${prefix}-g-scope`, message: 'What the guarantee covers', short: 'the guarantee cover' })
    if (short(g.duration, 1))
      problems.push({ field: `${prefix}-g-duration`, message: 'How long the guarantee lasts', short: 'the guarantee length' })
    if (short(g.claim_contact, 3))
      problems.push({ field: `${prefix}-g-contact`, message: 'How the buyer makes a guarantee claim', short: 'the claim contact' })
  }
  if (draft.offerInsurance) {
    const i = draft.insurance
    if (short(i.insurer, 2)) problems.push({ field: `${prefix}-i-insurer`, message: 'The insurer', short: 'the insurer' })
    if (short(i.coverage, 3))
      problems.push({ field: `${prefix}-i-coverage`, message: 'What the insurance covers', short: 'the insurance cover' })
    if (short(i.claim_process, 3))
      problems.push({ field: `${prefix}-i-claims`, message: 'How the buyer makes an insurance claim', short: 'the claim process' })
  }
  return problems
}
