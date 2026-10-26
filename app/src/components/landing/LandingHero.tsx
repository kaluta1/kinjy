import { Link } from 'react-router'
import { Trans, useTranslation } from 'react-i18next'
import { StoreBadges } from './shared'
import { useJoinTarget } from './useJoinTarget'

/** A photo inside an absolutely placed card of the hero collage. */
function Photo({ src, alt }: { src: string; alt: string }) {
  return <img src={src} alt={alt} className="absolute inset-0 h-full w-full object-cover" loading="eager" />
}

/** The collage: a community, a story, a post, an avatar. Decorative; also used on the sign-in page. */
export function HeroCollage({
  className = 'relative w-full max-w-[640px] justify-self-end',
  english = false,
}: {
  className?: string
  /** Ignore the browser language: the sign-in page is English only. */
  english?: boolean
}) {
  const { t: translate, i18n } = useTranslation()
  const t = english ? i18n.getFixedT('en') : translate
  return (
    <div className={className} style={{ aspectRatio: '1 / 1.08' }} aria-hidden="true">
      <div
        className="kl-rise kl-card-shadow absolute overflow-hidden rounded-2xl bg-[var(--kl-paper-2)]"
        style={{ left: '4%', top: '18%', width: '38%', height: '44%', ['--kl-delay' as string]: '.25s' }}
      >
        <Photo src="/landing/commu.jpg" alt="" />
        <div className="absolute left-3.5 top-3.5 grid h-[34px] w-[34px] place-items-center rounded-[10px] bg-white shadow-[0_4px_10px_rgba(0,0,0,.12)]">
          <div className="flex">
            <span className="h-[11px] w-[11px] rounded-full bg-[var(--kl-night)]" />
            <span className="-ms-1 h-[11px] w-[11px] rounded-full border-2 border-[var(--kl-night)]" />
          </div>
        </div>
      </div>

      <div
        className="kl-rise kl-card-shadow absolute overflow-hidden rounded-2xl bg-[#E3ECF7]"
        style={{ left: '36%', top: '4%', width: '52%', height: '72%', ['--kl-delay' as string]: '.1s' }}
      >
        <Photo src="/landing/story.jpg" alt="" />
        <div className="absolute inset-x-[18px] top-4 flex gap-1.5">
          <span className="h-1 flex-1 rounded bg-white" />
          <span className="h-1 flex-1 overflow-hidden rounded bg-white/45">
            <span className="kl-grow block h-full bg-white" />
          </span>
          <span className="h-1 flex-1 rounded bg-white/45" />
        </div>
        <div className="absolute inset-x-4 bottom-4 flex items-center gap-2">
          <span className="flex h-10 flex-1 items-center rounded-[20px] border-2 border-white/90 bg-white/15 px-4 text-[13px] text-white backdrop-blur-md">
            {t('landing.hero.reply')}
          </span>
          <span className="h-10 w-10 rounded-full border-2 border-white/90" />
        </div>
      </div>

      <div
        className="kl-floatb absolute grid h-[74px] w-[74px] place-items-center rounded-full bg-[var(--kl-gold-soft)] shadow-[0_16px_30px_-14px_rgba(169,118,28,.5)]"
        style={{ top: '6%', left: '10%' }}
      >
        <span className="kl-star h-[30px] w-[30px] bg-[var(--kl-night)]" />
      </div>

      <div
        className="kl-rise kl-card-shadow absolute rounded-[10px] bg-[var(--kl-surface)] p-3.5"
        style={{ left: '14%', top: '50%', width: '36%', ['--kl-delay' as string]: '.4s' }}
      >
        <div className="mb-3 grid h-[30px] w-[30px] place-items-center rounded-[9px] bg-[var(--kl-sky)]">
          <span className="h-3 w-3 rotate-45 rounded-sm bg-white" />
        </div>
        <div className="relative overflow-hidden rounded-[10px] bg-[var(--kl-paper)]" style={{ aspectRatio: '4 / 3' }}>
          <Photo src="/landing/post.jpg" alt="" />
        </div>
        <div className="mb-2 mt-3.5 h-2.5 w-[88%] rounded-md bg-[var(--kl-paper-2)]" />
        <div className="h-2.5 w-[56%] rounded-md bg-[var(--kl-paper-2)]" />
      </div>

      <div
        className="kl-float kl-orb-ring absolute rounded-full p-[5px] shadow-[0_24px_40px_-18px_rgba(0,0,0,.35)]"
        style={{ left: '47%', top: '68%', width: '26%', aspectRatio: '1', ['--kl-dur' as string]: '7s' }}
      >
        <div className="relative h-full w-full overflow-hidden rounded-full border-4 border-[var(--kl-cutout)] bg-[#F6EBD3]">
          <Photo src="/landing/avatar.jpg" alt="" />
        </div>
      </div>

      <div
        className="kl-floatb absolute grid h-[76px] w-[76px] place-items-center rounded-full bg-[var(--kl-coral)] shadow-[0_18px_32px_-12px_rgba(224,120,86,.6)]"
        style={{ right: '2%', top: '60%', ['--kl-dur' as string]: '5s' }}
      >
        <span className="text-[34px] leading-none text-white">♥</span>
      </div>
    </div>
  )
}

