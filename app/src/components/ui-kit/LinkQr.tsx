import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { Download } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * A link, as something a phone can scan.
 *
 * Every place Kinjy hands somebody a link to send — a post, an invitation, a
 * community, a group — also offers it as a code. A link is how you send
 * something to a chat; a code is how you send it to the person standing next
 * to you, or put it on a poster, a flyer or a slide, where nobody is going to
 * type out an address.
 *
 * Drawn black on white whatever the theme, and that is not negotiable: scanners
 * read dark modules on a light ground, and a "dark mode" code - light on dark -
 * is one that half the phone cameras in use will not read at all. The quiet
 * margin is part of the code for the same reason.
 *
 * Generated in the browser from the exact string being shared, so the code and
 * the link beside it can never disagree, and nothing about who is sharing what
 * is sent anywhere to produce it.
 */
export default function LinkQr({
  url,
  label,
  filename = 'kinjy-qr',
  size = 128,
  caption,
  className,
}: {
  url: string
  /** What the code opens, for a screen reader: "the invitation link", "this post". */
  label: string
  /** For the downloaded file, without the extension. */
  filename?: string
  /** Drawn size in CSS pixels. The image is generated larger, so it prints sharp. */
  size?: number
  /** One line under the code, when the surface needs to say what it is for. */
  caption?: string
  className?: string
}) {
  const [image, setImage] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    // Four times the drawn size: a downloaded code goes onto paper, and a code
    // that is crisp on a screen at 128px is a blur on an A4 poster.
    QRCode.toDataURL(url, {
      margin: 2,
      width: Math.max(320, size * 4),
      errorCorrectionLevel: 'M',
      color: { dark: '#000000', light: '#ffffff' },
    })
      .then((data) => live && setImage(data))
      .catch(() => live && setImage(null))
    return () => {
      live = false
    }
  }, [url, size])

  return (
    <figure className={cn('flex flex-col items-center gap-1.5', className)}>
      {image ? (
        <img
          src={image}
          alt={`QR code that opens ${label}`}
          width={size}
          height={size}
          className="rounded-lg bg-white p-1"
          style={{ width: size, height: size }}
        />
      ) : (
        // Held at the final size while it is drawn, so nothing under it jumps.
        <span
          aria-hidden="true"
          className="rounded-lg bg-text-low/10"
          style={{ width: size, height: size }}
        />
      )}
      {caption && <figcaption className="text-center text-[0.7rem] text-text-low">{caption}</figcaption>}
      {image && (
        <a
          href={image}
          download={`${filename}.png`}
          className="inline-flex items-center gap-1 text-[0.7rem] font-semibold text-text-mid hover:text-gold-soft"
        >
          <Download size={11} aria-hidden="true" />
          Download
        </a>
      )}
    </figure>
  )
}
