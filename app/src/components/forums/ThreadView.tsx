import { useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  BadgeInfo,
  Bell,
  BellOff,
  Bookmark,
  BookmarkCheck,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  EyeOff,
  Flag,
  Languages,
  Lock,
  MessageSquareReply,
  MoveRight,
  Merge,
  Paperclip,
  Pencil,
  Pin,
  RotateCcw,
  Send,
  Share2,
  Sparkles,
  Tag,
  Unlock,
  X,
} from 'lucide-react'
import MemberAvatar from '@/components/social/MemberAvatar'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { ApiError, kaluta, type Forum, type ForumAttachment, type ThreadDetail } from '@/lib/api'
import { copyLink, siteOrigin } from '@/lib/share'
import { cn } from '@/lib/utils'
import { ContributorDialog, ReportDialog, Sheet } from './ForumDialogs'
import RichText from './RichText'
import { KIND, ago, countryName, field } from './forumText'

type Reply = ThreadDetail['replies'][number]

function Attachments({ items }: { items?: ForumAttachment[] }) {
  if (!items?.length) return null
  return (
    <ul className="flex flex-wrap gap-2">
      {items.map((a) =>
        a.kind === 'image' && a.url ? (
          <li key={a.id}>
            <a href={a.url} target="_blank" rel="noreferrer">
              <img src={a.url} alt={a.name ?? ''} loading="lazy" className="max-h-48 rounded-xl object-cover" />
            </a>
          </li>
        ) : a.kind === 'video' && a.url ? (
          <li key={a.id} className="w-full max-w-md">
            <video src={a.url} controls preload="metadata" className="w-full rounded-xl" />
          </li>
        ) : (
          <li key={a.id}>
            <a href={a.url ?? '#'} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs text-text-mid hover:text-text-hi">
              <Paperclip size={12} aria-hidden="true" />
              {a.name ?? 'File'}
            </a>
          </li>
        ),
      )}
    </ul>
  )
}

function Badge({ tone, children }: { tone: 'gold' | 'green' | 'sky' | 'amber' | 'grey'; children: React.ReactNode }) {
  const tones = {
    gold: 'border-gold/40 bg-gold/10 text-gold',
    green: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
    sky: 'border-sky-400/30 bg-sky-500/10 text-sky',
    amber: 'border-amber-400/30 bg-amber-500/10 text-amber-200',
    grey: 'border-white/10 bg-white/5 text-text-mid',
  }
  return <span className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold', tones[tone])}>{children}</span>
}

/**
 * One thread, read and acted on.
 *
 * The question first, then the answer its asker accepted (said to be the
 * asker's choice, not a guarantee), then the whole discussion with the
 * comments under the answer they comment on. A long thread offers a jump to
 * its latest replies.
 */
