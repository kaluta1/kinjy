import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import PublicShell from '@/components/landing/PublicShell'
import { KL_BTN_GOLD } from '@/components/landing/kl-classes'
import { ApiError, kaluta } from '@/lib/api'

/**
 * kinjy.com/s/<token>, when the app itself is asked for it.
 *
 * In production the edge sends /s/* to auth-service, which redirects in one
 * hop (or draws a preview for a link unfurler), so a person normally never
 * sees this page. It is the same answer for the cases the edge does not
 * cover - the development server, a proxy that passed the request through -
 * so a short link opens the same way everywhere.
 */
export function ShortLinkPage() {
  const { token = '' } = useParams()
  const navigate = useNavigate()
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    let cancelled = false
    kaluta.auth
      .resolveShareLink(token)
      .then((link) => {
        if (cancelled) return
        // A neutral link adds nothing. An invitation link adds its token,
        // which ReferralWatch takes off the address once it is held.
        if (!link.invitation) {
          navigate(link.destination, { replace: true })
          return
        }
        const [path, fragment] = link.destination.split('#', 2)
        const joiner = path.includes('?') ? '&' : '?'
        navigate(`${path}${joiner}via=${encodeURIComponent(link.token)}${fragment ? `#${fragment}` : ''}`, {
          replace: true,
        })
      })
      .catch((err: unknown) => {
        if (!cancelled) setMissing(!(err instanceof ApiError) || err.status === 404 || err.status >= 400)
      })
    return () => {
      cancelled = true
    }
  }, [token, navigate])

  if (missing) return <LinkUnavailable />
  return (
    <PublicShell>
      <section className="mx-auto w-full max-w-2xl px-5 pb-16 pt-14">
        <p className="text-sm text-[var(--kl-low)]" role="status">
          Opening the link…
        </p>
      </section>
    </PublicShell>
  )
}

/** A short link that does not exist, or whose page no longer does. */
export default function LinkUnavailable() {
  return (
    <PublicShell>
      <section className="mx-auto w-full max-w-2xl px-5 pb-16 pt-10 sm:pt-14">
        <div className="rounded-[20px] border border-[var(--kl-paper-2)] bg-[var(--kl-surface)] p-8 shadow-[0_24px_48px_-34px_var(--kl-shadow)]">
          <h1 className="text-[clamp(28px,4vw,40px)] font-bold leading-[1.05] tracking-[-0.04em]">
            This link does not work
          </h1>
          <p className="mt-4 text-[1rem] leading-relaxed text-[var(--kl-mid)]">
            It may have been mistyped, or what it pointed to is no longer available. If somebody sent it to you, ask
            them to share it again.
          </p>
          <Link to="/" className={`${KL_BTN_GOLD} mt-6 !py-3 !text-[15px]`}>
            Go to Kinjy
          </Link>
        </div>
      </section>
    </PublicShell>
  )
}
