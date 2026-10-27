import type { AudienceMode, AudiencePerson } from '@/lib/api'

/** What the composer holds for a post shared with circles and people. */
export interface AudienceDraft {
  circleIds: string[]
  include: AudiencePerson[]
  exclude: AudiencePerson[]
  mode: AudienceMode
  notify: boolean
}

export const EMPTY_AUDIENCE: AudienceDraft = { circleIds: [], include: [], exclude: [], mode: 'live', notify: false }

export const audienceIsEmpty = (draft: AudienceDraft) => draft.circleIds.length === 0 && draft.include.length === 0

export const MODE_TEXT: Record<AudienceMode, { title: string; body: string }> = {
  live: {
    title: 'Follow my circles',
    body: 'People you add to these circles later can see it. People you remove can no longer see it.',
  },
  snapshot: {
    title: 'Only the people in them now',
    body: 'Fixed when the post is published. People added later do not get it; people you remove keep it unless they block you, you leave them out, or they opt out.',
  },
}
