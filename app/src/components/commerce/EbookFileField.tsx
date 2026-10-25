import { useRef, useState } from 'react'
import { BookOpen, FileUp, RefreshCw, Trash2, X } from 'lucide-react'
import { ApiError, kaluta } from '@/lib/api'
import { fileSize } from '@/lib/ebooks'
import { cn } from '@/lib/utils'

export interface EbookFileValue {
  id: string
  filename: string
  size_bytes: number
  format: 'pdf' | 'epub'
}

const ACCEPT = '.pdf,.epub,application/pdf,application/epub+zip'

/**
 * The ebook itself (or its free sample) - a file, not a picture.
 *
 * Kept apart from the cover on purpose: the cover is public, this is what the
 * buyer pays for. The server decides what the file is from its bytes; the
 * checks here only spare the seller a wasted upload.
 */
export default function EbookFileField({
  id,
  purpose,
  label,
  hint,
  maxMb,
  value,
  onChange,
  onBusy,
  invalid,
  describedBy,
}: {
  id: string
  purpose: 'ebook' | 'ebook_sample'
  label: string
  hint: string
  maxMb: number
  value: EbookFileValue | null
  onChange: (value: EbookFileValue | null) => void
  onBusy?: (busy: boolean) => void
  invalid?: boolean
  describedBy?: string
}) {
  const input = useRef<HTMLInputElement>(null)
  const abort = useRef<AbortController | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const [uploading, setUploading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const busy = (on: boolean) => {
    onBusy?.(on)
    if (!on) {
      setProgress(null)
      setUploading(null)
    }
  }

  const choose = async (file: File | undefined) => {
    if (input.current) input.current.value = ''
    if (!file) return
    setError(null)
    const extension = file.name.toLowerCase().split('.').pop()
    if (extension !== 'pdf' && extension !== 'epub') {
      setError('Choose a PDF (.pdf) or EPUB (.epub) file.')
      return
    }
    if (file.size > maxMb * 1024 * 1024) {
      setError(`This file is ${fileSize(file.size)}. The limit is ${maxMb} MB.`)
      return
    }
    if (file.size === 0) {
      setError('This file is empty.')
      return
    }
    abort.current = new AbortController()
    setUploading(file.name)
    setProgress(0)
    busy(true)
    try {
      const uploaded = await kaluta.media.uploadWithProgress(file, (f) => setProgress(f), {
        purpose,
        signal: abort.current.signal,
      })
      onChange({
        id: uploaded.id,
        filename: uploaded.filename || file.name,
        size_bytes: uploaded.size_bytes,
        format: uploaded.content_type === 'application/epub+zip' ? 'epub' : 'pdf',
      })
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 0 && err.message === 'Upload cancelled')) {
        setError(err instanceof ApiError ? err.message : 'The upload failed. Try again.')
      }
    } finally {
      busy(false)
    }
  }

  return (
    <div>
      <p id={`${id}-label`} className="caption mb-1.5">
        {label}
      </p>
      <p id={`${id}-hint`} className="mb-2 text-xs text-text-low">
        {hint}
      </p>
      <input
        ref={input}
        id={id}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        aria-labelledby={`${id}-label`}
        aria-describedby={[`${id}-hint`, describedBy].filter(Boolean).join(' ')}
        aria-invalid={invalid || undefined}
        onChange={(e) => void choose(e.target.files?.[0])}
      />

      {uploading !== null ? (
        <div className="rounded-2xl border border-white/10 bg-ink-2/60 p-3" role="status" aria-live="polite">
          <div className="flex items-center gap-2 text-sm text-text-hi">
            <FileUp size={16} className="shrink-0 text-gold-soft" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{uploading}</span>
            <span className="mono-data text-xs text-text-mid">{Math.round((progress ?? 0) * 100)}%</span>
            <button
              type="button"
              onClick={() => abort.current?.abort()}
              aria-label="Cancel the upload"
              className="rounded-full p-1.5 text-text-low hover:text-text-hi"
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10" aria-hidden="true">
            <div className="h-full bg-gold transition-[width]" style={{ width: `${Math.round((progress ?? 0) * 100)}%` }} />
          </div>
        </div>
      ) : value ? (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-success/30 bg-success/[0.06] p-3">
          <BookOpen size={18} className="shrink-0 text-success" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-text-hi">{value.filename}</span>
            <span className="block text-xs text-text-mid">
              {value.format.toUpperCase()} · {fileSize(value.size_bytes)} · uploaded
            </span>
          </span>
          <button
            type="button"
            onClick={() => input.current?.click()}
            className="inline-flex min-h-[40px] items-center gap-1.5 rounded-full border border-white/12 px-3.5 text-xs font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
          >
            <RefreshCw size={13} aria-hidden="true" /> Replace
          </button>
          <button
            type="button"
            onClick={() => onChange(null)}
            className="inline-flex min-h-[40px] items-center gap-1.5 rounded-full border border-white/12 px-3.5 text-xs font-semibold text-text-mid hover:border-red-300/50 hover:text-red-200"
          >
            <Trash2 size={13} aria-hidden="true" /> Remove
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => input.current?.click()}
          className={cn(
            'flex min-h-[88px] w-full flex-col items-center justify-center gap-1 rounded-2xl border border-dashed px-4 py-4 text-center',
            invalid ? 'border-red-300/60' : 'border-white/20 hover:border-gold/50',
          )}
        >
          <FileUp size={20} className="text-gold-soft" aria-hidden="true" />
          <span className="text-sm font-semibold text-text-hi">Choose a PDF or EPUB</span>
          <span className="text-xs text-text-low">Up to {maxMb} MB</span>
        </button>
      )}

      {error && (
        <p role="alert" className="mt-2 text-xs text-red-200">
          {error}
        </p>
      )}
    </div>
  )
}
