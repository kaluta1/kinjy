import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { ArrowLeft, BookOpen, CheckCircle2, Package, Wrench } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useAppTheme } from '@/components/appdemo/theme'
import EbookFileField, { type EbookFileValue } from '@/components/commerce/EbookFileField'
import ImageUploader from '@/components/commerce/ImageUploader'
import MarginCalculator, { parseCents } from '@/components/commerce/MarginCalculator'
import ProtectionFields from '@/components/commerce/ProtectionFields'
import { protectionDraft, protectionPayload, protectionProblems } from '@/components/commerce/protectionDraft'
import RichTextEditor from '@/components/social/RichTextEditor'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type EbookInput } from '@/lib/api'
import { EBOOK_CATEGORY_LABELS, EBOOK_LANGUAGES } from '@/lib/ebooks'
import { countryOptions } from '@/lib/profileOptions'
import { htmlToText } from '@/lib/richtext'
import { cn } from '@/lib/utils'

type Kind = 'product' | 'service' | 'digital'

const KINDS: { id: Kind; label: string; hint: string; icon: typeof Package }[] = [
  { id: 'product', label: 'Product', hint: 'Something you ship or hand over', icon: Package },
  { id: 'service', label: 'Service', hint: 'Work you do for the buyer', icon: Wrench },
  { id: 'digital', label: 'Ebook', hint: 'A PDF or EPUB, downloaded after payment', icon: BookOpen },
]

const PLACEHOLDERS: Record<Kind, { title: string; description: string }> = {
  product: { title: 'Hand-dyed kanga cloth', description: 'What it is, condition, what the buyer gets' },
  service: { title: 'Logo design in three days', description: 'What you do, how long it takes, what the buyer receives' },
  digital: { title: 'The Small Business Bookkeeping Guide', description: 'What the book covers, who it is for, what the reader will learn' },
}

const field =
  'w-full min-h-[44px] rounded-2xl border border-white/10 bg-ink-2/60 px-4 py-2.5 text-base text-text-hi placeholder:text-text-low focus:border-gold/40 focus:outline-none sm:text-sm'
const invalidField = 'border-red-300/60'

type Problem = { field: string; message: string; short: string }

