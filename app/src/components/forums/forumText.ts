import type { ThreadKind } from '@/lib/api'

/** Words the forum screens share. */

export const KIND: Record<ThreadKind, { label: string; hint: string }> = {
  question: { label: 'Question', hint: 'You need an answer. You can accept the one that solves it.' },
  discussion: { label: 'Discussion', hint: 'An open conversation, with no single answer.' },
  guide: { label: 'Guide', hint: 'You explain how to do something, for others to use.' },
  announcement: { label: 'Announcement', hint: 'News from the forum’s moderators.' },
}

export const VISIBILITY: Record<string, { label: string; hint: string }> = {
  public: { label: 'Public', hint: 'Anyone can read it.' },
  members: { label: 'Community members only', hint: 'Only active members of its community can read it.' },
  private: { label: 'Private', hint: 'Only its moderators and the community’s owner and moderators.' },
}

export const POLICY: Record<string, string> = {
  everyone: 'Everyone who can read it',
  members: 'Community members',
  moderators: 'Moderators only',
}

export const FORUM_REPORT_REASONS: Array<{ id: string; label: string }> = [
  { id: 'spam', label: 'Spam or repeated promotion' },
  { id: 'harassment', label: 'Harassment' },
  { id: 'hate', label: 'Hate' },
  { id: 'violence', label: 'Violence or threats' },
  { id: 'sexual', label: 'Sexual content' },
  { id: 'child_safety', label: 'A child’s safety' },
  { id: 'misinformation', label: 'Dangerous or false information' },
  { id: 'private_info', label: 'Someone’s private information' },
  { id: 'other', label: 'Something else' },
]

/** Relative time, compact for cards. */
export function ago(iso?: string | null): string {
  if (!iso) return ''
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function dateOf(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : ''
}

/** "CI" -> "Côte d'Ivoire", in the reader's language when the browser knows it. */
export function countryName(code?: string | null): string {
  if (!code) return ''
  try {
    return new Intl.DisplayNames(undefined, { type: 'region' }).of(code) ?? code
  } catch {
    return code
  }
}

/** The text fields of the forum dialogs. */
export const field =
  'w-full rounded-xl border border-white/12 bg-text-hi/[0.05] px-3 py-2 text-sm text-text-hi placeholder:text-text-low focus:border-gold/50 focus:outline-none'
