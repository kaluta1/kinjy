import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router'
import { Minus, Plus, ShieldCheck, X } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import PaymentPanel from '@/components/commerce/PaymentPanel'
import { ApiError, kaluta, type OrderShipping } from '@/lib/api'
import { countryOptions } from '@/lib/profileOptions'
import { cn } from '@/lib/utils'

export interface CheckoutProduct {
  id: string
  title: string
  kind: string
  customer_price: string
  /** Null or undefined means unlimited (or unknown on a list card: the server enforces it). */
  stock?: number | null
  /** Present on an ebook: one copy, delivered to the library, no address. */
  ebook?: unknown
}

const field =
  'w-full min-h-[44px] rounded-2xl border border-white/10 bg-ink-2/60 px-4 py-2.5 text-base text-text-hi placeholder:text-text-low focus:border-gold/40 focus:outline-none sm:text-sm aria-[invalid=true]:border-red-300/60'

const MAX_QTY = 99

function toCents(price: string): number {
  const [whole, frac = ''] = price.split('.')
  return Number(whole) * 100 + Number((frac + '00').slice(0, 2))
}

function money(cents: number): string {
  return `$${Math.floor(cents / 100)}.${(cents % 100).toString().padStart(2, '0')}`
}

const EMPTY: OrderShipping = {
  full_name: '',
  line1: '',
  line2: '',
  city: '',
  region: '',
  postal_code: '',
  country: '',
  phone: '',
}

