import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ArrowRight, Eye, MessageSquare, Sparkles, TreeDeciduous, UserPlus, UsersRound } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta } from '@/lib/api'
import { FEATURES, type Feature } from '@/lib/features'
import { announce, onChange } from '@/lib/live'
import { Badge, Panel, PanelState } from './primitives'
import { cn } from '@/lib/utils'

const CHOICES = [
  { id: 'everyone', label: 'Anyone' },
  { id: 'connections', label: 'Connections only' },
  { id: 'nobody', label: 'No one' },
] as const

interface Control {
  key: string
  icon: LucideIcon
  title: string
  hint: string
  /** Defaults to CHOICES. The family tree asks a different question. */
  choices?: ReadonlyArray<{ id: string; label: string }>
  fallback?: string
  /** A setting about a feature that is switched off is not shown either. */
  feature?: Feature
}

/** Who may read your family tree. "No one" is not offered here — closing the
 *  tree entirely is the separate switch below, so the two are not confused. */
const FAMILY_CHOICES = [
  { id: 'family', label: 'Your family' },
  { id: 'connections', label: 'Connections' },
  { id: 'everyone', label: 'Anyone' },
] as const

const ALL_CONTROLS: Control[] = [
  {
    key: 'who_can_invite',
    icon: UserPlus,
    title: 'Send you a connection invitation',
    hint: 'Following you is always open — following is one-way and public.',
  },
  {
    key: 'who_can_message',
    icon: MessageSquare,
    title: 'Message you',
    hint: 'Enforced by messaging-service, not just hidden in the UI.',
  },
  {
    key: 'who_can_add_family',
    icon: TreeDeciduous,
    title: 'Add you to a family tree',
    hint: 'Decides who may put your own node into their family tree. You can remove yourself from a tree at any time.',
    feature: 'familyTreeApp',
  },
  {
    key: 'who_can_add_community',
    icon: UsersRound,
    title: 'Add you to a community',
    hint: 'Stops someone dropping you into a group you never asked for.',
  },
  {
    key: 'who_can_add_group',
    icon: MessageSquare,
    title: 'Add you straight into a group chat',
    hint: 'In Messages. Anybody else can only invite you, if you allow it below, and you join when you accept.',
  },
  {
    key: 'who_can_invite_group',
    icon: MessageSquare,
    title: 'Invite you to a group chat',
    hint: 'An invitation shows the group and its rules; you accept or decline it. Blocked people can never invite you.',
  },
  {
    key: 'who_can_see_family',
    icon: TreeDeciduous,
    title: 'See your family tree',
    hint: 'Applied by family-service on every read — names, dates and relationships, including the dead. "Your family" means people who share the graph with you.',
    choices: FAMILY_CHOICES,
    fallback: 'family',
    feature: 'familyTreeApp',
  },
]

const CONTROLS = ALL_CONTROLS.filter((control) => !control.feature || FEATURES[control.feature])

/**
 * Privacy — who may reach you.
 *
 * The invitations and connections themselves are managed on /connections; this
 * tab keeps the rules and a pointer there, so there is one place to act on a
 * person rather than two that can each do half of it.
 */
