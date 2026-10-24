import { Link } from 'react-router'
import { FileText, ArrowRight } from 'lucide-react'
import PublicShell from '@/components/landing/PublicShell'
import { ClosingStage } from '@/components/landing/PageKit'
import { KL_BTN_GHOST, KL_BTN_GOLD } from '@/components/landing/kl-classes'
import SafetyHero from '@/components/safety/SafetyHero'
import ModerationStack from '@/components/safety/ModerationStack'
import AgeModes from '@/components/safety/AgeModes'
import HonestContent from '@/components/safety/HonestContent'
import PasskeysSection from '@/components/safety/PasskeysSection'
import DeletionStepper from '@/components/safety/DeletionStepper'
import TranslationPrivacy from '@/components/safety/TranslationPrivacy'
import EarlyWarningSystem from '@/components/safety/EarlyWarningSystem'
import CrisisAlertMode from '@/components/safety/CrisisAlertMode'
import ProvenanceSigning from '@/components/safety/ProvenanceSigning'
import VoiceFirstAccess from '@/components/safety/VoiceFirstAccess'
import WhoOperatesKinjy from '@/components/safety/WhoOperatesKinjy'

/**
 * Safety, Privacy & Account Control — /safety.
 * Reassuring, principled, transparent. Layered moderation, age-appropriate spaces,
 * provenance & Community Notes, passkeys (no central biometrics), self-service
 * deletion, and privacy-first translation routing.
 */
export default function Safety() {
  return (
    <PublicShell>
      <SafetyHero />
      <ModerationStack />
      <AgeModes />
      <HonestContent />
      <PasskeysSection />
      <DeletionStepper />
      <TranslationPrivacy />
      <EarlyWarningSystem />
      <CrisisAlertMode />
      <ProvenanceSigning />
      <VoiceFirstAccess />

      {/* Last before the call to action: a member who has read what the
          platform promises should finish by learning who is promising it. */}
      <WhoOperatesKinjy />

      <ClosingStage
        eyebrow="Trust by design"
        title={
          <>
            Trust, <span className="text-[var(--kl-gold-deep)]">verified.</span>
          </>
        }
      >
        <div className="mt-12 flex flex-col flex-wrap items-center justify-center gap-4 sm:flex-row">
          <button type="button" className={KL_BTN_GHOST}>
            <FileText size={16} aria-hidden="true" /> Read the transparency report
          </button>
          <Link to="/app" className={KL_BTN_GOLD}>
            See account controls in the app <ArrowRight size={17} aria-hidden="true" />
          </Link>
        </div>
      </ClosingStage>
    </PublicShell>
  )
}
