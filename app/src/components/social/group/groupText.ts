import type { MessageBrief, ReportReason } from '@/lib/api'

/** Words the group screens share. */

export const REPORT_REASONS: Array<{ id: ReportReason; label: string }> = [
  { id: 'spam', label: 'Spam or unwanted promotion' },
  { id: 'harassment', label: 'Harassment or bullying' },
  { id: 'hate', label: 'Hate' },
  { id: 'violence', label: 'Violence or threats' },
  { id: 'sexual', label: 'Sexual content' },
  { id: 'child_safety', label: 'A child’s safety' },
  { id: 'scam', label: 'A scam or fraud' },
  { id: 'other', label: 'Something else' },
]

export const ROLE_LABEL: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  moderator: 'Moderator',
  member: 'Member',
}

/** One line for a message in a list: pins, saved, replies, search. */
export function briefText(m: Pick<MessageBrief, 'body' | 'media_kind' | 'deleted' | 'encrypted' | 'kind' | 'media_withheld'>): string {
  if (m.deleted) return 'This message was deleted'
  if (m.encrypted) return 'Encrypted message'
  const label = { poll: 'Poll', event: 'Event', task: 'Task' }[m.kind as 'poll' | 'event' | 'task']
  if (label) return `${label}: ${m.body ?? ''}`
  if (m.media_withheld) return 'A file is hidden until you accept the group'
  if (m.body) return m.body
  return m.media_kind ? `A ${m.media_kind === 'image' ? 'photo' : m.media_kind}` : ''
}
