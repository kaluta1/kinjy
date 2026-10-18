import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Loader2, MailCheck, ShieldAlert } from 'lucide-react'
import { ApiError, kaluta, type PayoutWallet } from '@/lib/api'
import PublicShell from '@/components/landing/PublicShell'
import { Stage } from '@/components/landing/PageKit'

/**
 * The link in the payout wallet e-mail.
 *
 * Works signed out - the member may open the mail on another device, and
 * having the mail is the proof being asked for. The token is used once, as
 * soon as the page opens; confirming it here does not make the wallet usable
 * yet: an admin approves it next.
 */
export default function PayoutWalletConfirm() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [wallet, setWallet] = useState<PayoutWallet | null>(null)
  const [error, setError] = useState<string | null>(token ? null : 'This link is incomplete. Open it again from the e-mail.')
  const sent = useRef(false)

  useEffect(() => {
    // Once: React's development double-run must not spend the token twice.
    if (!token || sent.current) return
    sent.current = true
    kaluta.account
      .confirmPayoutWallet(token)
      .then((result) => setWallet(result.wallet))
      .catch((err) => setError(err instanceof ApiError ? err.message : 'The wallet could not be confirmed. Try again.'))
  }, [token])

  const title = 'mt-4 text-[clamp(26px,3.4vw,34px)] font-bold leading-[1.05] tracking-[-0.04em]'
  const text = 'mt-3 text-[0.95rem] leading-relaxed text-[var(--kl-mid)]'
  const tile = 'grid h-12 w-12 place-items-center rounded-[14px] bg-[#F6EBD3] text-[#8A6414]'
  const link = 'mt-6 inline-block font-semibold text-[var(--kl-gold-deep)] hover:underline'

  return (
    <PublicShell>
      <section className="kl-pad-x py-[clamp(40px,7vw,96px)]">
        <Stage className="mx-auto w-full max-w-[520px] p-[clamp(14px,3vw,32px)]" glows={['var(--kl-sky)', '#D9A648']}>
          <div className="rounded-[20px] bg-[var(--kl-surface)] p-7 shadow-[0_30px_60px_-34px_var(--kl-shadow)] md:p-8">
            {wallet ? (
              <>
                <span className={tile}>
                  <MailCheck size={22} aria-hidden="true" />
                </span>
                <h1 className={title}>Wallet confirmed</h1>
                <p className={text}>
                  Thank you. An admin checks it next, and you are notified as soon as withdrawals are open to it.
                </p>
                <code className="mt-4 block break-all rounded-xl bg-black/[0.05] px-3 py-2 font-mono text-xs">
                  {wallet.address}
                </code>
                <p className="mt-2 text-xs text-[var(--kl-mid)]">
                  {wallet.asset} on {wallet.network}
                </p>
                <Link to="/dashboard?tab=earnings" className={link}>
                  Go to my earnings
                </Link>
              </>
            ) : error ? (
              <>
                <span className={tile}>
                  <ShieldAlert size={22} aria-hidden="true" />
                </span>
                <h1 className={title}>This link did not work</h1>
                <p className={text} role="alert">
                  {error}
                </p>
                <Link to="/dashboard?tab=earnings" className={link}>
                  Go to my earnings
                </Link>
              </>
            ) : (
              <p className="flex items-center gap-2 text-[var(--kl-mid)]" role="status">
                <Loader2 size={18} className="animate-spin" aria-hidden="true" />
                Confirming your wallet…
              </p>
            )}
          </div>
        </Stage>
      </section>
    </PublicShell>
  )
}