export default function LandingHero() {
  const join = useJoinTarget()
  const { t } = useTranslation()
  return (
    <header className="kl-pad-x relative pb-12 pt-14">
      {/* The headline beside its words and buttons, the banner under both,
          as wide as the hero: ten people need the room a side column lacks. */}
      <div className="kl-split kl-rise items-end gap-x-[clamp(40px,6vw,96px)] gap-y-8">
        <h1
          className="kl-serif font-semibold"
          style={{ fontSize: 'clamp(56px, 7.6vw, 104px)', lineHeight: 0.92, letterSpacing: '-.02em' }}
        >
          {/* <Trans> rather than two keys: the highlighted word sits in a
              different place in every language - at the end in French, in the
              middle in Chinese - so the sentence has to be translated whole
              and carry the emphasis with it. */}
          <Trans i18nKey="hero.title">
            A new way to <span className="text-[var(--kl-gold-deep)]">connect</span>.
          </Trans>
        </h1>
        <div className="flex flex-col gap-8">
          <div>
            <p className="mb-8 max-w-[460px] text-[19px] leading-[1.55] text-[var(--kl-mid)]" style={{ textWrap: 'pretty' }}>
              {t('hero.sub')}
            </p>
            <Link
              to={join.to}
              className="kl-sheen inline-flex items-center gap-[18px] rounded-[20px] py-[7px] pe-[7px] ps-[30px] text-[17px] font-semibold shadow-[0_14px_30px_-12px_rgba(169,118,28,.55)] transition-transform hover:-translate-y-0.5"
            >
              {join.to.startsWith('/join') ? t('hero.cta') : join.label}
              <span className="grid h-12 w-12 place-items-center rounded-full bg-white text-xl text-[var(--kl-night)]" aria-hidden="true">
                →
              </span>
            </Link>
          </div>
          <div className="flex flex-col gap-4 border-t border-[var(--kl-paper-2)] pt-7">
            <span className="text-sm font-semibold text-[var(--kl-low)]">{t('hero.app')}</span>
            <StoreBadges />
          </div>
        </div>
      </div>

      {/* The whole picture, never cropped: width follows the hero, height
          follows the image. Seen on arrival, so loaded first, not lazily. */}
      <img
        src="/landing/kinjy-homepage-banner.webp"
        alt="Diverse men and women wearing Kinjy T-shirts featuring family trees, digital memorials, and social media icons."
        width={800}
        height={267}
        loading="eager"
        fetchPriority="high"
        decoding="async"
        className="kl-rise kl-card-shadow mt-[clamp(32px,5vw,56px)] block h-auto w-full rounded-2xl"
        style={{ ['--kl-delay' as string]: '.15s' }}
      />
    </header>
  )
}