export default function Privacy() {
  const prefs = useApi<Record<string, unknown>>(() => kaluta.account.preferences(), [])
  const connections = useApi(() => kaluta.connections.list(), [])
  const [saving, setSaving] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => onChange('connections', connections.reload), [connections.reload])

  const set = async (key: string, value: string | boolean) => {
    setSaving(key)
    setNote(null)
    try {
      await kaluta.account.setPreferences({ [key]: value })
      prefs.reload()
      // The assistant orb lives outside this tree; tell it to re-read.
      if (key === 'assistant_visible') announce('profile')
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not save that setting')
    } finally {
      setSaving(null)
    }
  }

  const incoming = connections.data?.incoming.length ?? 0
  const accepted = connections.data?.accepted.length ?? 0

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
      <Panel
        title="Who can reach you"
        subtitle="Following is always open. Everything below is your call."
      >
        <PanelState loading={prefs.loading} error={prefs.error}>
          <ul className="space-y-4">
            {CONTROLS.map((control) => {
              const value = String(prefs.data?.[control.key] ?? control.fallback ?? 'connections')
              const choices = control.choices ?? CHOICES
              return (
                <li key={control.key}>
                  <p className="flex items-center gap-2 text-sm font-medium text-text-hi">
                    <control.icon size={14} className="shrink-0 text-gold" aria-hidden="true" />
                    {control.title}
                    {saving === control.key && <span className="caption">saving…</span>}
                  </p>
                  <p className="caption mt-0.5 ps-6">{control.hint}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5 ps-6">
                    {choices.map((choice) => (
                      <button
                        key={choice.id}
                        type="button"
                        onClick={() => set(control.key, choice.id)}
                        aria-pressed={value === choice.id}
                        className={cn(
                          'rounded-full border px-3 py-1 text-xs font-semibold',
                          value === choice.id
                            ? 'border-gold/50 bg-gold/10 text-gold-soft'
                            : 'border-[var(--cloud-border)] text-text-mid hover:text-text-hi',
                        )}
                      >
                        {choice.label}
                      </button>
                    ))}
                  </div>
                </li>
              )
            })}

            {FEATURES.assistant && (
              <li className="border-t border-[var(--cloud-border)] pt-4">
                <label className="flex items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={Boolean(prefs.data?.assistant_visible ?? true)}
                    onChange={(e) => set('assistant_visible', e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="flex items-center gap-2 text-sm font-medium text-text-hi">
                      <Sparkles size={14} className="text-gold" aria-hidden="true" />
                      Show the Kinjy Assistant
                    </span>
                    <span className="caption mt-0.5 block">
                      The floating orb. Turning it off hides it everywhere — it is always-on-top UI,
                      so dismissing it should be a real setting, not a close button that comes back.
                    </span>
                  </span>
                </label>
              </li>
            )}

            <li className="border-t border-[var(--cloud-border)] pt-4">
              <label className="flex items-start gap-2.5">
                <input
                  type="checkbox"
                  checked={Boolean(prefs.data?.discoverable ?? true)}
                  onChange={(e) => set('discoverable', e.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  <span className="flex items-center gap-2 text-sm font-medium text-text-hi">
                    <Eye size={14} className="text-gold" aria-hidden="true" />
                    Suggest me to other members
                  </span>
                  <span className="caption mt-0.5 block">
                    Turning this off keeps you out of the “People to follow” rail. It does not hide
                    your posts — visibility per post is set when you publish.
                  </span>
                </span>
              </label>
            </li>

            {FEATURES.familyTreeApp && (
              <li className="border-t border-[var(--cloud-border)] pt-4">
                <label className="flex items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={Boolean(prefs.data?.family_tree_shared ?? true)}
                    onChange={(e) => set('family_tree_shared', e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="flex items-center gap-2 text-sm font-medium text-text-hi">
                      <TreeDeciduous size={14} className="text-gold" aria-hidden="true" />
                      Share your family tree at all
                    </span>
                    <span className="caption mt-0.5 block">
                      Turning this off closes the tree to everyone regardless of the audience above,
                      without losing the choice you made there. You always keep full access to the
                      people you added yourself.
                    </span>
                  </span>
                </label>
              </li>
            )}
          </ul>
        </PanelState>
      </Panel>

      <Panel
        title="Connections"
        subtitle={`Accepting an invitation is what opens messaging, ${FEATURES.familyTreeApp ? 'family links ' : ''}and community invites.`}
        action={incoming > 0 ? <Badge tone="warn">{incoming} waiting</Badge> : undefined}
      >
        <PanelState loading={connections.loading} error={connections.error}>
          <p className="text-sm text-text-mid">
            <span className="mono-data text-gold-soft">{incoming}</span>{' '}
            {incoming === 1 ? 'invitation' : 'invitations'} waiting ·{' '}
            <span className="mono-data text-gold-soft">{accepted}</span>{' '}
            {accepted === 1 ? 'connection' : 'connections'}
          </p>
          <Link
            to={incoming > 0 ? '/connections?tab=incoming' : '/connections'}
            className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-gold/40 px-3.5 py-1.5 text-xs font-semibold text-gold-soft hover:bg-gold/10"
          >
            Manage your connections
            <ArrowRight size={13} aria-hidden="true" />
          </Link>
        </PanelState>
      </Panel>

      {note && <p className="text-sm text-gold-soft lg:col-span-2">{note}</p>}
    </div>
  )
}
