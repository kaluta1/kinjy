import { useEffect, useMemo, useState } from 'react'
import { useAppTheme } from '@/components/appdemo/theme'
import ActionSheet, { fieldClass, primaryBtn } from '@/components/commerce/ActionSheet'
import ImageUploader from '@/components/commerce/ImageUploader'
import MarginCalculator, { parseCents } from '@/components/commerce/MarginCalculator'
import ProtectionFields from '@/components/commerce/ProtectionFields'
import {
  protectionDraft,
  protectionPayload,
  protectionProblems,
  type ProtectionDraft,
} from '@/components/commerce/protectionDraft'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type MyProduct, type ProductPatch } from '@/lib/api'
import { countryOptions } from '@/lib/profileOptions'
import { cn } from '@/lib/utils'

interface Props {
  product: MyProduct | null
  onClose: () => void
  onSaved: () => void
}

export default function ListingEditSheet({ product, onClose, onSaved }: Props) {
  const { lang } = useAppTheme()
  const countries = useMemo(() => countryOptions(lang), [lang])

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [price, setPrice] = useState('')
  const [stock, setStock] = useState('')
  const [country, setCountry] = useState('')
  const [city, setCity] = useState('')
  const [status, setStatus] = useState<'active' | 'paused'>('active')
  // The photos the listing will have once saved. Starts as the current ones and
  // only changes when the seller adds or removes a photo.
  const [images, setImages] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The listing's declarations live on its detail, not in the seller's list.
  // Until they are loaded they are left out of the save, so nothing is cleared by accident.
  const detail = useApi(
    () => (product && product.kind !== 'digital' ? kaluta.market.product(product.id) : Promise.resolve(null)),
    [product?.id, product?.kind],
  )
  const [protection, setProtection] = useState<ProtectionDraft | null>(null)
  const [attempted, setAttempted] = useState(false)

  useEffect(() => {
    setProtection(detail.data ? protectionDraft(detail.data.protection) : null)
  }, [detail.data])

  useEffect(() => {
    if (!product) return
    setTitle(product.title)
    setDescription(product.description ?? '')
    setPrice(product.vendor_price)
    setStock(product.stock == null ? '' : String(product.stock))
    setCountry(product.country ?? '')
    setCity(product.city ?? '')
    setStatus(product.status === 'paused' ? 'paused' : 'active')
    setImages(product.images ?? [])
    setUploading(false)
    setError(null)
    setAttempted(false)
  }, [product])

  if (!product) return null
  const cents = parseCents(price)
  const showStock = product.kind !== 'digital'
  const isService = product.kind === 'service'
  const gaps = protection ? protectionProblems(protection, isService, 'ed') : []
  const problemFor = (id: string) => (attempted ? gaps.find((p) => p.field === id) : undefined)
  const ready = !busy && !uploading && title.trim().length >= 2 && cents !== null && country !== ''

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!ready || cents === null || !product) return
    setAttempted(true)
    if (gaps.length) {
      document.getElementById(gaps[0].field)?.focus()
      return
    }
    const patch: ProductPatch = {
      title: title.trim(),
      description: description.trim(),
      vendor_price: `${Math.floor(cents / 100)}.${(cents % 100).toString().padStart(2, '0')}`,
      country,
      city: city.trim() || null,
      images,
      status,
    }
    if (showStock) patch.stock = stock === '' ? null : Number(stock)
    if (protection) {
      const declared = protectionPayload(protection, isService)
      patch.guarantee = declared.guarantee
      patch.insurance = declared.insurance
      if (isService) patch.service_terms = declared.service_terms
    }
    setBusy(true)
    setError(null)
    try {
      await kaluta.market.updateProduct(product.id, patch)
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save your changes')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ActionSheet open onClose={onClose} title="Edit listing">
      <form onSubmit={save} className="space-y-3">
        <div>
          <label htmlFor="ed-title" className="caption mb-1.5 block">Title</label>
          <input id="ed-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} className={fieldClass} />
        </div>
        <div>
          <label htmlFor="ed-desc" className="caption mb-1.5 block">Description</label>
          <textarea
            id="ed-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className={cn(fieldClass, 'resize-y')}
          />
        </div>
        <div className={cn('grid gap-3', showStock && 'grid-cols-2')}>
          <div>
            <label htmlFor="ed-price" className="caption mb-1.5 block">Your price (USD)</label>
            <input
              id="ed-price"
              value={price}
              onChange={(e) => setPrice(e.target.value.replace(/[^\d.]/g, ''))}
              inputMode="decimal"
              aria-invalid={price !== '' && cents === null}
              className={fieldClass}
            />
          </div>
          {showStock && (
            <div>
              <label htmlFor="ed-stock" className="caption mb-1.5 block">Stock</label>
              <input
                id="ed-stock"
                value={stock}
                onChange={(e) => setStock(e.target.value.replace(/\D/g, '').slice(0, 7))}
                inputMode="numeric"
                placeholder="Unlimited"
                className={fieldClass}
              />
            </div>
          )}
        </div>
        <MarginCalculator value={price} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="ed-country" className="caption mb-1.5 block">Country</label>
            <select id="ed-country" value={country} onChange={(e) => setCountry(e.target.value)} className={fieldClass}>
              <option value="">Select a country</option>
              {countries.map(({ code, name }) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="ed-city" className="caption mb-1.5 block">City</label>
            <input id="ed-city" value={city} onChange={(e) => setCity(e.target.value)} maxLength={80} className={fieldClass} />
          </div>
        </div>
        <div>
          <span className="caption mb-1.5 block">Photos</span>
          {/* Keyed by listing so opening another one starts from its own photos. */}
          <ImageUploader
            key={product.id}
            initialUrls={product.images}
            onChange={({ urls, busy: running }) => {
              setImages(urls)
              setUploading(running)
            }}
          />
        </div>
        {showStock && (
          <section aria-labelledby="ed-protection" className="space-y-2 pt-1">
            <h3 id="ed-protection" className="text-sm font-semibold text-text-hi">
              What buyers are told before paying
            </h3>
            {detail.loading && <p className="text-xs text-text-low">Loading what this listing declares…</p>}
            {detail.error && (
              <p className="text-xs text-red-200">
                Could not load the guarantee and insurance. Other changes still save; these stay as they were.
              </p>
            )}
            {protection && (
              <ProtectionFields
                draft={protection}
                onChange={setProtection}
                isService={isService}
                prefix="ed"
                fieldClass={fieldClass}
                invalidClass="border-red-300/60"
                problemFor={problemFor}
              />
            )}
          </section>
        )}
        <div>
          <label htmlFor="ed-status" className="caption mb-1.5 block">Visibility</label>
          <select
            id="ed-status"
            value={status}
            onChange={(e) => setStatus(e.target.value as 'active' | 'paused')}
            className={fieldClass}
          >
            <option value="active">Active — buyers can order</option>
            <option value="paused">Paused — hidden from buyers</option>
          </select>
        </div>
        {error && (
          <p role="alert" className="text-sm text-red-200">
            {error}
          </p>
        )}
        <button type="submit" disabled={!ready} className={primaryBtn}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
      </form>
    </ActionSheet>
  )
}