export default function MarketSell() {
  const { lang } = useAppTheme()
  const countries = useMemo(() => countryOptions(lang), [lang])
  const terms = useApi(() => kaluta.market.ebookTerms(), [])

  const [kind, setKind] = useState<Kind>('product')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [price, setPrice] = useState('')
  const [stock, setStock] = useState('')
  const [country, setCountry] = useState('')
  const [city, setCity] = useState('')
  const [photos, setPhotos] = useState<{ urls: string[]; busy: boolean }>({ urls: [], busy: false })
  // The ebook: its file, its sample and the book's details.
  const [book, setBook] = useState<EbookFileValue | null>(null)
  const [sample, setSample] = useState<EbookFileValue | null>(null)
  const [fileBusy, setFileBusy] = useState(false)
  const [sampleBusy, setSampleBusy] = useState(false)
  const [details, setDetails] = useState<EbookInput>({ license: 'personal' })
  // What the buyer is told before paying: guarantee, insurance, service terms.
  const [protection, setProtection] = useState(protectionDraft)
  const [submitting, setSubmitting] = useState<null | 'active' | 'draft'>(null)
  const [attempted, setAttempted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<{ id: string; status: string; pricing: Record<string, string> } | null>(null)
  const summaryRef = useRef<HTMLDivElement>(null)

  const ebook = kind === 'digital'
  const cents = parseCents(price)
  const stockNumber = stock === '' ? null : Number(stock)
  const busy = photos.busy || fileBusy || sampleBusy
  const maxMb = terms.data?.max_file_mb ?? 100
  const maxSampleMb = terms.data?.max_sample_mb ?? 20
  const set = (key: keyof EbookInput) => (value: string) =>
    setDetails((current) => ({ ...current, [key]: value === '' ? null : value }))

  // What still stops publication, in the order of the form. Shown next to
  // each field and in one list by the button - never a silently grey button.
  const problems: Problem[] = []
  if (title.trim().length < 2)
    problems.push({ field: 'sell-title', message: 'A title (at least 2 characters)', short: 'a title' })
  if (cents === null)
    problems.push({ field: 'sell-price', message: 'Your price, a positive amount like 25 or 25.50', short: 'your price' })
  if (kind === 'product' && stockNumber === null)
    problems.push({ field: 'sell-stock', message: 'How many you have in stock', short: 'the stock' })
  if (!ebook && country === '')
    problems.push({ field: 'sell-country', message: 'The country you sell from', short: 'your country' })
  if (ebook && !book) problems.push({ field: 'sell-file', message: 'The ebook file (PDF or EPUB)', short: 'the ebook file' })
  if (ebook && !(details.author ?? '').trim())
    problems.push({ field: 'sell-author', message: 'The author or publisher', short: 'the author' })
  if (!ebook) problems.push(...protectionProblems(protection, kind === 'service', 'sell'))
  const draftProblems = problems.filter((p) => p.field === 'sell-title' || p.field === 'sell-price')
  const problemFor = (id: string) => (attempted ? problems.find((p) => p.field === id) : undefined)

  const submit = async (status: 'active' | 'draft') => {
    if (submitting || busy) return
    setAttempted(true)
    setError(null)
    const blocking = status === 'draft' ? draftProblems : problems
    if (blocking.length) {
      // Bring the list into view and put the cursor on the first gap.
      summaryRef.current?.focus()
      document.getElementById(blocking[0].field)?.focus({ preventScroll: true })
      return
    }
    if (cents === null) return
    setSubmitting(status)
    try {
      const result = await kaluta.market.createProduct({
        title: title.trim(),
        description: (ebook ? description : description.trim()) || undefined,
        kind,
        vendor_price: `${Math.floor(cents / 100)}.${(cents % 100).toString().padStart(2, '0')}`,
        stock: kind === 'digital' ? null : stockNumber,
        country: country || undefined,
        city: ebook ? undefined : city.trim() || undefined,
        images: photos.urls,
        ...(ebook ? {} : protectionPayload(protection, kind === 'service')),
        ...(ebook
          ? {
              status,
              ebook: {
                ...details,
                author: (details.author ?? '').trim() || null,
                page_count: details.page_count ? Number(details.page_count) : null,
                download_limit: details.download_limit ? Number(details.download_limit) : null,
                access_days: details.access_days ? Number(details.access_days) : null,
                file_asset_id: book?.id ?? null,
                sample_asset_id: sample?.id ?? null,
              },
            }
          : {}),
      })
      setCreated(result)
    } catch (err) {
      // Everything typed stays in the form; only the message changes.
      setError(err instanceof ApiError ? err.message : 'Could not save the listing. Try again.')
    } finally {
      setSubmitting(null)
    }
  }

  if (created) {
    const draft = created.status === 'draft'
    return (
      <AppShell title={draft ? 'Draft saved' : 'Listing published'}>
        <div className="cloud-card mx-auto max-w-xl p-5 text-center">
          <CheckCircle2 size={36} className="mx-auto text-gold-soft" aria-hidden="true" />
          <h2 className="mt-3 text-base font-semibold text-text-hi">
            {draft ? `${title.trim()} is saved as a draft` : `${title.trim()} is live`}
          </h2>
          {draft && (
            <p className="mt-2 text-sm text-text-mid">Only you can see it. Publish it from the listing once it is ready.</p>
          )}
          <dl className="mono-data mt-4 space-y-1.5 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-text-mid">You asked</dt>
              <dd className="text-text-hi">${created.pricing.vendor_price}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-text-mid">Buyer pays</dt>
              <dd className="text-gold-soft">${created.pricing.customer_price}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-text-mid">Kinjy margin</dt>
              <dd className="text-text-hi">${created.pricing.margin}</dd>
            </div>
          </dl>
          <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
            <Link
              to={`/market/products/${created.id}`}
              className="inline-flex min-h-[44px] items-center justify-center rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink"
            >
              {draft ? 'Open the draft' : 'View my listing'}
            </Link>
            <Link
              to="/market"
              className="inline-flex min-h-[44px] items-center justify-center rounded-full border border-white/12 px-6 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
            >
              Back to the marketplace
            </Link>
          </div>
        </div>
      </AppShell>
    )
  }

  const hint = (id: string) => {
    const problem = problemFor(id)
    return problem ? (
      <p id={`${id}-error`} className="mt-1.5 text-xs text-red-200">
        Needed: {problem.message.toLowerCase()}
      </p>
    ) : null
  }
  const aria = (id: string) =>
    problemFor(id) ? { 'aria-invalid': true as const, 'aria-describedby': `${id}-error` } : {}

  return (
    <AppShell
      title="Sell on Kinjy"
      subtitle="You set your price and receive exactly that. Kinjy adds a 20% margin on top for the buyer."
      action={
        <Link
          to="/market"
          className="inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          Marketplace
        </Link>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit('active')
        }}
        noValidate
        className="mx-auto max-w-xl space-y-5"
      >
        <fieldset>
          <legend className="caption mb-2">What are you selling?</legend>
          <div className="grid grid-cols-3 gap-2">
            {KINDS.map(({ id, label, hint: kindHint, icon: Icon }) => (
              <label
                key={id}
                className={cn(
                  'cloud-card flex min-h-[88px] cursor-pointer flex-col items-center justify-center gap-1 p-2 text-center has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-gold/70',
                  kind === id && 'border-gold/60 bg-gold/[0.08]',
                )}
              >
                <input
                  type="radio"
                  name="kind"
                  value={id}
                  checked={kind === id}
                  onChange={() => setKind(id)}
                  className="sr-only"
                />
                <Icon size={20} className={kind === id ? 'text-gold-soft' : 'text-text-mid'} aria-hidden="true" />
                <span className="text-sm font-semibold text-text-hi">
                  {label}
                  {kind === id && <CheckCircle2 size={12} className="ms-1 inline text-gold-soft" aria-hidden="true" />}
                </span>
                <span className="hidden text-[11px] leading-tight text-text-low sm:block">{kindHint}</span>
              </label>
            ))}
          </div>
          {ebook && (
            <p className="mt-2 text-xs text-text-mid">
              A digital product: nothing is shipped. The buyer downloads the file from their library once the
              payment is confirmed.
            </p>
          )}
        </fieldset>

        <div>
          <label htmlFor="sell-title" className="caption mb-1.5 block">Title</label>
          <input
            id="sell-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
            placeholder={PLACEHOLDERS[kind].title}
            className={cn(field, problemFor('sell-title') && invalidField)}
            {...aria('sell-title')}
          />
          {hint('sell-title')}
        </div>

        {ebook && (
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="sell-subtitle" className="caption mb-1.5 block">Subtitle (optional)</label>
              <input id="sell-subtitle" value={details.subtitle ?? ''} onChange={(e) => set('subtitle')(e.target.value)}
                maxLength={200} className={field} />
            </div>
            <div>
              <label htmlFor="sell-author" className="caption mb-1.5 block">Author or publisher</label>
              <input id="sell-author" value={details.author ?? ''} onChange={(e) => set('author')(e.target.value)}
                maxLength={200} className={cn(field, problemFor('sell-author') && invalidField)} {...aria('sell-author')} />
              {hint('sell-author')}
            </div>
          </div>
        )}

        {ebook && (
          <div>
            <label htmlFor="sell-summary" className="caption mb-1.5 block">Short summary (optional)</label>
            <input id="sell-summary" value={details.summary ?? ''} onChange={(e) => set('summary')(e.target.value)}
              maxLength={500} placeholder="One or two sentences shown at the top of the page" className={field} />
          </div>
        )}

        <div>
          <label htmlFor="sell-desc" className="caption mb-1.5 block">Description</label>
          {ebook ? (
            <>
              <RichTextEditor value={description} onChange={setDescription} placeholder={PLACEHOLDERS.digital.description} minHeight={160} />
              <p className="mt-1 text-xs text-text-low">{htmlToText(description).length} characters</p>
            </>
          ) : (
            <textarea
              id="sell-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
              placeholder={PLACEHOLDERS[kind].description}
              className={cn(field, 'resize-y')}
            />
          )}
        </div>

        {ebook && (
          <>
            <EbookFileField
              id="sell-file"
              purpose="ebook"
              label="The ebook file"
              hint={`PDF or EPUB, up to ${maxMb} MB. Kept private: buyers download it only after paying.`}
              maxMb={maxMb}
              value={book}
              onChange={setBook}
              onBusy={setFileBusy}
              invalid={Boolean(problemFor('sell-file'))}
              describedBy={problemFor('sell-file') ? 'sell-file-error' : undefined}
            />
            {hint('sell-file')}
            <EbookFileField
              id="sell-sample"
              purpose="ebook_sample"
              label="Free sample (optional)"
              hint={`A few pages anyone can read before buying. PDF or EPUB, up to ${maxSampleMb} MB. Public.`}
              maxMb={maxSampleMb}
              value={sample}
              onChange={setSample}
              onBusy={setSampleBusy}
            />
          </>
        )}

        <div className={cn('grid gap-3', kind !== 'digital' && 'grid-cols-2')}>
          <div>
            <label htmlFor="sell-price" className="caption mb-1.5 block">Your price (USD)</label>
            <input
              id="sell-price"
              value={price}
              onChange={(e) => setPrice(e.target.value.replace(/[^\d.]/g, ''))}
              inputMode="decimal"
              placeholder="100.00"
              className={cn(field, (problemFor('sell-price') || (price !== '' && cents === null)) && invalidField)}
              {...aria('sell-price')}
            />
            {hint('sell-price')}
          </div>
          {kind !== 'digital' && (
            <div>
              <label htmlFor="sell-stock" className="caption mb-1.5 block">
                Stock{kind === 'service' ? ' (optional)' : ''}
              </label>
              <input
                id="sell-stock"
                value={stock}
                onChange={(e) => setStock(e.target.value.replace(/\D/g, '').slice(0, 7))}
                inputMode="numeric"
                placeholder={kind === 'service' ? 'Unlimited' : '0'}
                className={cn(field, problemFor('sell-stock') && invalidField)}
                {...aria('sell-stock')}
              />
              {hint('sell-stock')}
            </div>
          )}
        </div>
        {price !== '' && cents === null && !problemFor('sell-price') && (
          <p className="-mt-3 text-xs text-red-200">Use a positive amount with at most two decimals, like 25 or 25.50.</p>
        )}

        {ebook && (
          <details className="cloud-card p-4" open>
            <summary className="cursor-pointer text-sm font-semibold text-text-hi">Book details</summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="sell-category" className="caption mb-1.5 block">Category</label>
                <select id="sell-category" value={details.category ?? ''} onChange={(e) => set('category')(e.target.value)} className={field}>
                  <option value="">Choose a category</option>
                  {(terms.data?.categories ?? Object.keys(EBOOK_CATEGORY_LABELS)).map((c) => (
                    <option key={c} value={c}>{EBOOK_CATEGORY_LABELS[c] ?? c}</option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="sell-language" className="caption mb-1.5 block">Language</label>
                <select id="sell-language" value={details.language ?? ''} onChange={(e) => set('language')(e.target.value)} className={field}>
                  <option value="">Choose a language</option>
                  {EBOOK_LANGUAGES.map(([code, name]) => (
                    <option key={code} value={code}>{name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="sell-published" className="caption mb-1.5 block">Publication date (optional)</label>
                <input id="sell-published" type="date" value={details.published_on ?? ''} onChange={(e) => set('published_on')(e.target.value)} className={field} />
              </div>
              <div>
                <label htmlFor="sell-edition" className="caption mb-1.5 block">Edition (optional)</label>
                <input id="sell-edition" value={details.edition ?? ''} onChange={(e) => set('edition')(e.target.value)} maxLength={60} placeholder="2nd edition" className={field} />
              </div>
              <div>
                <label htmlFor="sell-pages" className="caption mb-1.5 block">Pages (optional)</label>
                <input id="sell-pages" inputMode="numeric" value={details.page_count ?? ''} onChange={(e) => set('page_count')(e.target.value.replace(/\D/g, '').slice(0, 6))} className={field} />
              </div>
              <div>
                <label htmlFor="sell-isbn" className="caption mb-1.5 block">ISBN (optional)</label>
                <input id="sell-isbn" value={details.isbn ?? ''} onChange={(e) => set('isbn')(e.target.value.slice(0, 17))} placeholder="978-…" className={field} />
              </div>
            </div>
            <fieldset className="mt-4">
              <legend className="caption mb-1.5">What the buyer may do with it</legend>
              <div className="flex flex-col gap-2 text-sm text-text-mid">
                <label className="flex items-start gap-2">
                  <input type="radio" name="license" checked={(details.license ?? 'personal') === 'personal'} onChange={() => setDetails((d) => ({ ...d, license: 'personal' }))} className="mt-1 accent-gold" />
                  <span>Personal use - read it, not share or resell it</span>
                </label>
                <label className="flex items-start gap-2">
                  <input type="radio" name="license" checked={details.license === 'commercial'} onChange={() => setDetails((d) => ({ ...d, license: 'commercial' }))} className="mt-1 accent-gold" />
                  <span>Commercial use allowed - the buyer may use it in their work</span>
                </label>
              </div>
            </fieldset>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="sell-limit" className="caption mb-1.5 block">Download limit (optional)</label>
                <input id="sell-limit" inputMode="numeric" value={details.download_limit ?? ''} onChange={(e) => set('download_limit')(e.target.value.replace(/\D/g, '').slice(0, 4))} placeholder="No limit" className={field} />
              </div>
              <div>
                <label htmlFor="sell-days" className="caption mb-1.5 block">Access period in days (optional)</label>
                <input id="sell-days" inputMode="numeric" value={details.access_days ?? ''} onChange={(e) => set('access_days')(e.target.value.replace(/\D/g, '').slice(0, 4))} placeholder="For good" className={field} />
              </div>
            </div>
          </details>
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="sell-country" className="caption mb-1.5 block">
              {ebook ? 'Your country (optional)' : 'Country'}
            </label>
            <select
              id="sell-country"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              className={cn(field, problemFor('sell-country') && invalidField)}
              autoComplete="country"
              {...aria('sell-country')}
            >
              <option value="">{ebook ? 'Not shown' : 'Select a country'}</option>
              {countries.map(({ code, name }) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
            {ebook ? (
              <p className="mt-1 text-xs text-text-low">Shown on the listing so buyers know where the seller is. Nothing is shipped.</p>
            ) : (
              hint('sell-country')
            )}
          </div>
          {!ebook && (
            <div>
              <label htmlFor="sell-city" className="caption mb-1.5 block">Pickup or shipping city</label>
              <input
                id="sell-city"
                value={city}
                onChange={(e) => setCity(e.target.value)}
                maxLength={80}
                placeholder="Optional"
                className={field}
                autoComplete="address-level2"
              />
            </div>
          )}
        </div>

        <div>
          <p className="caption mb-1.5">{ebook ? 'Cover image' : 'Photos'}</p>
          {ebook && (
            <p className="mb-2 text-xs text-text-low">
              The book's cover, shown on the listing and in the buyer's library. Portrait, about 2:3, works best.
              This is not the downloadable file.
            </p>
          )}
          <ImageUploader onChange={setPhotos} />
          {ebook && photos.urls[0] && (
            <div className="mt-3 w-32 overflow-hidden rounded-xl border border-white/10 bg-black/20">
              <img src={photos.urls[0]} alt="Cover preview" className="aspect-[2/3] w-full object-contain" />
            </div>
          )}
        </div>

        {!ebook && (
          <section aria-labelledby="sell-protection" className="space-y-3">
            <h2 id="sell-protection" className="text-base font-semibold text-text-hi">
              What buyers are told before paying
            </h2>
            <p className="text-xs text-text-low">
              Shown on the listing and at checkout. The purchase becomes final when the buyer confirms receipt in
              their own account; Kinjy does not arbitrate disputes, so state clearly what you offer.
            </p>
            <ProtectionFields
              draft={protection}
              onChange={setProtection}
              isService={kind === 'service'}
              prefix="sell"
              fieldClass={field}
              invalidClass={invalidField}
              problemFor={problemFor}
            />
          </section>
        )}

        {/* Sticks above the bottom tab bar on a phone so the price stays in view while typing. */}
        <div className="sticky bottom-20 z-10 space-y-2 lg:bottom-4">
          <MarginCalculator value={price} className="bg-ink/95 backdrop-blur" />
          {attempted && problems.length > 0 && (
            <div
              ref={summaryRef}
              tabIndex={-1}
              role="alert"
              className="rounded-2xl bg-ink/95 px-4 py-2.5 text-sm text-red-200 focus:outline-none"
            >
              {/* One line, so the form stays visible above it; each item jumps to its field,
                  where the full explanation sits. */}
              <span className="font-semibold">Before publishing, add </span>
              {problems.map((problem, i) => (
                <span key={problem.field}>
                  {i > 0 && (i === problems.length - 1 ? ' and ' : ', ')}
                  <a
                    href={`#${problem.field}`}
                    onClick={(e) => {
                      e.preventDefault()
                      document.getElementById(problem.field)?.focus()
                    }}
                    className="underline underline-offset-2"
                  >
                    {problem.short}
                  </a>
                </span>
              ))}
              .
            </div>
          )}
          {error && (
            <p role="alert" className="rounded-2xl bg-ink/95 px-3 py-2 text-sm text-red-200">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            {ebook && (
              <button
                type="button"
                onClick={() => void submit('draft')}
                disabled={Boolean(submitting) || busy}
                className="min-h-[48px] rounded-full border border-white/15 bg-ink/95 px-5 text-sm font-semibold text-text-hi disabled:opacity-40"
              >
                {submitting === 'draft' ? 'Saving…' : 'Save draft'}
              </button>
            )}
            <button
              type="submit"
              disabled={Boolean(submitting) || busy}
              className="min-h-[48px] flex-1 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink disabled:opacity-40"
            >
              {submitting === 'active'
                ? 'Publishing…'
                : fileBusy || sampleBusy
                  ? 'Uploading the file…'
                  : photos.busy
                    ? 'Uploading photos…'
                    : 'Publish listing'}
            </button>
          </div>
        </div>
      </form>
    </AppShell>
  )
}