export default function CheckoutSheet({
  product,
  onClose,
  onOrdered,
}: {
  product: CheckoutProduct
  onClose: () => void
  onOrdered?: () => void
}) {
  const { lang } = useAppTheme()
  const countries = useMemo(() => countryOptions(lang), [lang])
  const physical = product.kind === 'product'
  const isEbook = Boolean(product.ebook)
  const max = isEbook ? 1 : Math.max(1, Math.min(product.stock ?? MAX_QTY, MAX_QTY))

  const [quantity, setQuantity] = useState(1)
  const [address, setAddress] = useState<OrderShipping>(EMPTY)
  const [touched, setTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [orderId, setOrderId] = useState<string | null>(null)
  // The price the server put on the order: the payment must be exactly this.
  const [orderPrice, setOrderPrice] = useState('')

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])

  const set = (key: keyof OrderShipping) => (value: string) =>
    setAddress((current) => ({ ...current, [key]: value }))

  const missing = {
    full_name: address.full_name.trim() === '',
    line1: address.line1.trim() === '',
    city: address.city.trim() === '',
    country: address.country === '',
  }
  const addressOk = !physical || !Object.values(missing).some(Boolean)
  const total = toCents(product.customer_price) * quantity

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (!addressOk || submitting) return
    setError(null)
    setSubmitting(true)
    try {
      const clean = (value?: string | null) => value?.trim() || undefined
      const shipping: OrderShipping | undefined = physical
        ? {
            full_name: address.full_name.trim(),
            line1: address.line1.trim(),
            line2: clean(address.line2),
            city: address.city.trim(),
            region: clean(address.region),
            postal_code: clean(address.postal_code),
            country: address.country,
            phone: clean(address.phone),
          }
        : undefined
      const order = await kaluta.market.order(product.id, quantity, shipping)
      setOrderPrice(order.customer_price)
      setOrderId(order.id)
      onOrdered?.()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not place the order')
    } finally {
      setSubmitting(false)
    }
  }

  const text = (id: keyof OrderShipping, label: string, opts: { required?: boolean; autoComplete?: string; inputMode?: 'tel' } = {}) => {
    const invalid = touched && opts.required && address[id] === ''
    return (
      <div>
        <label htmlFor={`co-${id}`} className="caption mb-1.5 block">
          {label}
          {opts.required ? '' : ' (optional)'}
        </label>
        <input
          id={`co-${id}`}
          value={address[id] ?? ''}
          onChange={(e) => set(id)(e.target.value)}
          autoComplete={opts.autoComplete}
          inputMode={opts.inputMode}
          aria-invalid={invalid || undefined}
          aria-required={opts.required || undefined}
          className={field}
        />
        {invalid && <p className="mt-1 text-xs text-red-200">Required</p>}
      </div>
    )
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/70 md:items-center md:p-6"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={orderId ? 'Order placed' : `Order ${product.title}`}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92svh] w-full flex-col rounded-t-3xl border border-white/10 bg-ink md:max-w-lg md:rounded-3xl"
      >
        <div className="flex items-center justify-between gap-3 px-5 pb-2 pt-4">
          <h2 className="truncate text-base font-semibold text-text-hi">
            {orderId ? 'Order placed' : physical ? 'Delivery and payment' : 'Confirm your order'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-me-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-text-mid hover:text-gold-soft"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {orderId ? (
          <div className="px-5 pb-6 pt-2">
            <p className="text-center text-sm text-text-hi">Your order is placed.</p>
            <p className="mono-data mb-4 mt-1 text-center text-xs text-text-mid">Order {orderId.slice(0, 12)}</p>
            {/* An order is only a request until it is paid: pay now, or later from the orders list. */}
            <PaymentPanel orderId={orderId} amount={orderPrice || money(total).slice(1)} onPaid={() => onOrdered?.()} />
            {isEbook && (
              <p className="mt-4 text-center text-sm text-text-mid">
                Once the payment is confirmed, the ebook is in{' '}
                <Link to="/market/library" onClick={onClose} className="font-semibold text-gold-soft underline">
                  your library
                </Link>
                , ready to download.
              </p>
            )}
            <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:justify-center">
              <Link
                to="/market#orders"
                onClick={onClose}
                className="inline-flex min-h-[44px] items-center justify-center rounded-full border border-white/12 px-6 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
              >
                Pay later — see my orders
              </Link>
              <button
                type="button"
                onClick={onClose}
                className="min-h-[44px] rounded-full border border-white/12 px-6 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
              >
                Keep browsing
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} noValidate className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 pb-4">
              <div className="flex items-center justify-between gap-3">
                <p className="min-w-0 truncate text-sm text-text-mid">{product.title}</p>
                <div className="flex shrink-0 items-center gap-1" role="group" aria-label="Quantity">
                  <button
                    type="button"
                    onClick={() => setQuantity((q) => Math.max(1, q - 1))}
                    disabled={quantity <= 1}
                    aria-label="Decrease quantity"
                    className="flex h-11 w-11 items-center justify-center rounded-full border border-white/12 text-text-hi disabled:opacity-30"
                  >
                    <Minus size={16} aria-hidden="true" />
                  </button>
                  <span className="mono-data w-8 text-center text-base text-text-hi" aria-live="polite">
                    {quantity}
                  </span>
                  <button
                    type="button"
                    onClick={() => setQuantity((q) => Math.min(max, q + 1))}
                    disabled={quantity >= max}
                    aria-label="Increase quantity"
                    className="flex h-11 w-11 items-center justify-center rounded-full border border-white/12 text-text-hi disabled:opacity-30"
                  >
                    <Plus size={16} aria-hidden="true" />
                  </button>
                </div>
              </div>

              {physical && (
                <fieldset className="space-y-3">
                  <legend className="caption mb-1">Delivery address</legend>
                  {text('full_name', 'Full name', { required: true, autoComplete: 'name' })}
                  {text('line1', 'Address line 1', { required: true, autoComplete: 'address-line1' })}
                  {text('line2', 'Address line 2', { autoComplete: 'address-line2' })}
                  <div className="grid grid-cols-2 gap-3">
                    {text('city', 'City', { required: true, autoComplete: 'address-level2' })}
                    {text('region', 'Region', { autoComplete: 'address-level1' })}
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    {text('postal_code', 'Postal code', { autoComplete: 'postal-code' })}
                    {text('phone', 'Phone', { autoComplete: 'tel', inputMode: 'tel' })}
                  </div>
                  <div>
                    <label htmlFor="co-country" className="caption mb-1.5 block">Country</label>
                    <select
                      id="co-country"
                      value={address.country}
                      onChange={(e) => set('country')(e.target.value)}
                      autoComplete="country"
                      aria-invalid={(touched && missing.country) || undefined}
                      aria-required="true"
                      className={field}
                    >
                      <option value="">Select a country</option>
                      {countries.map(({ code, name }) => (
                        <option key={code} value={code}>
                          {name}
                        </option>
                      ))}
                    </select>
                    {touched && missing.country && <p className="mt-1 text-xs text-red-200">Required</p>}
                  </div>
                </fieldset>
              )}

              <dl className="mono-data space-y-1.5 rounded-2xl border border-white/10 bg-ink-2/60 p-3 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-text-mid">
                    ${product.customer_price} x {quantity}
                  </dt>
                  <dd className="text-text-hi">{money(total)}</dd>
                </div>
                <div className="flex justify-between gap-3 border-t border-white/10 pt-1.5">
                  <dt className="text-text-hi">Total</dt>
                  <dd className="text-gold-soft">{money(total)}</dd>
                </div>
              </dl>
              <p className="flex items-start gap-2 text-xs text-text-low">
                <ShieldCheck size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                Held in escrow by a licensed custodian until you confirm receipt.
              </p>
            </div>

            <div className="space-y-2 border-t border-white/10 px-5 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3">
              {error && (
                <p role="alert" className="text-sm text-red-200">
                  {error}
                </p>
              )}
              <button
                type="submit"
                disabled={submitting}
                className={cn(
                  'min-h-[48px] w-full rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink',
                  submitting && 'opacity-60',
                )}
              >
                {submitting ? 'Placing order...' : `Order - ${money(total)} held in escrow`}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>,
    document.body,
  )
}
