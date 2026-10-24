import MentionLink from './MentionLink'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, CornerDownRight, Loader2, Send, SmilePlus, ThumbsUp } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { MentionList, useMentionSuggest } from './MentionSuggest'
import { ApiError, kaluta, type CommentNode } from '@/lib/api'
import { isLink, LINK_RE, splitTrailing } from '@/lib/linkify'
import MessageInput from '@/components/ui-kit/MessageInput'
import { cn } from '@/lib/utils'
import MemberAvatar from './MemberAvatar'
import { useMyProfile } from '@/hooks/useMyProfile'
import { useApi } from '@/hooks/useApi'
import StickerFace from './StickerFace'
import FloatingPanel from './FloatingPanel'
import { Link } from 'react-router'

const when = (iso: string) => {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

/**
 * Render @handle mentions as their own emphasis.
 *
 * Text nodes only — never markup — so a comment cannot inject anything by
 * writing HTML into a mention.
 */
/**
 * `names` maps a handle to the person's display name, as the server resolved
 * them. A mention is stored as the handle - that is what the author typed and
 * what has to survive an edit - but "@k_mbeki3" means nothing to a reader
 * next to "Kofi Mbeki", so the name is what gets drawn. The link still goes
 * to the handle, and a handle with no name resolved stays as it was rather
 * than being replaced by a guess.
 */
function withMentions(body: string, names: Record<string, string> = {}) {
  return (
    body
      // One split over both, so an @ inside an address - https://x.com/@someone,
      // or any path with a handle in it - is not pulled out of the link and
      // rendered as a mention of somebody who may not exist.
      .split(new RegExp(`${LINK_RE.source}|(@[a-z0-9_.]+)`, 'gi'))
      .map((part, index) => {
        if (part === undefined || part === '') return null
        // split() with more than one capturing group yields undefined for
        // every group that did not take part in the match, so each match
        // produces a hole beside it. Skipped rather than rendered.
        if (isLink(part)) {
          // Trailing punctuation belongs to the sentence, not the address.
          const { href, text, trailing } = splitTrailing(part)
          return (
            <span key={index}>
              <a
                href={href}
                target="_blank"
                // noreferrer as well as noopener: the destination has no
                // business learning which thread the reader came from.
                rel="noopener noreferrer nofollow"
                className="font-medium text-sky hover:underline"
                onClick={(event) => event.stopPropagation()}
              >
                {text}
              </a>
              {trailing}
            </span>
          )
        }
        if (part.startsWith('@')) {
          // A mention is a person, so it goes to them. It was styled as a link
          // and did nothing, which is the most annoying kind of not-a-link.
          // A handle may contain a dot but never ends with one, so a
          // trailing dot is the sentence's full stop and belongs outside the
          // link - exactly like the punctuation after an address.
          const trailing = part.match(/\.+$/)?.[0] ?? ''
          const token = trailing ? part.slice(0, -trailing.length) : part
          const handle = token.slice(1).toLowerCase()
          return (
            <span key={index}>
              <MentionLink handle={handle} name={names[handle]} className="text-gold-soft" stopPropagation />
              {trailing}
            </span>
          )
        }
        return part
      })
  )
}

interface Tree extends CommentNode {
  children: Tree[]
}

/** Group the flat list into the two levels the server allows. */
function toTree(items: CommentNode[]): Tree[] {
  const byId = new Map<string, Tree>()
  for (const item of items) byId.set(item.id, { ...item, children: [] })
  const roots: Tree[] = []
  for (const node of byId.values()) {
    const parent = node.parent_id ? byId.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  return roots
}

export default function Comments({
  postId,
  currentUserId,
  onCountChange,
  acceptedId = null,
  canAccept = false,
  onAccept,
}: {
  postId: string
  currentUserId: string
  onCountChange: (delta: number) => void
  /** The comment marked as the answer to a community discussion. */
  acceptedId?: string | null
  /** Whether this reader (the asker, or a moderator) may mark the answer. */
  canAccept?: boolean
  onAccept?: (commentId: string | null) => void
}) {
  const { tok } = useAppTheme()
  const [items, setItems] = useState<CommentNode[] | null>(null)
  // handle -> name for everyone mentioned anywhere in this thread, resolved
  // once by the server rather than once per comment by the browser.
  const [mentions, setMentions] = useState<Record<string, string>>({})
  const [maxDepth, setMaxDepth] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [body, setBody] = useState('')
  const [replyTo, setReplyTo] = useState<CommentNode | null>(null)
  const [sending, setSending] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // Finishing an @mention as it is typed. Handles are no longer shown
  // anywhere in the app, so nobody can be expected to recall one.
  const mention = useMentionSuggest(body, setBody, inputRef)

  const load = async () => {
    try {
      const page = await kaluta.posts.comments(postId)
      setItems(page.items)
      setMaxDepth(page.max_depth)
      setMentions(page.mentions ?? {})
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load comments')
    }
  }

  useEffect(() => {
    let cancelled = false
    kaluta.posts
      .comments(postId)
      .then((page) => {
        if (cancelled) return
        setItems(page.items)
        setMaxDepth(page.max_depth)
        setMentions(page.mentions ?? {})
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load comments')
      })
    return () => {
      cancelled = true
    }
  }, [postId])

  const tree = useMemo(() => toTree(items ?? []), [items])

  /**
   * Start a reply. Past the nesting cap the thread can no longer show *who* is
   * being answered by indentation, so the mention carries that instead — which
   * is why it is prefilled rather than left to the member to remember.
   */
  const startReply = (comment: CommentNode) => {
    setReplyTo(comment)
    const handle = comment.author?.handle
    // Includes replying to yourself: once the thread is flat, other readers
    // still need to see which message this answers.
    const needsMention = comment.depth >= maxDepth && Boolean(handle)
    setBody(needsMention ? `@${handle} ` : '')
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  // Called by the form's submit and by Enter in the box, which is not a
  // submit now that the box is a textarea.
  const submit = async (event?: React.FormEvent) => {
    event?.preventDefault()
    const text = body.trim()
    if (!text || sending) return
    setSending(true)
    setError(null)
    try {
      await kaluta.posts.comment(postId, text, replyTo?.id)
      setBody('')
      setReplyTo(null)
      onCountChange(1)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not post the comment')
    } finally {
      setSending(false)
    }
  }

  // Likes the viewer has changed since the list was fetched. Held apart from
  // `items` so a like does not mean re-fetching the thread, and so the button
  // answers immediately - the request is in flight while the state already
  // shows what the member just did.
  // The comment being rewritten, and its draft. One at a time: two open
  // editors in a thread is two ways to lose what you typed.
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null)
  const [removed, setRemoved] = useState<Set<string>>(new Set())
  const [busyId, setBusyId] = useState<string | null>(null)

  /** A comment can be fixed for a day; after that it stands. */
  const withinEditWindow = (createdAt: string) =>
    Date.now() - new Date(createdAt).getTime() < 24 * 60 * 60 * 1000

  const saveEdit = async () => {
    if (!editing || !editing.body.trim()) return
    setBusyId(editing.id)
    try {
      await kaluta.posts.editComment(editing.id, editing.body.trim())
      setEditing(null)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the change')
    } finally {
      setBusyId(null)
    }
  }

  const removeComment = async (id: string) => {
    setBusyId(id)
    try {
      await kaluta.posts.deleteComment(id)
      // Hidden immediately as well as refetched: the refetch is what makes it
      // true, this is what stops the comment sitting there looking undeleted
      // while the request is in flight.
      setRemoved((current) => new Set(current).add(id))
      onCountChange(-1)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the comment')
    } finally {
      setBusyId(null)
    }
  }

  const me = useMyProfile()
  interface Reacted {
    liked: boolean
    likes: number
    stickers: Record<string, number>
    my_sticker: string | null
  }
  const [likeEdits, setLikeEdits] = useState<Record<string, Reacted>>({})
  /** The comment whose sticker picker is open. */
  const [stickerFor, setStickerFor] = useState<string | null>(null)
  const stickerPacks = useApi(() => kaluta.messages.stickers(), [])
  /** A sticker from the catalogue by its id, for drawing one somebody chose. */
  const stickerById = (id: string) => {
    for (const pack of stickerPacks.data?.packs ?? []) {
      const found = pack.stickers.find((sticker) => sticker.id === id)
      if (found) return found
    }
    return null
  }

  // The server's answer to this reader's own tap wins over the page it was
  // loaded with: the page is a snapshot, the edit is what just happened.
  const likeState = (node: Tree): Reacted =>
    likeEdits[node.id] ?? {
      liked: node.liked ?? false,
      likes: node.likes ?? 0,
      stickers: node.stickers ?? {},
      my_sticker: node.my_sticker ?? null,
    }

  /**
   * Answer a comment: a plain like, or a sticker.
   *
   * One reaction per person, so choosing a sticker when you had already liked
   * it moves your one reaction rather than adding a second - the server
   * decides, and its answer replaces the guess below.
   */
  const react = async (node: Tree, stickerId: string | null = null) => {
    const before = likeState(node)
    const same = before.my_sticker === stickerId && before.liked
    // Optimistic, then corrected by the server's own count - which is the one
    // that knows about the other people reacting at the same moment.
    const guess: Reacted = {
      ...before,
      liked: !same,
      likes: before.likes + (before.liked ? (same ? -1 : 0) : 1),
      my_sticker: same ? null : stickerId,
    }
    setLikeEdits((current) => ({ ...current, [node.id]: guess }))
    try {
      const result = await kaluta.posts.likeComment(node.id, stickerId)
      setLikeEdits((current) => ({ ...current, [node.id]: result }))
    } catch {
      // Put it back. A reaction that silently did not happen is worse than one
      // that visibly did not.
      setLikeEdits((current) => ({ ...current, [node.id]: before }))
    }
  }

  const toggleLike = (node: Tree) => void react(node, null)

  const Row = ({ node }: { node: Tree }) => {
    if (removed.has(node.id)) return null
    const isMine = node.author_id === currentUserId
    const canEdit = isMine && withinEditWindow(node.created_at)
    return (
    <li>
      <div className="flex gap-2">
        <span className="mt-0.5">
          <MemberAvatar
            handle={node.author?.handle}
            displayName={node.author?.display_name}
            avatarUrl={node.author?.avatar_url}
            size={28}
          />
        </span>

        <div className="min-w-0 flex-1">
          {/* The bubble. Facebook's is a tight, strongly rounded block that
              wraps to the text rather than filling the row - so it is
              inline-block with a max width, not a full-width card. 18px of
              radius is what gives it its shape; the card radius read as a
              panel. */}
          {node.id === acceptedId && (
            <p className="mb-1 inline-flex items-center gap-1 rounded-full bg-emerald-400/15 px-2 py-0.5 text-[0.7rem] font-bold text-emerald-300">
              <Check size={11} aria-hidden="true" /> Answer
            </p>
          )}
          <div
            className={cn(
              'max-w-full rounded-[18px] px-3 py-2',
              // Wrapped to the text when read; the whole row when edited, so
              // the text being changed has room instead of a narrow column.
              editing?.id === node.id ? 'block w-full' : 'inline-block',
              tok.subtleBg,
              node.id === acceptedId && 'ring-1 ring-emerald-400/50',
            )}
          >
            <p className="text-[0.8125rem] font-semibold leading-tight">
              {node.author?.handle ? (
                <Link to={`/u/${node.author.handle}`} className={cn(tok.text, 'hover:underline')}>
                  {node.author_id === currentUserId ? 'You' : node.author.display_name}
                </Link>
              ) : (
                <span className={tok.text}>{node.author_id.slice(0, 12)}</span>
              )}
              {/* Beyond the cap the reply is no longer indented under its
                  target, so name it explicitly. */}
              {node.reply_to_user && node.depth >= maxDepth && (
                <span className={cn('ms-1.5 inline-flex items-center gap-0.5 font-normal', tok.low)}>
                  <CornerDownRight size={10} aria-hidden="true" />
                  replying to
                  {/* Their name. The handle was what the server happened to
                      send first, not what a reader is looking for. */}
                  <span className="font-semibold text-gold-soft">
                    {node.reply_to_user.display_name || 'Member'}
                  </span>
                </span>
              )}
            </p>
            {/* tok.text, not tok.mid: in a bubble the comment is the content,
                and dimming it made every reply read as secondary to the name
                above it. The name carries the weight instead. */}
            {editing?.id === node.id ? (
              /* Edited in place, inside its own bubble: a dialog for one line
                 of text loses the thread it belongs to. */
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  void saveEdit()
                }}
                className="mt-1"
              >
                <MessageInput
                  autoFocus
                  value={editing.body}
                  onChange={(e) => setEditing({ id: node.id, body: e.target.value })}
                  onSubmit={() => void saveEdit()}
                  onKeyDown={(e) => e.key === 'Escape' && setEditing(null)}
                  aria-label="Edit comment"
                  className={cn('w-full rounded-lg px-2 py-1 text-[0.9375rem]', tok.input, tok.text)}
                />
                <div className="mt-1 flex items-center gap-2 text-[0.72rem]">
                  <button type="submit" disabled={busyId === node.id} className="font-semibold text-gold-soft disabled:opacity-50">
                    Save
                  </button>
                  <button type="button" onClick={() => setEditing(null)} className={tok.low}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <p className={cn('mt-0.5 whitespace-pre-wrap text-[0.9375rem] leading-[1.35]', tok.text)}>
                {withMentions(node.body, mentions)}
                {node.edited_at && (
                  /* Said plainly, because a reply underneath may be answering
                     words that are no longer there. */
                  <span className={cn('ms-1.5 text-[0.72rem]', tok.low)}>(edited)</span>
                )}
              </p>
            )}
          </div>

          {/* Like, Reply, then the time - the order Facebook uses, and the
              order that matters: the two things you can do, then the one thing
              you can only read. Liked is gold and stays bold, so the state is
              legible without a second control. */}
          <div className="mt-0.5 flex items-center gap-3 ps-3">
            <button
              type="button"
              onClick={() => toggleLike(node)}
              aria-pressed={likeState(node).liked}
              className={cn(
                'text-[0.75rem] font-semibold hover:underline',
                likeState(node).liked ? 'text-gold-soft' : tok.low,
              )}
            >
              Like
            </button>
            <button
              type="button"
              id={`cmt-react-${node.id}`}
              onClick={() => setStickerFor(stickerFor === node.id ? null : node.id)}
              aria-haspopup="dialog"
              aria-label="React with a sticker"
              className={cn('rounded-full p-0.5 hover:text-gold-soft', tok.low)}
            >
              <SmilePlus size={13} aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => startReply(node)}
              className={cn('text-[0.75rem] font-semibold hover:underline', tok.low)}
            >
              Reply
            </button>
            <span className={cn('text-[0.75rem]', tok.low)}>{when(node.created_at)}</span>
            {/* Only the author's own, and Edit only while it can still be
                edited - a button that answers "too late" is worse than no
                button. Delete has no window: taking back something you regret
                must always be possible. */}
            {canEdit && editing?.id !== node.id && (
              <button
                type="button"
                onClick={() => setEditing({ id: node.id, body: node.body })}
                className={cn('text-[0.75rem] font-semibold hover:underline', tok.low)}
              >
                Edit
              </button>
            )}
            {canAccept && onAccept && (
              <button
                type="button"
                onClick={() => onAccept(node.id === acceptedId ? null : node.id)}
                className={cn('text-[0.75rem] font-semibold hover:underline', tok.low)}
              >
                {node.id === acceptedId ? 'Unmark answer' : 'Mark as answer'}
              </button>
            )}
            {isMine && (
              <button
                type="button"
                onClick={() => void removeComment(node.id)}
                disabled={busyId === node.id}
                className={cn('text-[0.75rem] font-semibold hover:underline disabled:opacity-50', tok.low)}
              >
                Delete
              </button>
            )}
            {/* The counts sit on the row, not inside the bubble: a bubble
                that changes size when somebody reacts makes the thread jump.
                Plain likes keep the thumb; stickers are shown as themselves,
                which is the whole point of sending one. */}
            {(() => {
              const state = likeState(node)
              const stickerTotal = Object.values(state.stickers).reduce((sum, n) => sum + n, 0)
              const plainLikes = state.likes - stickerTotal
              return (
                <>
                  {plainLikes > 0 && (
                    <span className={cn('inline-flex items-center gap-1 text-[0.75rem]', tok.low)}>
                      <ThumbsUp size={11} className="text-gold-soft" aria-hidden="true" />
                      {plainLikes}
                    </span>
                  )}
                  {Object.entries(state.stickers).map(([id, count]) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => void react(node, id)}
                      aria-pressed={state.my_sticker === id}
                      aria-label={`${count} reacted with this sticker`}
                      className={cn(
                        'inline-flex items-center gap-1 rounded-full border px-1.5 text-[0.75rem]',
                        state.my_sticker === id
                          ? 'border-gold/50 bg-gold/15'
                          : 'border-transparent hover:border-[var(--cloud-border)]',
                        tok.low,
                      )}
                    >
                      {/* A sticker withdrawn from the catalogue still has
                          rows pointing at it; its id is shown rather than a
                          blank, so the count is never silently wrong. */}
                      {(() => {
                        const sticker = stickerById(id)
                        return sticker ? (
                          <StickerFace sticker={sticker} className="text-[15px]" imageClassName="h-4 w-4" />
                        ) : (
                          <span aria-hidden="true">•</span>
                        )
                      })()}
                      {count}
                    </button>
                  ))}
                </>
              )
            })()}
          </div>

          {stickerFor === node.id && (
            <FloatingPanel
              anchor={`cmt-react-${node.id}`}
              label="Choose a sticker"
              onClose={() => setStickerFor(null)}
            >
              <div className="flex max-w-[260px] flex-wrap gap-0.5">
                {(stickerPacks.data?.packs ?? []).flatMap((pack) =>
                  pack.stickers.map((sticker) => (
                    <button
                      key={sticker.id}
                      type="button"
                      onClick={() => {
                        void react(node, sticker.id)
                        setStickerFor(null)
                      }}
                      aria-label={sticker.label}
                      className="flex h-9 w-9 items-center justify-center rounded-full hover:scale-110 hover:bg-white/10"
                    >
                      <StickerFace sticker={sticker} className="text-[22px]" imageClassName="h-6 w-6" />
                    </button>
                  )),
                )}
              </div>
            </FloatingPanel>
          )}

          {/* Replies indent by margin alone. The rule down the left read as a
              quote block rather than as a thread. */}
          {node.children.length > 0 && (
            <ul className="mt-2 space-y-2 ps-5">
              {node.children.map((child) => (
                <Row key={child.id} node={child} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </li>
    )
  }

  return (
    <div className={cn('mt-3 border-t pt-3', tok.divider, 'border-t-current/10')}>
      {/* A skeleton rather than the word "Loading".
          The shape of what is coming is more use than the news that something
          is: the thread does not jump when it arrives, because the space is
          already the right size, and a spinner on a short thread flashed for
          long enough to notice and not long enough to read. */}
      {items === null && !error && (
        <ul className="space-y-3" aria-hidden="true">
          {[0, 1, 2].map((row) => (
            <li key={row} className="flex gap-2">
              <span className={cn('h-7 w-7 shrink-0 animate-pulse rounded-full', tok.subtleBg)} />
              <span className="min-w-0 flex-1">
                <span
                  className={cn('block animate-pulse rounded-[18px]', tok.subtleBg)}
                  // Three different widths, so it reads as comments rather
                  // than as a loading bar repeated three times.
                  style={{ height: 46, width: ['78%', '58%', '68%'][row] }}
                />
                <span className={cn('mt-1 block h-2.5 w-24 animate-pulse rounded', tok.subtleBg)} />
              </span>
            </li>
          ))}
          <li className="sr-only" aria-live="polite">
            Loading comments
          </li>
        </ul>
      )}

      {items && items.length === 0 && (
        <p className={cn('py-1 text-xs', tok.low)}>No replies yet. Start the conversation.</p>
      )}

      {tree.length > 0 && <ul className="max-h-[420px] space-y-3 overflow-y-auto pe-1">{tree.map((node) => <Row key={node.id} node={node} />)}</ul>}

      <form onSubmit={submit} className="mt-3">
        {replyTo && (
          <p className={cn('mb-1.5 flex items-center gap-1.5 ps-1 text-[0.68rem]', tok.low)}>
            <CornerDownRight size={11} aria-hidden="true" />
            Replying to{' '}
            <span className="font-semibold text-gold-soft">
              {replyTo.author?.display_name ?? replyTo.author_id.slice(0, 12)}
            </span>
            <button
              type="button"
              onClick={() => {
                setReplyTo(null)
                setBody('')
              }}
              className="ms-1 underline hover:text-text-hi"
            >
              cancel
            </button>
          </p>
        )}

        {/* The viewer's own face beside the box, as every comment composer
            has. It says whose comment this will be, which matters on a shared
            device and on a page where you may be reading as somebody else. */}
        <div className="flex items-center gap-2">
          <span className="shrink-0">
            <MemberAvatar
              handle={me?.handle}
              displayName={me?.display_name}
              avatarUrl={me?.avatar_url}
              size={32}
            />
          </span>
          <div className={cn('relative flex min-w-0 flex-1 items-center rounded-full', tok.input)}>
            {/* Above the box rather than below it: a comment composer sits at
                the bottom of a thread, and a list dropped underneath would be
                off the screen or under the keyboard. */}
            {mention.open && (
              <MentionList
                items={mention.items}
                index={mention.index}
                onPick={mention.pick}
                onHover={mention.setIndex}
              />
            )}
            {/* A textarea, so a comment can have a second paragraph. Enter
                sends on a keyboard and breaks the line on a phone, where
                there is no Shift to hold beside it. */}
            <MessageInput
              ref={inputRef}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              onSubmit={() => void submit()}
              onKeyDown={(event) => {
                // The list owns the arrows and Enter while it is open: Enter
                // in this box sends the comment, and choosing a name is not
                // sending it.
                if (mention.onKeyDown(event)) event.preventDefault()
              }}
              onKeyUp={mention.sync}
              onClick={mention.sync}
              placeholder={replyTo ? 'Write a reply…' : 'Write a comment…'}
              aria-label={replyTo ? 'Write a reply' : 'Write a comment'}
              className={cn(
                'min-w-0 flex-1 bg-transparent px-4 py-2 text-[0.9375rem] focus:outline-none',
                tok.text,
              )}
            />
            {/* Enter sends, so the button is only there while there is
                something to send - which is how it reads as confirmation
                rather than as the only way out. */}
            {(body.trim() || sending) && (
              <button
                type="submit"
                disabled={!body.trim() || sending}
                aria-label="Send"
                className="shrink-0 rounded-full px-3 py-2 text-gold-soft disabled:opacity-40"
              >
                {sending ? (
                  <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                ) : (
                  <Send size={15} aria-hidden="true" />
                )}
              </button>
            )}
          </div>
        </div>
      </form>

      {error && (
        <p role="alert" className="mt-2 text-xs text-red-300">
          {error}
        </p>
      )}
    </div>
  )
}
