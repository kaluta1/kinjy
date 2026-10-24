import { useId, useState, type FormEvent } from 'react'
import { Check, Copy, ExternalLink, Link2, Loader2 } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type ShareLinkOut } from '@/lib/api'
import { copyLink } from '@/lib/share'
import { cn } from '@/lib/utils'
import { Badge, Panel, PanelState } from './primitives'

/**
 * Shorten a Kinjy page (kinjy.com/s/<token>), and the member's list of links.
 *
 * The invitation is a switch that starts off: a link is neutral unless the
 * member asks for it to credit them. Whose invitation it carries is decided on
 * the server from the signed-in account; nothing in the request names them.
 * A link never changes once made - a different page or choice is a new link -
 * and disabling one is final, so it asks first.
 */

const BTN_GOLD =
  'inline-flex items-center justify-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40'
const BTN_GHOST =
  'inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-3 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'

function when(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function CopyButton({ url, small = false }: { url: string; small?: boolean }) {
  const [state, setState] = useState<'copied' | 'failed' | null>(null)
  const copy = async () => {
    const result = await copyLink(url)
    setState(result)
    if (result === 'copied') setTimeout(() => setState(null), 2000)
  }
  return (
    <>
      <button type="button" onClick={copy} className={small ? BTN_GHOST : BTN_GOLD}>
        {state === 'copied' ? <Check size={small ? 12 : 14} aria-hidden="true" /> : <Copy size={small ? 12 : 14} aria-hidden="true" />}
        {state === 'copied' ? 'Copied' : 'Copy Link'}
      </button>
      {state === 'failed' && (
        <span role="alert" className="text-xs text-text-low">
          Copying was blocked. Select the link and copy it.
        </span>
      )}
    </>
  )
}

function ShortenForm({ onCreated }: { onCreated: () => void }) {
  const fieldId = useId()
  const toggleId = useId()
  const [destination, setDestination] = useState('')
  const [withInvitation, setWithInvitation] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ShareLinkOut | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setResult(null)
    if (!destination.trim()) {
      setError('Paste the address of a Kinjy page first.')
      return
    }
    setBusy(true)
    try {
      const link = await kaluta.auth.shareLink(destination.trim(), withInvitation)
      setResult(link)
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the link. Check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <div>
        <label htmlFor={fieldId} className="text-sm font-semibold text-text-hi">
          Paste a Kinjy page link
        </label>
        <input
          id={fieldId}
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          value={destination}
          onChange={(e) => {
            setDestination(e.target.value)
            setError(null)
          }}
          placeholder="https://kinjy.com/p/…"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${fieldId}-error` : undefined}
          className="mt-2 w-full min-w-0 rounded-full border border-transparent bg-text-hi/[0.07] px-4 py-2.5 text-sm text-text-hi placeholder:text-text-low focus:border-gold/50 focus:outline-none"
        />
      </div>

      <div className="flex items-start gap-3">
        <button
          id={toggleId}
          type="button"
          role="switch"
          aria-checked={withInvitation}
          onClick={() => {
            setWithInvitation((on) => !on)
            setResult(null)
          }}
          className={cn(
            'relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-gold',
            withInvitation ? 'bg-gold' : 'bg-text-hi/[0.18]',
          )}
        >
          <span
            aria-hidden="true"
            className={cn(
              'inline-block h-5 w-5 rounded-full bg-white shadow transition-transform',
              withInvitation ? 'translate-x-[22px]' : 'translate-x-0.5',
            )}
          />
        </button>
        <div className="min-w-0">
          <label htmlFor={toggleId} className="cursor-pointer text-sm font-semibold text-text-hi">
            Include my personal invitation
          </label>
          <p className="mt-0.5 text-xs leading-relaxed text-text-low">
            When enabled, eligible new members who register through this link will be attributed to you under
            Kinjy’s referral rules.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={busy} className={BTN_GOLD}>
          {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Link2 size={14} aria-hidden="true" />}
          {busy ? 'Creating…' : 'Create Short Link'}
        </button>
      </div>

      {error && (
        <p id={`${fieldId}-error`} role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}

      {result && (
        <div role="status" className="rounded-2xl border border-[var(--cloud-border)] bg-ink-2/40 p-4">
          <p className="text-sm text-text-mid">
            {result.created === false ? 'You already had this link:' : 'Your short link is ready:'}
            {result.include_invitation && <span className="ms-1 text-text-low">(with your invitation)</span>}
          </p>
          <input
            readOnly
            value={result.url}
            onFocus={(e) => e.currentTarget.select()}
            aria-label="Short link"
            className="mono-data mt-2 w-full min-w-0 rounded-full bg-text-hi/[0.07] px-4 py-2.5 text-sm text-text-hi focus:outline-none"
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <CopyButton url={result.url} />
            <a href={result.url} target="_blank" rel="noopener noreferrer" className={BTN_GHOST + ' !py-2.5 !text-sm'}>
              <ExternalLink size={14} aria-hidden="true" />
              Open Link
            </a>
          </div>
        </div>
      )}
    </form>
  )
}

function LinkRow({ link, onChanged }: { link: ShareLinkOut; onChanged: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const disable = async () => {
    setBusy(true)
    setError(null)
    try {
      await kaluta.auth.disableShareLink(link.token)
      setConfirming(false)
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not disable the link')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="rounded-2xl border border-[var(--cloud-border)] bg-ink-2/40 p-3.5">
      {/* On a phone the badges go under the address rather than squeezing it. */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0 flex-1">
          <p className={cn('mono-data break-all text-sm font-semibold', link.active ? 'text-text-hi' : 'text-text-low line-through')}>
            {link.url}
          </p>
          <p className="caption mt-1 break-all" title={link.destination_url}>
            {link.destination}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <span className="caption" title="Opened by people (link previews not counted) · accounts created · communities joined">
            {link.opens ?? 0} open{(link.opens ?? 0) === 1 ? '' : 's'} · {link.registrations ?? 0} sign-up
            {(link.registrations ?? 0) === 1 ? '' : 's'} · {link.community_joins ?? 0} join{(link.community_joins ?? 0) === 1 ? '' : 's'}
          </span>
          <Badge tone={link.active ? 'good' : 'neutral'}>{link.active ? 'Active' : 'Disabled'}</Badge>
          <Badge tone={link.include_invitation ? 'warn' : 'neutral'}>
            {link.include_invitation ? 'With invitation' : 'Neutral'}
          </Badge>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="caption me-auto">Created {when(link.created_at)}</span>
        {link.active && !confirming && (
          <>
            <CopyButton url={link.url} small />
            <a href={link.url} target="_blank" rel="noopener noreferrer" className={BTN_GHOST}>
              <ExternalLink size={12} aria-hidden="true" />
              Open
            </a>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className={BTN_GHOST + ' hover:!border-red-400/40 hover:!text-red-200'}
            >
              Disable
            </button>
          </>
        )}
        {confirming && (
          <span role="group" aria-label="Confirm disabling" className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-text-mid">Disable this link? It will stop opening for everyone.</span>
            <button
              type="button"
              onClick={disable}
              disabled={busy}
              className="rounded-full bg-red-500/80 px-3 py-1.5 text-xs font-bold text-white disabled:opacity-40"
            >
              {busy ? 'Disabling…' : 'Yes, disable'}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy} className={BTN_GHOST}>
              Cancel
            </button>
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="mt-2 text-xs text-red-300">
          {error}
        </p>
      )}
    </li>
  )
}

export default function ShortLinks() {
  const links = useApi(() => kaluta.auth.myShareLinks(), [])
  const items = links.data?.items ?? []

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <Panel title="Shorten a Link" subtitle="A short kinjy.com address for any Kinjy page you want to share.">
        <ShortenForm onCreated={links.reload} />
      </Panel>
      <Panel title="My Short Links" subtitle="A link never changes. To point somewhere else, create a new one.">
        <PanelState
          loading={links.loading && !links.data}
          error={links.error}
          empty={items.length === 0}
          emptyLabel="You have not created a short link yet."
        >
          <ul className="space-y-3">
            {items.map((link) => (
              <LinkRow key={link.token} link={link} onChanged={links.reload} />
            ))}
          </ul>
        </PanelState>
      </Panel>
    </div>
  )
}