export default function ThreadView({
  thread,
  forums,
  userId,
  canTranslate,
  locale,
  onBack,
  onReload,
  onOpenThread,
  backLabel,
}: {
  thread: ThreadDetail
  forums: Forum[]
  userId: string | null
  canTranslate: boolean
  locale: string
  onBack: () => void
  onReload: () => Promise<void>
  onOpenThread: (id: string) => void
  backLabel: string
}) {
  const [reply, setReply] = useState('')
  const [replyTo, setReplyTo] = useState<Reply | null>(null)
  const [files, setFiles] = useState<Array<{ id: string; name: string }>>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ kind: 'thread' | 'reply'; id: string; text: string } | null>(null)
  const [reporting, setReporting] = useState<{ kind: 'thread' | 'reply'; id: string } | null>(null)
  const [profile, setProfile] = useState<string | null>(null)
  const [modAction, setModAction] = useState<null | 'hide' | 'move' | 'merge' | 'lock' | { reply: string }>(null)
  const [reason, setReason] = useState('')
  const [target, setTarget] = useState('')
  const [audienceWarning, setAudienceWarning] = useState(false)
  const [reopening, setReopening] = useState(false)
  const [accepting, setAccepting] = useState<string | null>(null)
  const [translations, setTranslations] = useState<Record<string, string>>({})
  const [summary, setSummary] = useState<Awaited<ReturnType<typeof kaluta.forums.summary>> | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const kind = thread.kind ?? 'question'

  // Answers in the server's order (the accepted one first), and under each the
  // comments on it and on its comments.
  const { answers, comments } = useMemo(() => {
    const byId = new Map(thread.replies.map((r) => [r.id, r]))
    const rootOf = (r: Reply): string => {
      let current = r
      for (let i = 0; i < 20 && current.parent_id && byId.has(current.parent_id); i++) current = byId.get(current.parent_id)!
      return current.id
    }
    const answers = thread.replies.filter((r) => !r.parent_id || !byId.has(r.parent_id))
    const comments = new Map<string, Reply[]>()
    for (const r of thread.replies) {
      if (r.parent_id && byId.has(r.parent_id)) {
        const root = rootOf(r)
        comments.set(root, [...(comments.get(root) ?? []), r].sort((a, b) => a.created_at.localeCompare(b.created_at)))
      }
    }
    return { answers, comments }
  }, [thread.replies])
  const accepted = answers.find((r) => r.accepted_answer)
  const latest = [...thread.replies].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]

  const act = async (key: string, action: () => Promise<unknown>, said?: string) => {
    setBusy(key)
    setNote(null)
    try {
      await action()
      if (said) setNote(said)
      await onReload()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'That did not go through')
    } finally {
      setBusy(null)
    }
  }

  const jump = (id: string) => document.getElementById(`reply-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })

  const translate = async (id: string, text: string) => {
    try {
      const out = await kaluta.ai.translate(text, locale, undefined, true)
      setTranslations((t) => ({ ...t, [id]: out.translated }))
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Translation is not available right now')
    }
  }

  const share = async () => {
    const url = new URL(`/forums?thread=${thread.id}`, siteOrigin()).toString()
    const result = await copyLink(url)
    setNote(result === 'copied' ? 'Link copied. It opens the thread only for people who can already read this forum.' : `Copy this link: ${url}`)
  }

  const shareInCommunity = () =>
    act(
      'share',
      () =>
        kaluta.posts.create({
          body: `${thread.title}\n${new URL(`/forums?thread=${thread.id}`, siteOrigin()).toString()}`,
          visibility: 'community',
          community_id: thread.forum!.community_id!,
        } as Parameters<typeof kaluta.posts.create>[0]),
      'Shared in the community. Its members who cannot read this forum will not be able to open it.',
    )

  const send = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!reply.trim()) return
    await act('reply', async () => {
      await kaluta.forums.reply(thread.id, reply.trim(), { parentId: replyTo?.id, attachments: files.map((f) => f.id) })
      setReply('')
      setReplyTo(null)
      setFiles([])
    })
  }

  const author = (r: { author_id: string; author?: { display_name?: string; handle?: string; avatar_url?: string | null } | null }) => (
    <button type="button" onClick={() => setProfile(r.author_id)} className="flex min-w-0 items-center gap-2 text-start" aria-label={`About ${r.author?.display_name ?? 'this member'}`}>
      <MemberAvatar handle={r.author?.handle} displayName={r.author?.display_name} avatarUrl={r.author?.avatar_url} size={30} />
      <span className="truncate text-xs font-semibold text-text-hi hover:underline">{r.author?.display_name || 'Member'}</span>
    </button>
  )

  const replyCard = (r: Reply, nested = false) => (
    <li key={r.id} id={`reply-${r.id}`} className={cn(nested ? 'border-s-2 border-white/10 ps-3' : 'cloud-card p-4', 'space-y-2', r.accepted_answer && !nested && 'border-emerald-500/40 bg-emerald-500/5')}>
      <div className="flex flex-wrap items-center gap-2">
        {author(r)}
        <span className="caption text-text-low">{ago(r.created_at)}</span>
        {r.edited_at && <span className="caption text-text-low">· edited</span>}
        {r.is_author && <Badge tone="grey">Asked it</Badge>}
        {r.is_expert && <Badge tone="sky">Session expert</Badge>}
        {r.commercial && <Badge tone="amber">Commercial</Badge>}
        {r.accepted_answer && (
          <Badge tone="green">
            <CheckCircle2 size={11} aria-hidden="true" /> Accepted by the asker
          </Badge>
        )}
      </div>
      {r.author_background && <p className="text-[11px] text-text-low">Says about themself: {r.author_background} (not verified)</p>}
      <RichText text={r.body} />
      {translations[r.id] && (
        <div className="rounded-xl border border-sky-400/20 bg-sky-500/5 p-2">
          <p className="text-[11px] font-semibold text-sky">Translated by Kinjy’s AI service. The original is above.</p>
          <RichText text={translations[r.id]} />
        </div>
      )}
      <Attachments items={r.attachments} />
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={() => void act(`vote-${r.id}`, () => kaluta.forums.upvote(r.id))}
          disabled={!userId || r.author_id === userId || thread.locked || busy === `vote-${r.id}`}
          aria-pressed={Boolean(r.voted_by_me)}
          aria-label={`Helpful: ${r.upvotes} ${r.upvotes === 1 ? 'vote' : 'votes'}`}
          title={r.author_id === userId ? 'You cannot vote on your own reply' : 'Helpful'}
          className={cn('inline-flex min-h-[36px] items-center gap-1 rounded-xl border px-2.5 text-xs font-semibold disabled:opacity-50', r.voted_by_me ? 'border-gold/60 bg-gold/15 text-gold' : 'border-white/10 text-text-mid hover:text-text-hi')}
        >
          <ArrowUp size={13} aria-hidden="true" /> {r.upvotes}
        </button>
        {thread.can_accept && !nested && !r.accepted_answer && (
          <button type="button" onClick={() => (accepted ? setAccepting(r.id) : void act(`acc-${r.id}`, () => kaluta.forums.acceptAnswer(r.id)))} className="inline-flex min-h-[36px] items-center gap-1 rounded-xl border border-emerald-500/30 px-2.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-500/10">
            <CheckCircle2 size={13} aria-hidden="true" /> Accept as the answer
          </button>
        )}
        {thread.can_reply && (
          <button
            type="button"
            onClick={() => {
              setReplyTo(r)
              composerRef.current?.focus()
            }}
            className="inline-flex min-h-[36px] items-center gap-1 rounded-xl px-2 text-xs font-semibold text-text-mid hover:text-text-hi"
          >
            <MessageSquareReply size={13} aria-hidden="true" /> {nested ? 'Reply' : 'Comment'}
          </button>
        )}
        {r.can_edit && (
          <button type="button" onClick={() => setEditing({ kind: 'reply', id: r.id, text: r.body })} className="inline-flex min-h-[36px] items-center gap-1 rounded-xl px-2 text-xs text-text-mid hover:text-text-hi">
            <Pencil size={12} aria-hidden="true" /> Edit
          </button>
        )}
        {canTranslate && !translations[r.id] && (
          <button type="button" onClick={() => void translate(r.id, r.body)} className="inline-flex min-h-[36px] items-center gap-1 rounded-xl px-2 text-xs text-text-mid hover:text-text-hi">
            <Languages size={12} aria-hidden="true" /> Translate
          </button>
        )}
        {userId && r.author_id !== userId && (
          <button type="button" onClick={() => setReporting({ kind: 'reply', id: r.id })} className="inline-flex min-h-[36px] items-center gap-1 rounded-xl px-2 text-xs text-text-low hover:text-red-300" aria-label="Report this reply">
            <Flag size={12} aria-hidden="true" />
          </button>
        )}
        {thread.can_moderate && (
          <button type="button" onClick={() => setModAction({ reply: r.id })} className="inline-flex min-h-[36px] items-center gap-1 rounded-xl px-2 text-xs text-text-low hover:text-red-300">
            <EyeOff size={12} aria-hidden="true" /> Hide
          </button>
        )}
      </div>
      {!nested && (comments.get(r.id)?.length ?? 0) > 0 && <ul className="mt-2 space-y-3">{comments.get(r.id)!.map((c) => replyCard(c, true))}</ul>}
    </li>
  )

  const otherForums = forums.filter((f) => f.id !== thread.forum_id)

  return (
    <article className="space-y-4">
      <div className="cloud-card space-y-4 p-4 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/8 pb-3">
          <button type="button" onClick={onBack} className="inline-flex min-h-[44px] items-center gap-2 rounded-xl px-2.5 text-xs font-semibold text-gold hover:bg-gold/10">
            <ArrowLeft size={16} aria-hidden="true" />
            {backLabel}
          </button>
          <div className="flex flex-wrap items-center gap-1">
            {userId && (
              <>
                <button type="button" onClick={() => void act('follow', () => kaluta.forums.followThread(thread.id, { following: !thread.following }), thread.following ? 'You no longer follow this thread.' : 'You will be told about new replies.')} aria-pressed={thread.following} className="inline-flex min-h-[40px] items-center gap-1 rounded-xl px-2.5 text-xs font-semibold text-text-mid hover:text-text-hi">
                  {thread.following ? <BellOff size={14} aria-hidden="true" /> : <Bell size={14} aria-hidden="true" />}
                  {thread.following ? 'Unfollow' : 'Follow'}
                </button>
                <button type="button" onClick={() => void act('save', () => kaluta.forums.followThread(thread.id, { saved: !thread.saved }))} aria-pressed={thread.saved} className="inline-flex min-h-[40px] items-center gap-1 rounded-xl px-2.5 text-xs font-semibold text-text-mid hover:text-text-hi">
                  {thread.saved ? <BookmarkCheck size={14} aria-hidden="true" /> : <Bookmark size={14} aria-hidden="true" />}
                  {thread.saved ? 'Saved' : 'Save'}
                </button>
              </>
            )}
            <button type="button" onClick={() => void share()} className="inline-flex min-h-[40px] items-center gap-1 rounded-xl px-2.5 text-xs font-semibold text-text-mid hover:text-text-hi">
              <Share2 size={14} aria-hidden="true" /> Share
            </button>
            {userId && thread.forum?.community_id && (
              <button type="button" disabled={busy === 'share'} onClick={() => void shareInCommunity()} className="inline-flex min-h-[40px] items-center gap-1 rounded-xl px-2.5 text-xs font-semibold text-text-mid hover:text-text-hi">
                Share in the community
              </button>
            )}
            <button
              type="button"
              onClick={() => kaluta.forums.summary(thread.id).then(setSummary).catch((err) => setNote(err instanceof ApiError ? err.message : 'The summariser is unavailable'))}
              className="inline-flex min-h-[40px] items-center gap-1 rounded-xl px-2.5 text-xs font-semibold text-sky hover:bg-sky-500/10"
            >
              <Sparkles size={14} aria-hidden="true" /> AI summary
            </button>
            {userId && thread.author_id !== userId && (
              <button type="button" onClick={() => setReporting({ kind: 'thread', id: thread.id })} aria-label="Report this thread" className="inline-flex min-h-[40px] items-center rounded-xl px-2 text-text-low hover:text-red-300">
                <Flag size={14} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        {thread.can_moderate && (
          <div className="flex flex-wrap gap-1.5 rounded-xl border border-white/10 bg-white/[0.03] p-2" aria-label="Moderator tools">
            <span className="self-center px-1 text-[11px] font-semibold text-text-low">Moderator</span>
            <button type="button" onClick={() => void act('pin', () => kaluta.forums.moderateThread(thread.id, thread.pinned ? 'unpin' : 'pin'))} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-mid hover:bg-white/8">
              <Pin size={12} aria-hidden="true" /> {thread.pinned ? 'Unpin' : 'Pin'}
            </button>
            <button type="button" onClick={() => (thread.locked ? void act('lock', () => kaluta.forums.moderateThread(thread.id, 'unlock')) : setModAction('lock'))} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-mid hover:bg-white/8">
              {thread.locked ? <Unlock size={12} aria-hidden="true" /> : <Lock size={12} aria-hidden="true" />} {thread.locked ? 'Unlock' : 'Lock'}
            </button>
            <button type="button" onClick={() => setModAction('move')} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-mid hover:bg-white/8">
              <MoveRight size={12} aria-hidden="true" /> Move
            </button>
            <button type="button" onClick={() => setModAction('merge')} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-mid hover:bg-white/8">
              <Merge size={12} aria-hidden="true" /> Merge into…
            </button>
            <button type="button" onClick={() => void act('com', () => kaluta.forums.moderateThread(thread.id, thread.commercial ? 'unmark_commercial' : 'mark_commercial'))} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-mid hover:bg-white/8">
              {thread.commercial ? 'Not commercial' : 'Mark commercial'}
            </button>
            <button type="button" onClick={() => void act('draft', () => kaluta.forums.article.aiDraft(thread.id), 'An AI draft article was made from this discussion. Review it in the Knowledge tab before publishing.')} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-sky hover:bg-white/8">
              <BookOpen size={12} aria-hidden="true" /> AI draft for Knowledge
            </button>
            <button type="button" onClick={() => setModAction('hide')} className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-red-300 hover:bg-red-500/10">
              <EyeOff size={12} aria-hidden="true" /> Hide
            </button>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="gold">{KIND[kind].label}</Badge>
          {kind === 'question' && (thread.solved ? <Badge tone="green"><CheckCircle2 size={11} aria-hidden="true" /> Solved</Badge> : thread.replies.length === 0 ? <Badge tone="grey">Unanswered</Badge> : <Badge tone="grey">Open</Badge>)}
          {thread.reopened_at && !thread.solved && <Badge tone="amber">Reopened by the asker</Badge>}
          {thread.pinned && <Badge tone="gold"><Pin size={11} aria-hidden="true" /> Pinned</Badge>}
          {thread.locked && <Badge tone="grey"><Lock size={11} aria-hidden="true" /> Locked</Badge>}
          {thread.commercial && <Badge tone="amber">Commercial</Badge>}
          {thread.session && <Badge tone="sky">Expert session: {thread.session.title}</Badge>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {author(thread)}
          <span className="caption text-text-low">{ago(thread.created_at)}</span>
          {thread.edited_at && <span className="caption text-text-low">· edited {ago(thread.edited_at)}</span>}
        </div>
        {thread.author_background && <p className="text-[11px] text-text-low">Says about themself: {thread.author_background} (not verified)</p>}
        <h1 className="text-lg font-bold leading-tight text-text-hi sm:text-xl">{thread.title}</h1>
        <RichText text={thread.body} />
        {translations[thread.id] && (
          <div className="rounded-xl border border-sky-400/20 bg-sky-500/5 p-3">
            <p className="text-[11px] font-semibold text-sky">Translated by Kinjy’s AI service. The original is above.</p>
            <RichText text={translations[thread.id]} />
          </div>
        )}
        <Attachments items={thread.attachments} />
        {(thread.tags?.length || thread.country || thread.region) && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            {thread.tags?.map((t) => (
              <button key={t} type="button" onClick={() => userId && void kaluta.forums.followTopic(t).then(() => setNote(`You follow #${t}: new threads on it are sent to you.`))} title="Follow this topic" className="inline-flex items-center gap-1 rounded-full bg-text-hi/[0.07] px-2 py-0.5 text-text-mid hover:text-gold-soft">
                <Tag size={10} aria-hidden="true" />
                {t}
              </button>
            ))}
            {(thread.country || thread.region) && <span className="text-text-low">About: {[thread.region, countryName(thread.country)].filter(Boolean).join(', ')}</span>}
          </div>
        )}
        {thread.event && (
          <p className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-xs text-text-mid">
            <CalendarDays size={13} aria-hidden="true" />
            Event: <span className="font-semibold text-text-hi">{thread.event.title}</span> · {new Date(thread.event.starts_at).toLocaleString()} {thread.event.status === 'cancelled' && '· cancelled'}
          </p>
        )}
        {thread.poll && (
          <div className="rounded-xl border border-white/10 p-3">
            <p className="text-sm font-semibold text-text-hi">{thread.poll.question}</p>
            <ul className="mt-2 space-y-1.5">
              {thread.poll.options.map((o) => {
                const share = thread.poll!.voters ? Math.round((o.votes / thread.poll!.voters) * 100) : 0
                const mine = thread.poll!.my_votes.includes(o.index)
                return (
                  <li key={o.index}>
                    <button
                      type="button"
                      disabled={!userId || thread.poll!.closed || thread.locked}
                      onClick={() => {
                        const next = thread.poll!.multiple ? (mine ? thread.poll!.my_votes.filter((x) => x !== o.index) : [...thread.poll!.my_votes, o.index]) : mine ? [] : [o.index]
                        void act('poll', () => kaluta.forums.votePoll(thread.id, next))
                      }}
                      aria-pressed={mine}
                      className={cn('relative w-full overflow-hidden rounded-xl border px-3 py-1.5 text-start text-sm', mine ? 'border-gold/60' : 'border-white/10')}
                    >
                      <span aria-hidden="true" className="absolute inset-y-0 start-0 bg-gold/15" style={{ width: `${share}%` }} />
                      <span className="relative flex justify-between gap-2 text-text-hi">
                        {o.text}
                        <span className="text-xs text-text-mid">{o.votes}</span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
            <p className="mt-1 text-xs text-text-low">{thread.poll.voters} voted{thread.poll.closed ? ' · closed' : ''}</p>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          {thread.can_edit && (
            <button type="button" onClick={() => setEditing({ kind: 'thread', id: thread.id, text: thread.body })} className="inline-flex items-center gap-1 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-semibold text-text-mid hover:text-text-hi">
              <Pencil size={12} aria-hidden="true" /> Edit or add details
            </button>
          )}
          {thread.can_reopen && (
            <button type="button" onClick={() => setReopening(true)} className="inline-flex items-center gap-1 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-semibold text-text-mid hover:text-text-hi">
              <RotateCcw size={12} aria-hidden="true" /> Reopen: the answer no longer solves it
            </button>
          )}
          {canTranslate && !translations[thread.id] && (
            <button type="button" onClick={() => void translate(thread.id, `${thread.title}\n\n${thread.body}`)} className="inline-flex items-center gap-1 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-semibold text-text-mid hover:text-text-hi">
              <Languages size={12} aria-hidden="true" /> Translate
            </button>
          )}
        </div>
        {summary && (
          <div className="rounded-2xl border border-sky-400/25 bg-sky-500/10 p-4">
            {summary.summarised ? (
              <>
                <p className="flex items-center gap-1.5 text-xs font-bold text-sky"><Sparkles size={13} aria-hidden="true" /> AI-generated summary</p>
                <p className="mt-1 text-sm text-text-hi">{summary.summary}</p>
                <p className="caption mt-1 text-sky/80">Written by a machine from {summary.replies_counted} replies, not checked by anyone. {summary.mock && 'Test provider: not a real summary.'}</p>
              </>
            ) : (
              <p className="text-xs text-sky/90">{summary.reason}</p>
            )}
          </div>
        )}
        {note && <p role="status" className="text-xs text-text-mid">{note}</p>}
      </div>

      {(accepted || thread.replies.length > 8) && (
        <div className="flex flex-wrap gap-2">
          {accepted && (
            <button type="button" onClick={() => jump(accepted.id)} className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold text-emerald-300">
              <CheckCircle2 size={13} aria-hidden="true" /> Go to the accepted answer
            </button>
          )}
          {thread.replies.length > 8 && latest && (
            <button type="button" onClick={() => jump(latest.id)} className="inline-flex items-center gap-1.5 rounded-full border border-white/10 px-3 py-1.5 text-xs font-semibold text-text-mid">
              <ArrowDown size={13} aria-hidden="true" /> Latest reply
            </button>
          )}
        </div>
      )}
      {accepted && (
        <p className="flex items-start gap-1.5 px-1 text-[11px] text-text-low">
          <BadgeInfo size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          The accepted answer is the one the asker chose. It is not checked by Kinjy and may not be right for you.
        </p>
      )}

      <section aria-label="Replies" className="space-y-3">
        <h2 className="px-1 text-sm font-bold text-text-hi">
          {answers.length} {kind === 'question' ? (answers.length === 1 ? 'answer' : 'answers') : answers.length === 1 ? 'reply' : 'replies'}
        </h2>
        {thread.replies.length === 0 && <div className="cloud-card p-6 text-center text-sm text-text-low">{kind === 'question' ? 'No answer yet.' : 'No reply yet.'}</div>}
        <ul className="space-y-3">{answers.map((r) => replyCard(r))}</ul>
      </section>

      <div className="cloud-card sticky bottom-4 z-10 p-3 shadow-xl backdrop-blur-md sm:p-4">
        {thread.locked ? (
          <p className="flex items-center justify-center gap-2 py-2 text-xs font-semibold text-text-low">
            <Lock size={14} aria-hidden="true" /> This thread is locked: no new replies, votes or answers.
          </p>
        ) : !userId ? (
          <p className="py-2 text-center text-xs text-text-low">Sign in to reply.</p>
        ) : !thread.can_reply ? (
          <p className="py-2 text-center text-xs text-text-low">In this forum only some members can reply.</p>
        ) : (
          <form onSubmit={send} className="space-y-2">
            {replyTo && (
              <p className="flex items-center gap-2 text-xs text-text-mid">
                Commenting on {replyTo.author?.display_name ?? 'a reply'}
                <button type="button" onClick={() => setReplyTo(null)} aria-label="Cancel the comment"><X size={12} aria-hidden="true" /></button>
              </p>
            )}
            <div className="flex gap-2">
              <textarea ref={composerRef} value={reply} onChange={(e) => setReply(e.target.value)} rows={2} maxLength={20000} placeholder={replyTo ? 'Your comment - say why it helped, or what to add' : kind === 'question' ? 'Your answer. @name to call someone in.' : 'Your reply'} aria-label="Write a reply" className={`${field} resize-y`} />
              <div className="flex flex-col gap-1">
                <input ref={fileRef} type="file" multiple hidden onChange={(e) => {
                  const list = e.target.files
                  if (!list) return
                  void (async () => {
                    for (const file of Array.from(list).slice(0, 6 - files.length)) {
                      try {
                        const asset = await kaluta.media.uploadWithProgress(file, () => undefined, { purpose: 'chat' })
                        setFiles((x) => [...x, { id: asset.id, name: file.name }])
                      } catch (err) {
                        setNote(err instanceof ApiError ? err.message : 'The file could not be uploaded')
                      }
                    }
                  })()
                }} />
                <button type="button" onClick={() => fileRef.current?.click()} aria-label="Attach a file" className="grid h-10 w-10 place-items-center rounded-xl border border-white/10 text-text-mid hover:text-text-hi">
                  <Paperclip size={15} aria-hidden="true" />
                </button>
                <button type="submit" disabled={!reply.trim() || busy === 'reply'} aria-label="Send" className="grid h-10 w-10 place-items-center rounded-xl bg-gold text-ink disabled:opacity-40">
                  <Send size={15} aria-hidden="true" />
                </button>
              </div>
            </div>
            {files.length > 0 && <p className="text-xs text-text-low">Attached: {files.map((f) => f.name).join(', ')}</p>}
          </form>
        )}
      </div>

      {editing && (
        <Sheet title={editing.kind === 'thread' ? 'Edit the thread' : 'Edit the reply'} onClose={() => setEditing(null)} wide>
          <textarea value={editing.text} onChange={(e) => setEditing({ ...editing, text: e.target.value })} rows={10} className={`${field} resize-y`} aria-label="Text" />
          <p className="mt-1 text-xs text-text-low">Everyone will see that it was edited.</p>
          <button
            type="button"
            disabled={!editing.text.trim()}
            onClick={() => {
              const e = editing
              setEditing(null)
              void act('edit', () => (e.kind === 'thread' ? kaluta.forums.editThread(e.id, { body: e.text }) : kaluta.forums.editReply(e.id, e.text)), 'Saved.')
            }}
            className="mt-3 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-50"
          >
            Save
          </button>
        </Sheet>
      )}
      {reopening && (
        <Sheet title="Reopen the question" onClose={() => setReopening(false)}>
          <p className="text-sm text-text-mid">The accepted answer stays in the discussion but is no longer marked as the solution. Say what is still missing (optional; it is posted as a reply).</p>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={1000} className={`${field} mt-2 resize-none`} aria-label="What is still missing" />
          <button type="button" onClick={() => { setReopening(false); void act('reopen', () => kaluta.forums.reopen(thread.id, reason.trim() || undefined).then(() => setReason('')), 'The question is open again.') }} className="mt-3 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink">
            Reopen
          </button>
        </Sheet>
      )}
      {modAction && (
        <Sheet
          title={modAction === 'hide' ? 'Hide this thread' : modAction === 'lock' ? 'Lock this thread' : modAction === 'move' ? 'Move to another forum' : modAction === 'merge' ? 'Merge into another thread' : 'Hide this reply'}
          onClose={() => { setModAction(null); setAudienceWarning(false) }}
        >
          {modAction === 'move' && (
            <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Forum to move it to" className={`${field} mb-2`}>
              <option value="">Choose a forum…</option>
              {otherForums.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          )}
          {modAction === 'merge' && (
            <input value={target} onChange={(e) => setTarget(e.target.value.trim())} placeholder="Link or id of the thread to merge into" aria-label="Thread to merge into" className={`${field} mb-2`} />
          )}
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={2000} placeholder="Reason - the author is told" aria-label="Reason" className={`${field} resize-none`} />
          {audienceWarning && (
            <p className="mt-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-2 text-xs text-amber-200">
              The other {modAction === 'move' ? 'forum' : 'thread'} is read by different people. Its replies will be readable by them, and some current readers may lose access. Confirm only if that is right.
            </p>
          )}
          <button
            type="button"
            disabled={!reason.trim() || ((modAction === 'move' || modAction === 'merge') && !target)}
            onClick={() => {
              const action = modAction
              const into = target.includes('thread=') ? target.split('thread=')[1].split('&')[0] : target
              const run = async () => {
                if (action === 'hide') await kaluta.forums.moderateThread(thread.id, 'hide', reason)
                else if (action === 'lock') await kaluta.forums.moderateThread(thread.id, 'lock', reason)
                else if (action === 'move') await kaluta.forums.moveThread(thread.id, target, reason, audienceWarning)
                else if (action === 'merge') {
                  await kaluta.forums.mergeThread(thread.id, into, reason, audienceWarning)
                  onOpenThread(into)
                } else if (typeof action === 'object' && action) await kaluta.forums.moderateReply(action.reply, 'hide', reason)
              }
              setBusy('mod')
              run()
                .then(async () => {
                  setModAction(null)
                  setReason('')
                  setTarget('')
                  setAudienceWarning(false)
                  if (action === 'hide') onBack()
                  else await onReload()
                })
                .catch((err) => {
                  if (err instanceof ApiError && err.status === 409 && /different people/.test(err.message)) setAudienceWarning(true)
                  else setNote(err instanceof ApiError ? err.message : 'That did not go through')
                  if (!(err instanceof ApiError && err.status === 409)) setModAction(null)
                })
                .finally(() => setBusy(null))
            }}
            className="mt-3 rounded-full bg-red-500/90 px-5 py-2 text-sm font-bold text-white disabled:opacity-50"
          >
            {audienceWarning ? 'Confirm' : 'Do it'}
          </button>
        </Sheet>
      )}
      <ConfirmDialog
        open={Boolean(accepting)}
        onOpenChange={(open) => !open && setAccepting(null)}
        title="Change the accepted answer?"
        description="Another answer is marked as the one that solved your question. This one will replace it; both stay in the discussion."
        confirmLabel="Accept this one"
        destructive={false}
        onConfirm={() => {
          const id = accepting
          setAccepting(null)
          if (id) void act(`acc-${id}`, () => kaluta.forums.acceptAnswer(id))
        }}
      />
      {reporting && <ReportDialog kind={reporting.kind} id={reporting.id} onClose={() => setReporting(null)} />}
      {profile && <ContributorDialog userId={profile} onClose={() => setProfile(null)} />}
    </article>
  )
}
