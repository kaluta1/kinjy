import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useAppTheme } from '@/components/appdemo/theme'
import { kaluta, type PersonBrief } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Finishing an @mention while somebody types it.
 *
 * Mentions worked, but only if you already knew the handle exactly - and
 * handles are the one thing on this platform nobody is shown any more, now
 * that names are drawn everywhere instead. Asking a member to recall
 * "@k_mbeki3" from memory to credit Kofi Mbeki is asking them not to bother.
 *
 * So: type `@` and two letters, pick a face. What goes into the text is still
 * the handle, because that is what the server resolves and what has to
 * survive an edit; the list is how you find it.
 */

type Person = PersonBrief & { user_id: string }

/**
 * Enough to be a search.
 *
 * Two, because `/users/search` answers a one-character query with an empty
 * list - so asking was a wasted request and a wasted moment, and the member
 * saw nothing happen on the keystroke where something should have.
 */
const MIN_QUERY = 2
/** A list you can take in at a glance, and one request's worth of rows. */
const LIMIT = 6

/**
 * The token being typed, if the caret is inside one.
 *
 * An `@` only starts a mention at the beginning of the text or after a space:
 * an email address and a URL both contain one, and neither is a mention of
 * anybody.
 */
function tokenAt(value: string, caret: number): { query: string; start: number } | null {
  const before = value.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at < 0) return null
  if (at > 0 && !/\s/.test(before[at - 1])) return null
  const query = before.slice(at + 1)
  // A handle has no spaces in it, so a space ends the token - and with it any
  // reason to keep showing a list.
  if (/[^a-z0-9_.]/i.test(query)) return null
  return { query, start: at }
}

export function useMentionSuggest(
  value: string,
  setValue: (next: string) => void,
  elRef: React.RefObject<HTMLInputElement | HTMLTextAreaElement | null>,
  /**
   * Where the names come from. The whole platform by default; a chat passes
   * the people in the room, because a mention of somebody outside it reaches
   * nobody.
   */
  search?: (query: string) => Promise<Person[]>,
) {
  const [items, setItems] = useState<Person[]>([])
  const [index, setIndex] = useState(0)
  const [query, setQuery] = useState<string | null>(null)
  const [start, setStart] = useState(0)
  // Set when the member dismisses the list with Escape. Cleared as soon as
  // they type something else, because dismissing one list is not a standing
  // instruction to never suggest again.
  const dismissed = useRef<string | null>(null)
  const latest = useRef(0)

  // Reading the caret from the element rather than tracking it: every way of
  // moving it - a click, an arrow key, a paste - has to count, and listening
  // for all of them separately is how one of them gets missed.
  const sync = useCallback(() => {
    const el = elRef.current
    if (!el) return
    const token = tokenAt(value, el.selectionStart ?? value.length)
    if (!token || token.query.length < MIN_QUERY) {
      setQuery(null)
      return
    }
    if (dismissed.current === token.query) {
      setQuery(null)
      return
    }
    dismissed.current = null
    setQuery(token.query)
    setStart(token.start)
  }, [value, elRef])

  useEffect(sync, [sync])

  useEffect(() => {
    if (query === null) {
      setItems([])
      return
    }
    // Debounced: a handle is typed a letter at a time and each letter would
    // otherwise be a search.
    const ticket = ++latest.current
    const timer = window.setTimeout(() => {
      void (search ? search(query) : kaluta.people.search(query, LIMIT).then((page) => page.items))
        .then((found) => {
          // Answers arrive out of order; the one for "ko" must not replace the
          // one for "kofi".
          if (ticket !== latest.current) return
          setItems(found.slice(0, LIMIT))
          setIndex(0)
        })
        .catch(() => {
          if (ticket === latest.current) setItems([])
        })
    }, 160)
    return () => window.clearTimeout(timer)
  }, [query, search])

  const open = query !== null && items.length > 0

  /** Put the handle in the text and the caret after it. */
  const pick = useCallback(
    (person: Person) => {
      const el = elRef.current
      const caret = el?.selectionStart ?? value.length
      const next = `${value.slice(0, start)}@${person.handle} ${value.slice(caret)}`
      setValue(next)
      setQuery(null)
      // After the state lands, not before: setting it now puts the caret in
      // the old text and the browser moves it back on the next render.
      const to = start + person.handle.length + 2
      window.requestAnimationFrame(() => {
        el?.focus()
        el?.setSelectionRange(to, to)
      })
    },
    [value, start, setValue, elRef],
  )

  /**
   * Keys the list owns while it is open.
   *
   * Returns true when it handled the key, so the caller knows to stop - Enter
   * in a comment box submits, and choosing a name out of a list is not
   * submitting.
   */
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent): boolean => {
      if (!open) return false
      if (event.key === 'ArrowDown') {
        setIndex((i) => (i + 1) % items.length)
        return true
      }
      if (event.key === 'ArrowUp') {
        setIndex((i) => (i - 1 + items.length) % items.length)
        return true
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        const person = items[index]
        if (person) pick(person)
        return true
      }
      if (event.key === 'Escape') {
        dismissed.current = query
        setQuery(null)
        return true
      }
      return false
    },
    [open, items, index, pick, query],
  )

  return { open, items, index, setIndex, pick, onKeyDown, sync }
}

