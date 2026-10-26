import { useEffect, useState } from 'react'
import ActionSheet, { fieldClass, primaryBtn } from '@/components/commerce/ActionSheet'
import { ApiError, kaluta } from '@/lib/api'

const CARRIERS = ['DHL', 'FedEx', 'UPS', 'USPS', 'Aramex', 'La Poste', 'Local courier', 'Hand delivery']

interface Props {
  open: boolean
  orderId: string
  productId: string
  onClose: () => void
  onDone: () => void
  /** Known by the caller: a service is "performed", not shipped. */
  service?: boolean
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

export default function DeliverySheet({ open, orderId, productId, onClose, onDone, service: serviceHint = false }: Props) {
  // The order does not say what was sold; the product does. Unknown counts as physical,
  // which only asks for a bit more proof, never less.
  const [physical, setPhysical] = useState(true)
  const [service, setService] = useState(serviceHint)
  const [carrier, setCarrier] = useState('')
  const [tracking, setTracking] = useState('')
  const [url, setUrl] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let live = true
    setError(null)
    kaluta.market
      .product(productId)
      .then((product) => {
        if (!live) return
        setPhysical(product.kind === 'product')
        setService(product.kind === 'service')
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [open, productId])

  const urlBad = url.trim() !== '' && !isHttpUrl(url.trim())
  const hasProof = carrier.trim() !== '' || tracking.trim() !== '' || note.trim() !== ''
  const ready = !busy && !urlBad && (physical ? hasProof : true)

  const send = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      if (physical) {
        await kaluta.market.markDelivered(orderId, {
          carrier: carrier.trim() || undefined,
          tracking_number: tracking.trim() || undefined,
          tracking_url: url.trim() || undefined,
          note: note.trim() || undefined,
        })
      } else {
        await kaluta.market.markDelivered(orderId, note.trim())
      }
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not go through')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ActionSheet
      open={open}
      onClose={onClose}
      title={service ? 'Mark the service as performed' : 'Mark as shipped / ready'}
      description="The buyer is told. Release the goods or final deliverables only once the buyer has confirmed receipt on Kinjy."
    >
      <form onSubmit={send} className="space-y-3">
        {physical && (
          <>
            <div>
              <label htmlFor="dl-carrier" className="caption mb-1.5 block">Carrier</label>
              <input
                id="dl-carrier"
                list="dl-carriers"
                value={carrier}
                onChange={(e) => setCarrier(e.target.value)}
                maxLength={80}
                placeholder="DHL, local courier…"
                className={fieldClass}
              />
              <datalist id="dl-carriers">
                {CARRIERS.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </div>
            <div>
              <label htmlFor="dl-tracking" className="caption mb-1.5 block">Tracking number</label>
              <input
                id="dl-tracking"
                value={tracking}
                onChange={(e) => setTracking(e.target.value)}
                maxLength={120}
                autoCapitalize="characters"
                className={fieldClass}
              />
            </div>
            <div>
              <label htmlFor="dl-url" className="caption mb-1.5 block">Tracking link (optional)</label>
              <input
                id="dl-url"
                type="url"
                inputMode="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://"
                aria-invalid={urlBad}
                className={fieldClass}
              />
              {urlBad && <p className="mt-1 text-xs text-red-200">The link must start with http:// or https://</p>}
            </div>
          </>
        )}
        <div>
          <label htmlFor="dl-note" className="caption mb-1.5 block">
            Note for the buyer{physical ? '' : ' (optional)'}
          </label>
          <textarea
            id="dl-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            maxLength={500}
            placeholder={physical ? 'Handed to the courier on Monday' : 'Your files are ready / the work is done'}
            className={`${fieldClass} resize-y`}
          />
        </div>
        {physical && !hasProof && (
          <p className="caption">Add a carrier, a tracking number or a note so the buyer can follow the parcel.</p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-200">
            {error}
          </p>
        )}
        <button type="submit" disabled={!ready} className={primaryBtn}>
          {busy ? 'Sending…' : 'Send'}
        </button>
      </form>
    </ActionSheet>
  )
}
