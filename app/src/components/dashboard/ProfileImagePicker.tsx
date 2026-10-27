import { useEffect, useRef, useState } from 'react'
import { ImagePlus, Loader2, Trash2, X } from 'lucide-react'
import MemberAvatar from '@/components/social/MemberAvatar'
import { ApiError } from '@/lib/api'
import {
  PROFILE_IMAGE_TYPES,
  profileImageProblem,
  uploadProfileImage,
  type ProfileImagePurpose,
} from '@/lib/profileImages'
import { cn } from '@/lib/utils'

type Phase =
  | { kind: 'idle' }
  | { kind: 'uploading'; progress: number }
  | { kind: 'processing' }
  | { kind: 'saving' }

interface ProfileImagePickerProps {
  purpose: ProfileImagePurpose
  /** What is on the profile now. */
  url: string | null
  /** For the initials shown when there is no avatar. */
  displayName: string
  /** Puts the asset on the profile (or removes it, with null). Throws on refusal. */
  /** The uploaded image's url comes second, for a caller that shows it before saving. */
  onChange: (assetId: string | null, url?: string | null) => Promise<void>
  disabled?: boolean
}

const LABELS: Record<ProfileImagePurpose, { name: string; hint: string }> = {
  avatar: { name: 'Profile photo', hint: 'JPEG, PNG or WebP, up to 5 MB. Shown as a circle.' },
  cover: { name: 'Cover image', hint: 'JPEG, PNG or WebP, up to 10 MB. Wide images work best.' },
}

/** One line per stage, so a screen reader hears three changes, not every percent. */
function statusText(phase: Phase): string {
  switch (phase.kind) {
    case 'uploading':
      return 'Uploading…'
    case 'processing':
      return 'Checking the image…'
    case 'saving':
      return 'Saving…'
    default:
      return ''
  }
}

/**
 * Choose, upload and apply one profile image.
 *
 * The image is applied as soon as it is ready rather than waiting for the
 * form's Save button: an uploaded image that is never saved is a stored file
 * nobody references, and leaving the page is exactly when that happens.
 */
export default function ProfileImagePicker({
  purpose,
  url,
  displayName,
  onChange,
  disabled = false,
}: ProfileImagePickerProps) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const [preview, setPreview] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const controllerRef = useRef<AbortController | null>(null)

  // Leaving the page mid-upload stops it rather than finishing in the background.
  useEffect(() => () => controllerRef.current?.abort(), [])
  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview)
  }, [preview])

  const busy = phase.kind !== 'idle'
  const label = LABELS[purpose]
  const shown = preview ?? url

  const choose = async (file: File | undefined) => {
    if (inputRef.current) inputRef.current.value = ''
    if (!file) return
    setError(null)
    const problem = profileImageProblem(file, purpose)
    if (problem) {
      setError(problem)
      return
    }

    const controller = new AbortController()
    controllerRef.current = controller
    setPreview(URL.createObjectURL(file))
    setPhase({ kind: 'uploading', progress: 0 })
    try {
      const image = await uploadProfileImage(file, purpose, {
        signal: controller.signal,
        onProgress: (progress) => setPhase({ kind: 'uploading', progress }),
        onStage: (stage) => {
          if (stage === 'processing') setPhase({ kind: 'processing' })
        },
      })
      setPhase({ kind: 'saving' })
      await onChange(image.asset_id, image.url)
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof ApiError ? err.message : 'The image could not be uploaded.')
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null
      setPreview(null)
      setPhase({ kind: 'idle' })
    }
  }

  const remove = async () => {
    setError(null)
    setPhase({ kind: 'saving' })
    try {
      await onChange(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The image could not be removed.')
    } finally {
      setPhase({ kind: 'idle' })
    }
  }

  const frame =
    purpose === 'avatar' ? (
      <MemberAvatar displayName={displayName} avatarUrl={shown} size={88} ring />
    ) : (
      <div className="relative h-28 w-full overflow-hidden rounded-2xl sm:h-32">
        {shown ? (
          <img src={shown} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="h-full w-full bg-gradient-to-r from-indigo/60 via-sky/40 to-gold/40" aria-hidden="true" />
        )}
      </div>
    )

  const button =
    'inline-flex items-center gap-1.5 rounded-full border px-3.5 py-2 text-xs font-semibold transition-colors disabled:opacity-40'

  return (
    <div className={cn(purpose === 'avatar' ? 'flex items-center gap-5' : 'space-y-3')}>
      <div className={cn('relative', busy && 'opacity-70')}>{frame}</div>

      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-text-hi">{label.name}</p>
        <p className="caption mt-0.5">{label.hint}</p>

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <input
            ref={inputRef}
            type="file"
            accept={PROFILE_IMAGE_TYPES.join(',')}
            className="peer sr-only"
            id={`profile-${purpose}`}
            disabled={disabled || busy}
            onChange={(event) => void choose(event.target.files?.[0])}
          />
          <label
            htmlFor={`profile-${purpose}`}
            aria-disabled={disabled || busy}
            className={cn(
              button,
              'cursor-pointer border-[var(--cloud-border)] text-text-mid hover:border-gold/40 hover:text-gold-soft',
              'peer-focus-visible:ring-2 peer-focus-visible:ring-gold/50',
              (disabled || busy) && 'pointer-events-none opacity-40',
            )}
          >
            <ImagePlus size={13} aria-hidden="true" />
            {url ? 'Change' : 'Upload'}
          </label>

          {busy && phase.kind !== 'saving' ? (
            <button
              type="button"
              onClick={() => controllerRef.current?.abort()}
              className={cn(button, 'border-[var(--cloud-border)] text-text-mid hover:text-text-hi')}
            >
              <X size={13} aria-hidden="true" />
              Cancel
            </button>
          ) : (
            url && (
              <button
                type="button"
                onClick={() => void remove()}
                disabled={disabled || busy}
                className={cn(button, 'border-red-400/30 text-red-200 hover:bg-red-500/10')}
              >
                <Trash2 size={13} aria-hidden="true" />
                Remove
              </button>
            )
          )}
        </div>

        <div aria-live="polite" className="mt-2 min-h-[1.25rem]">
          {busy && (
            <p className="flex items-center gap-2 text-xs text-text-mid">
              <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              {statusText(phase)}
              {phase.kind === 'uploading' && (
                <span aria-hidden="true" className="mono-data">
                  {Math.round(phase.progress * 100)}%
                </span>
              )}
            </p>
          )}
          {phase.kind === 'uploading' && (
            <div
              role="progressbar"
              aria-label={`${label.name} upload`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(phase.progress * 100)}
              className="mt-1.5 h-1 overflow-hidden rounded-full bg-text-hi/10"
            >
              <div
                className="h-full origin-left rounded-full transition-transform"
                style={{ transform: `scaleX(${phase.progress})`, background: 'var(--grad-arc)' }}
              />
            </div>
          )}
          {error && (
            <p role="alert" className="text-xs text-red-200">
              {error}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