/**
 * The list itself, which picks its own side.
 *
 * It was the caller's choice at first - above the box in a comment thread,
 * below it in the composer - and that was wrong the moment the comment box
 * appeared somewhere else. Inside the post dialog the box sits near the top,
 * so "above" put the list at y = -10, off the screen and clipped by the
 * dialog. The DOM said it was open; it was not on screen.
 *
 * So: below by default, flipped above only when below would run off the
 * bottom and there is actually room above. Measured once per opening, after
 * layout and before paint, so nothing is seen to jump.
 */
export function MentionList({
  items,
  index,
  onPick,
  onHover,
}: {
  items: Person[]
  index: number
  onPick: (person: Person) => void
  onHover: (index: number) => void
}) {
  const { tok } = useAppTheme()
  const ref = useRef<HTMLUListElement>(null)
  const [above, setAbove] = useState(false)

  useLayoutEffect(() => {
    const place = () => {
      const el = ref.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      // The *visual* viewport, not the layout one. When a phone opens its
      // keyboard, window.innerHeight does not change on iOS and often does
      // not on Android either - so a list placed below the box was being put
      // behind the keyboard, where it is as good as not there. visualViewport
      // is the part of the page the member can actually see.
      const view = window.visualViewport
      const room = (view?.height ?? window.innerHeight) + (view?.offsetTop ?? 0)
      // Only flip when there is a reason to and somewhere to flip to. Without
      // the second half this trades one off-screen list for another.
      if (!above && rect.bottom > room - 8 && rect.top > rect.height + 16) setAbove(true)
      if (above && rect.top < 8) setAbove(false)
    }
    place()
    // The keyboard opens after the field takes focus, which is after this
    // list has already decided where to go. Deciding again when the visible
    // area changes is what makes it follow.
    const view = window.visualViewport
    view?.addEventListener('resize', place)
    return () => view?.removeEventListener('resize', place)
  }, [items, above])

  return (
    <ul
      ref={ref}
      role="listbox"
      aria-label="People to mention"
      className={cn(
        'absolute start-0 z-50 max-h-64 w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-2xl p-1.5 shadow-xl',
        above ? 'bottom-full mb-2' : 'top-full mt-1',
        tok.card,
      )}
    >
      {items.map((person, at) => (
        <li key={person.user_id}>
          <button
            type="button"
            role="option"
            aria-selected={at === index}
            // onMouseDown, not onClick: a click fires after the input has
            // already lost focus, and losing focus is what closes this.
            onMouseDown={(event) => {
              event.preventDefault()
              onPick(person)
            }}
            onMouseEnter={() => onHover(at)}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-xl px-2 py-1.5 text-start',
              at === index && 'bg-text-hi/[0.07]',
            )}
          >
            <MemberAvatar
              handle={person.handle}
              displayName={person.display_name}
              avatarUrl={person.avatar_url}
              size={32}
            />
            {/* The name, like everywhere else; the handle on hover. Two people
                can share a name, and the photo beside it is what tells them
                apart here. */}
            <span className="min-w-0 flex-1" title={`@${person.handle}`}>
              <span className={cn('block truncate text-sm font-semibold', tok.text)}>
                {person.display_name}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
