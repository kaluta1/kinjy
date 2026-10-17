import MentionLink from '@/components/social/MentionLink'
import type { ReactNode } from 'react'

/**
 * WhatsApp's text marks, for messages and comments.
 *
 * `*bold*`, `_italic_`, `~strikethrough~`, `` `monospace` `` and a ```` ``` ````
 * block. Nothing else: this is the set people already type without being
 * taught, which is the whole reason to support it rather than invent a
 * formatting language of our own.
 *
 * Parsed into React elements, never into HTML. There is no `dangerouslySet…`
 * here and there must never be one: the text is written by one member and read
 * by another, so a formatter that produced markup would be a way to put a
 * script in somebody else's conversation. A tag this file does not create
 * cannot appear.
 *
 * The marks follow WhatsApp's own boundary rules, because the surprising part
 * of a formatter is not what it formats but what it formats by accident:
 *
 * * An opening mark must start a word — at the beginning, or after a space or
 *   an opening bracket. So `2*3*4` stays arithmetic and `snake_case_name`
 *   stays a name.
 * * It must be followed by something that is not a space, and the closing mark
 *   must be preceded by something that is not a space. So `a * b * c` is not
 *   bold, and a lone asterisk is just an asterisk.
 * * Inline marks do not cross a line break. A `*` opened at the end of a
 *   paragraph does not reach down and bold the next one.
 * * Monospace is literal: `` `*not bold*` `` shows the asterisks, which is the
 *   only way to write them down.
 */

const LINK_RE = /((?:https?:\/\/|www\.)[^\s<>()[\]]+)/gi

/** Where a mark is allowed to open: the start of a word, not the middle of one. */
function opensHere(previous: string | undefined): boolean {
  return previous === undefined || /[\s([{<"'¿¡—–]/.test(previous)
}

interface Mark {
  /** The delimiter, as typed. */
  delimiter: string
  /** How it is drawn. */
  render: (children: ReactNode, key: string) => ReactNode
  /** Monospace keeps its contents exactly as written: no marks inside. */
  literal?: boolean
  /** A fenced block, which is the only mark allowed to span lines. */
  multiline?: boolean
}

// Longest delimiters first: ``` has to be tried before `.
const MARKS: Mark[] = [
  {
    delimiter: '```',
    literal: true,
    multiline: true,
    render: (children, key) => (
      <code
        key={key}
        className="my-0.5 block overflow-x-auto whitespace-pre rounded-md bg-text-hi/[0.09] px-2 py-1.5 font-mono text-[0.86em]"
      >
        {children}
      </code>
    ),
  },
  {
    delimiter: '`',
    literal: true,
    render: (children, key) => (
      <code key={key} className="rounded bg-text-hi/[0.09] px-1 py-px font-mono text-[0.88em]">
        {children}
      </code>
    ),
  },
  {
    delimiter: '*',
    render: (children, key) => (
      <strong key={key} className="font-semibold">
        {children}
      </strong>
    ),
  },
  {
    delimiter: '_',
    render: (children, key) => <em key={key}>{children}</em>,
  },
  {
    delimiter: '~',
    render: (children, key) => <del key={key}>{children}</del>,
  },
]

interface Found {
  mark: Mark
  inner: string
  /** Index just past the closing delimiter. */
  end: number
}

/** A mark opening exactly at `at`, with its closing delimiter found. */
function markAt(text: string, at: number): Found | null {
  for (const mark of MARKS) {
    const { delimiter } = mark
    if (!text.startsWith(delimiter, at)) continue
    if (!opensHere(text[at - 1])) continue

    const from = at + delimiter.length
    const next = text[from]
    // Nothing to format, or a space straight after the mark: not a mark.
    if (next === undefined || /\s/.test(next)) continue

    let search = from
    for (;;) {
      const close = text.indexOf(delimiter, search)
      if (close === -1) break
      const inner = text.slice(from, close)
      if (!inner) break
      if (!mark.multiline && inner.includes('\n')) break
      // A closing mark cannot float after a space, and the run must not be empty.
      if (/\s$/.test(inner)) {
        search = close + 1
        continue
      }
      return { mark, inner, end: close + delimiter.length }
    }
  }
  return null
}

/** Trailing punctuation belongs to the sentence, not to the address. */
function splitTrailing(raw: string): { href: string; text: string; trailing: string } {
  const match = /[.,;:!?)\]}'"]+$/.exec(raw)
  const trailing = match ? match[0] : ''
  const text = trailing ? raw.slice(0, -trailing.length) : raw
  return { href: text.startsWith('www.') ? `https://${text}` : text, text, trailing }
}

/**
 * @handle mentions inside a run of plain text, as links to the person.
 *
 * Only at the start or after a space, as when typed: an email address has an
 * @ in it and names nobody. A trailing dot is the sentence's full stop.
 */
function mentionify(text: string, key: string, names: Record<string, string>): ReactNode[] {
  const parts = text.split(/(^|\s)(@[a-z0-9_.]{2,40})/i)
  if (parts.length === 1) return [text]
  return parts.map((part, index) => {
    if (!part) return null
    if (index % 3 !== 2) return part
    const trailing = /\.+$/.exec(part)?.[0] ?? ''
    const token = trailing ? part.slice(0, -trailing.length) : part
    const handle = token.slice(1).toLowerCase()
    return (
      <span key={`${key}-m${index}`}>
        <MentionLink handle={handle} name={names[handle]} className="underline-offset-2" />
        {trailing}
      </span>
    )
  })
}

/** Addresses inside a run of plain text, as links. */
function linkify(text: string, key: string, names: Record<string, string> = {}): ReactNode[] {
  const parts = text.split(LINK_RE)
  if (parts.length === 1) return mentionify(text, key, names)
  return parts.flatMap((part, index) => {
    if (!part) return null
    if (!/^(?:https?:\/\/|www\.)/i.test(part)) return mentionify(part, `${key}-t${index}`, names)
    const { href, text: label, trailing } = splitTrailing(part)
    if (!label) return part
    return (
      <span key={`${key}-l${index}`}>
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="underline underline-offset-2 hover:opacity-80"
        >
          {label}
        </a>
        {trailing}
      </span>
    )
  })
}

/**
 * The text of a message or comment, with its marks drawn and its addresses
 * linked.
 *
 * Newlines are left in the string: every caller renders inside
 * `whitespace-pre-wrap`, so a line break is a line break without a `<br>` per
 * paragraph.
 */
export function formatChatText(body: string, keyPrefix = 'f', names: Record<string, string> = {}): ReactNode[] {
  const out: ReactNode[] = []
  let plain = ''
  let index = 0

  const flush = () => {
    if (!plain) return
    out.push(...linkify(plain, `${keyPrefix}-${out.length}`, names))
    plain = ''
  }

  while (index < body.length) {
    const found = markAt(body, index)
    if (found) {
      flush()
      const key = `${keyPrefix}-${index}`
      out.push(
        found.mark.render(
          found.mark.literal ? found.inner : formatChatText(found.inner, `${key}i`, names),
          key,
        ),
      )
      index = found.end
      continue
    }
    plain += body[index]
    index += 1
  }
  flush()
  return out
}

/**
 * Whether Enter should send rather than start a new line.
 *
 * A keyboard sends on Enter and breaks the line on Shift+Enter, which is what
 * every desktop messenger does. A touch keyboard does the opposite: its Enter
 * key is a return key with no Shift beside it, so sending on it would mean a
 * phone could not write a second line at all. The send button is right there.
 */
export function enterSends(): boolean {
  if (typeof window === 'undefined') return true
  return window.matchMedia('(pointer: fine)').matches
}
