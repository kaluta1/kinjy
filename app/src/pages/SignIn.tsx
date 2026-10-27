import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { AlertCircle, Loader2 } from 'lucide-react'
import '@/components/landing/landing.css'
import { KlButton } from '@/components/landing/PageKit'
import { HeroCollage } from '@/components/landing/LandingHero'
import { Brand, ThemeToggle } from '@/components/landing/shared'
import { useLandingTheme } from '@/components/landing/useLandingTheme'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta } from '@/lib/api'
import {
  clearPendingRef,
  pendingInviter,
  pendingLink,
  pendingLinkDestination,
  pendingRef,
  refFromSearch,
} from '@/lib/share'
import { OPEN_MODULES, spelled } from '@/lib/features'
import { cn } from '@/lib/utils'

/**
 * Where to go after signing in.
 *
 * Only a path on this site. A `next` parameter that will follow anything is an
 * open redirect, and an open redirect on a sign-in page is a phishing tool:
 * the link really does come from kinjy.com, and really does hand the visitor
 * to somebody else's page afterwards. So: must start with a single slash -
 * "//evil.example" and "https://evil.example" are both refused - and must not
 * be the sign-in page itself, which would loop.
 */
function safeNext(value: string | null): string {
  // The feed, not the dashboard. Somebody who has just signed in came to read
  // what is new, not to look at their own earnings and settings - landing them
  // on their profile makes them navigate once before the product starts.
  if (!value) return '/hub'
  let path: string
  try {
    path = decodeURIComponent(value)
  } catch {
    return '/hub'
  }
  if (!path.startsWith('/') || path.startsWith('//')) return '/hub'
  if (path.startsWith('/join')) return '/hub'
  return path
}

type Mode = 'signin' | 'signup'

/** Mirrors auth-service's HANDLE_RE so the member is told before the round-trip. */
const HANDLE_RE = /^[a-z0-9](?:[a-z0-9_.]{1,38}[a-z0-9])$/

function suggestHandle(name: string): string {
  return name
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '.')
    .replace(/^\.+|\.+$/g, '')
    .slice(0, 40)
}

/** auth-service requires ≥10 chars and refuses all-letters or all-digits. */
function passwordProblem(value: string): string | null {
  if (value.length < 10) return 'At least 10 characters.'
  if (/^\d+$/.test(value) || /^[a-zA-Z]+$/.test(value))
    return 'Mix letters with digits or symbols.'
  return null
}

