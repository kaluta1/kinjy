import type { ReactNode } from 'react'
import { formatChatText } from '@/lib/chatFormat'

/**
 * A forum post's formatting: the chat marks (*bold*, _italic_, `code`,
 * links, @mentions) plus what a longer post needs - headings (#, ##, ###),
 * bulleted lists (- or *), numbered lists (1.) and paragraphs.
 *
 * Built into React elements, never HTML: the text is one member's and the
 * page is another's, so nothing here can turn into a tag nobody wrote.
 */
export default function RichText({ text, className }: { text: string; className?: string }) {
  const blocks: ReactNode[] = []
  // **bold**, as most people type it, is read as the chat's *bold*.
  const lines = text
    .replace(/\r\n/g, '\n')
    .replace(/\*\*(\S(?:[^*\n]*\S)?)\*\*/g, '*$1*')
    .split('\n')
  let list: { ordered: boolean; items: string[] } | null = null
  let paragraph: string[] = []
  let key = 0

  const flushParagraph = () => {
    if (paragraph.length) {
      const k = `p${key++}`
      blocks.push(
        <p key={k} className="whitespace-pre-wrap">
          {formatChatText(paragraph.join('\n'), k)}
        </p>,
      )
      paragraph = []
    }
  }
  const flushList = () => {
    if (list) {
      const k = `l${key++}`
      const items = list.items.map((item, i) => <li key={`${k}-${i}`}>{formatChatText(item, `${k}-${i}`)}</li>)
      blocks.push(
        list.ordered ? (
          <ol key={k} className="list-decimal space-y-1 ps-5">
            {items}
          </ol>
        ) : (
          <ul key={k} className="list-disc space-y-1 ps-5">
            {items}
          </ul>
        ),
      )
      list = null
    }
  }

  for (const line of lines) {
    const heading = /^(#{1,3})\s+(.+)$/.exec(line)
    const bullet = /^\s*[-*]\s+(.+)$/.exec(line)
    const numbered = /^\s*\d+[.)]\s+(.+)$/.exec(line)
    if (heading) {
      flushParagraph()
      flushList()
      const k = `h${key++}`
      const size = heading[1].length === 1 ? 'text-lg' : heading[1].length === 2 ? 'text-base' : 'text-sm'
      blocks.push(
        <p key={k} role="heading" aria-level={heading[1].length + 2} className={`${size} font-bold text-text-hi`}>
          {formatChatText(heading[2], k)}
        </p>,
      )
    } else if (bullet || numbered) {
      flushParagraph()
      const ordered = Boolean(numbered)
      if (list && list.ordered !== ordered) flushList()
      list = list ?? { ordered, items: [] }
      list.items.push((bullet ?? numbered)![1])
    } else if (!line.trim()) {
      flushParagraph()
      flushList()
    } else {
      flushList()
      paragraph.push(line)
    }
  }
  flushParagraph()
  flushList()
  return <div className={className ?? 'space-y-2 text-sm leading-relaxed text-text-mid'}>{blocks}</div>
}