export default function SignIn() {
  // This page is English only, whatever language the browser asks for.
  const { i18n } = useTranslation()
  const t = i18n.getFixedT('en')
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { user, signIn, signUp } = useAuth()
  const { theme } = useLandingTheme()

  // Sign-up when asked for - and when the link carries an invitation code
  // without saying either way. An invitation is by definition for somebody who
  // has no account, and the invite links members have been sending for weeks
  // are `/join?ref=CODE` with no mode: every one of them opened on "Welcome
  // back" and a password field. An explicit `mode=signin` still wins.
  const [mode, setMode] = useState<Mode>(() => {
    const asked = params.get('mode')
    if (asked === 'signup') return 'signup'
    if (asked === 'signin') return 'signin'
    return params.get('ref') ? 'signup' : 'signin'
  })
  const [email, setEmail] = useState(params.get('email') ?? '')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [handle, setHandle] = useState('')
  const [birthDate, setBirthDate] = useState('')
  const [handleEdited, setHandleEdited] = useState(false)
  // The code on this URL wins, then one remembered from a shared link opened
  // earlier. Without the second, somebody who arrived on a shared post and
  // read two more pages before signing up credited nobody - the code lived in
  // the URL of the first page and died with it.
  //
  // Read through refFromSearch rather than straight off the query string: it
  // validates, it uppercases, and it knows the other names an invitation
  // comes back under after a trip through a mail client. `?ref=` with nothing
  // after it used to count as an answer and silently beat the remembered
  // code.
  const [referral, setReferral] = useState(refFromSearch(params.toString()) ?? pendingRef())
  // Whether that code is one this browser remembered rather than one the
  // person in front of us typed. It decides what happens when the server does
  // not recognise it.
  const referralWasRemembered = !refFromSearch(params.toString()) && Boolean(pendingRef())
  // Who the stored code belongs to, if ReferralWatch managed to ask.
  const inviter = pendingInviter()
  // A short link's token, held when the invitation came that way. Sent as
  // the token: the server knows whose it is, the browser never sees a code.
  const [link] = useState(() => (refFromSearch(params.toString()) ? '' : pendingLink()))
  // Where that link was taking them. A new member goes there after signing
  // up - the post or community somebody sent them, not an empty feed.
  const [linkDestination] = useState(() => pendingLinkDestination())
  const afterAuth = (chosen: Mode) => {
    const next = params.get('next')
    if (next) return safeNext(next)
    if (chosen === 'signup' && linkDestination) return safeNext(linkDestination)
    return '/hub'
  }
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The invitation the server holds for this browser (kept for the
  // attribution window when an invitation link was followed). When there is
  // one it is applied at sign-up whatever the form says, so the form says so
  // instead of offering an "optional" code box that would not change it.
  const [heldInviter, setHeldInviter] = useState<string | null>(null)
  useEffect(() => {
    if (mode !== 'signup') return
    let live = true
    const fromUrl = refFromSearch(params.toString())
    kaluta.auth
      .heldInvitation()
      .then(async (held) => {
        if (held.inviter) return held.inviter.display_name
        // An invitation this browser remembered before the server held them
        // (or on this very address): hand it over to be checked and held.
        const code = fromUrl ?? pendingRef()
        const token = code ? '' : pendingLink()
        if (!code && !token) return null
        const captured = await kaluta.auth.captureInvitation(code ? { code } : { link: token })
        return captured.inviter?.display_name ?? null
      })
      .then((name) => live && setHeldInviter(name ?? null))
      .catch(() => live && setHeldInviter(null))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // Already signed in? The sign-in page has nothing to offer.
  useEffect(() => {
    if (user) navigate(afterAuth(mode), { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, navigate])

  // Keep the handle in step with the name until the member takes it over.
  useEffect(() => {
    if (mode === 'signup' && !handleEdited) setHandle(suggestHandle(displayName))
  }, [displayName, handleEdited, mode])

  const handleError = useMemo(() => {
    if (mode !== 'signup' || !handle) return null
    return HANDLE_RE.test(handle)
      ? null
      : '3–40 characters: lowercase letters, digits, dots or underscores.'
  }, [handle, mode])

  const passwordError = useMemo(
    () => (mode === 'signup' && password ? passwordProblem(password) : null),
    [password, mode],
  )

  const canSubmit =
    email.trim() !== '' &&
    password !== '' &&
    !submitting &&
    (mode === 'signin' ||
      (displayName.trim().length >= 2 && !handleError && !passwordError && birthDate !== ''))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      if (mode === 'signin') {
        await signIn(email.trim(), password)
      } else {
        const register = (code?: string) =>
          signUp({
            email: email.trim(),
            password,
            display_name: displayName.trim(),
            handle,
            date_of_birth: birthDate,
            referral_code: code,
            // The short link, only when no code is given: a code typed or
            // carried on the address is the newer, explicit answer.
            referral_link: code ? undefined : link || undefined,
          })
        try {
          // A held invitation is applied by the server; nothing to send.
          await register(heldInviter ? undefined : referral.trim() || undefined)
        } catch (err) {
          const unknownCode =
            err instanceof ApiError && /referral code/i.test(err.message)
          if (!unknownCode || !referralWasRemembered) throw err
          // A code this browser remembered, which the server does not know.
          // Dropping it is better than refusing somebody an account over a
          // link they followed last week.
          clearPendingRef()
          setReferral('')
          await register(undefined)
        }
        // Spent. Leaving it would credit the same sharer again if this browser
        // ever creates a second account, which is not a referral but a bug
        // that pays.
        clearPendingRef()
      }
      navigate(afterAuth(mode), { replace: true })
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not reach the Kinjy API. Check that the stack is running.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  const field =
    'w-full rounded-[12px] border border-[var(--kl-paper-2)] bg-[var(--kl-bg)] px-4 py-3 text-[16px] text-[var(--kl-ink)] placeholder:text-[var(--kl-low)] focus:border-[var(--kl-gold)] focus:outline-none focus:ring-2 focus:ring-[var(--kl-gold)]/25'
  const label = 'mb-1.5 block text-[13px] font-semibold text-[var(--kl-mid)]'
  const hint = 'mt-1.5 block text-[13px] leading-snug text-[var(--kl-low)]'
  const fieldError = 'mt-1.5 block text-[13px] text-[#B23B3B]'

  return (
    <div
      className={`kl kl-plain ${theme === 'dark' ? 'force-dark' : 'force-light'} min-h-screen bg-[var(--kl-bg)] text-[var(--kl-ink)] lg:h-screen lg:overflow-hidden`}
      data-kl-theme={theme}
    >
      <div className="grid min-h-screen grid-cols-[minmax(0,1fr)] lg:h-full lg:min-h-0 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        {/* Right: the form */}
        <main className="flex min-w-0 flex-col lg:order-2 lg:h-full lg:overflow-y-auto px-[clamp(20px,5vw,72px)] py-6">
          <div className="flex items-center justify-between">
            <Link to="/" aria-label="Kinjy home" className="lg:invisible">
              <Brand size={40} text={22} />
            </Link>
            <ThemeToggle />
          </div>

          <div className="mx-auto flex w-full max-w-[460px] flex-1 flex-col justify-center py-10">
            <h1 className="text-[clamp(30px,3.4vw,42px)] font-bold leading-[1.05] tracking-[-0.035em]">
              {mode === 'signin' ? 'Welcome back' : 'Create your account'}
            </h1>
            <p className="mt-2 text-[15px] leading-relaxed text-[var(--kl-mid)]">
              {mode === 'signin'
                ? 'Sign in to pick up where you left off.'
                : `One account opens ${spelled(OPEN_MODULES).toLowerCase()} modules.`}
            </p>

            <div className="mt-7 flex gap-1 rounded-full bg-[var(--kl-paper)] p-1">
              {(['signin', 'signup'] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    setMode(m)
                    setError(null)
                  }}
                  className={cn(
                    'flex-1 rounded-full px-4 py-2.5 text-sm font-semibold transition-colors',
                    mode === m ? 'kl-sheen' : 'text-[var(--kl-mid)] hover:text-[var(--kl-ink)]',
                  )}
                >
                  {m === 'signin' ? 'Sign in' : 'Create account'}
                </button>
              ))}
            </div>

            <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
              {mode === 'signup' && (
                <>
                  <label className="block">
                    <span className={label}>{t('signin.yourName')}</span>
                    <input
                      className={field}
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      placeholder={t('signin.yourFullName')}
                      autoComplete="name"
                    />
                  </label>
                  <label className="block">
                    <span className={label}>{t('signin.dateOfBirth')}</span>
                    <input
                      type="date"
                      className={field}
                      value={birthDate}
                      onChange={(e) => setBirthDate(e.target.value)}
                      autoComplete="bday"
                      max={new Date().toISOString().slice(0, 10)}
                      aria-describedby="dob-why"
                    />
                    {/* Says what it is for, and nothing about what would qualify.
                        Telling someone the minimum age is telling them which date
                        to type instead. */}
                    <span id="dob-why" className={hint}>
                      {t('signin.weUseThisTo')}
                    </span>
                  </label>
                  <label className="block">
                    <span className={label}>{t('signin.handle')}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-[var(--kl-low)]">@</span>
                      <input
                        className={field}
                        value={handle}
                        onChange={(e) => {
                          setHandleEdited(true)
                          setHandle(e.target.value.toLowerCase())
                        }}
                        placeholder="your.handle"
                        autoComplete="username"
                        aria-invalid={Boolean(handleError)}
                      />
                    </div>
                    {handleError && <span className={fieldError}>{handleError}</span>}
                  </label>
                </>
              )}

              <label className="block">
                <span className={label}>{t('signin.email')}</span>
                <input
                  className={field}
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  autoComplete="email"
                  required
                />
              </label>

              <label className="block">
                <span className={label}>{t('signin.password')}</span>
                <input
                  className={field}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={mode === 'signup' ? 'At least 10 characters' : '••••••••••'}
                  autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
                  aria-invalid={Boolean(passwordError)}
                  required
                />
                {passwordError && <span className={fieldError}>{passwordError}</span>}
                {mode === 'signin' && (
                  <Link
                    to="/reset-password"
                    className="mt-2 inline-block text-[13px] text-[var(--kl-mid)] hover:text-[var(--kl-gold-deep)]"
                  >
                    {t('signin.forgotPassword')}
                  </Link>
                )}
              </label>

              {mode === 'signup' && heldInviter && (
                <div className="rounded-[12px] border border-[var(--kl-gold)] px-4 py-3 text-sm">
                  <p className="font-semibold text-[var(--kl-ink)]">
                    {t('signin.invitedLocked', {
                      name: heldInviter,
                      defaultValue: 'Your account will be linked to {{name}}, who invited you.',
                    })}
                  </p>
                  <p className={hint}>
                    {t('signin.invitedLockedHint', {
                      defaultValue:
                        'You opened their invitation link, so it is applied automatically when you join.',
                    })}
                  </p>
                </div>
              )}

              {mode === 'signup' && !heldInviter && (
                <label className="block">
                  <span className={label}>{t('signin.referralCodeOptional')}</span>
                  <input
                    className={field}
                    value={referral}
                    onChange={(e) => setReferral(e.target.value.toUpperCase())}
                    placeholder="ABCD1234"
                  />
                  <span className={hint}>
                    {/* A name, when the invitation came with one: "Invited by
                        Demo" is something a person can check, where a code in
                        a box is something they have to trust. */}
                    {inviter && (referral === pendingRef() || (link && !referral))
                      ? `Invited by ${inviter}`
                      : t('signin.creditsWhoeverInvitedYou')}
                  </span>
                </label>
              )}

              {error && (
                <div
                  role="alert"
                  className="flex items-start gap-2.5 rounded-[12px] border border-[#B23B3B]/40 bg-[#B23B3B]/10 px-4 py-3 text-sm text-[#B23B3B]"
                >
                  <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              )}

              <KlButton size="lg" className="w-full" disabled={!canSubmit} type="submit">
                {submitting && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                {mode === 'signin' ? 'Sign in' : 'Create your account'}
              </KlButton>
            </form>

            <p className="mt-5 text-center text-[13px] text-[var(--kl-low)]">
              {mode === 'signin' ? (
                <>
                  No account yet?{' '}
                  <button type="button" onClick={() => setMode('signup')} className="font-semibold text-[var(--kl-gold-deep)] hover:underline">
                    {t('signin.createOne')}
                  </button>
                </>
              ) : (
                <>
                  By creating an account you accept the{' '}
                  <Link to="/safety" className="font-semibold text-[var(--kl-gold-deep)] hover:underline">
                    {t('signin.communityStandards')}
                  </Link>
                  .
                </>
              )}
            </p>
          </div>
        </main>

        {/* Left: the landing, as a showcase */}
        <aside
          aria-label="About Kinjy"
          className="relative hidden min-w-0 overflow-hidden lg:order-1 lg:block lg:h-full"
          style={{ background: 'linear-gradient(160deg, var(--kl-stage-a), var(--kl-stage-b))' }}
        >
          <div aria-hidden="true" className="absolute -right-24 -top-24 h-[420px] w-[420px] rounded-full opacity-35 blur-[110px]" style={{ background: 'var(--kl-sky)' }} />
          <div aria-hidden="true" className="absolute -bottom-32 -left-24 h-[420px] w-[420px] rounded-full opacity-40 blur-[110px]" style={{ background: '#D9A648' }} />
          <div className="relative flex h-full items-center justify-center px-[clamp(32px,5vw,84px)] py-10">
            <Link to="/" aria-label="Kinjy home" className="absolute left-[clamp(32px,5vw,84px)] top-6">
              <Brand size={40} text={22} />
            </Link>
            <div className="flex w-full flex-col items-center">
              <HeroCollage english className="relative w-[min(100%,min(480px,calc((100vh-240px)/1.08)))] shrink-0" />
              <p className="mt-6 max-w-[420px] text-center text-[17px] leading-[1.55] text-[var(--kl-mid)]" style={{ textWrap: 'pretty' }}>
                {t('hero.sub')}
              </p>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
