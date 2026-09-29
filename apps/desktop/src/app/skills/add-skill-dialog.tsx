import type * as React from 'react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { TextTab } from '@/components/ui/text-tab'
import { Textarea } from '@/components/ui/textarea'
import { createCustomSkill, getActionStatus, installSkillFromHub, searchSkillsHub } from '@/hermes'
import { notify, notifyError } from '@/store/notifications'
import type { SkillHubResult } from '@/types/hermes'

type AddMode = 'write' | 'hub'

// Mirrors VALID_NAME_RE in tools/skill_manager_tool.py.
const SKILL_NAME_RE = /^[a-z0-9][a-z0-9._-]*$/

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .replace(/-+$/, '')
    .slice(0, 64)
}

interface AddSkillDialogProps extends Omit<React.ComponentProps<typeof Dialog>, 'children'> {
  /** Must be referentially stable — the hub install poller depends on it. */
  onAdded: () => void
}

export function AddSkillDialog({ onAdded, onOpenChange, open, ...props }: AddSkillDialogProps) {
  const [mode, setMode] = useState<AddMode>('write')

  return (
    <Dialog onOpenChange={onOpenChange} open={open} {...props}>
      <DialogContent className="max-w-xl gap-4">
        <DialogHeader>
          <DialogTitle>Add a skill</DialogTitle>
          <DialogDescription>
            Teach Jolly Anrak your own procedures — a firm style guide, a due-diligence checklist, a filing workflow —
            or install one from the skill hub.
          </DialogDescription>
        </DialogHeader>
        <div className="flex gap-2">
          <TextTab active={mode === 'write'} onClick={() => setMode('write')}>
            Write your own
          </TextTab>
          <TextTab active={mode === 'hub'} onClick={() => setMode('hub')}>
            From the hub
          </TextTab>
        </div>
        {mode === 'write' ? (
          <WriteSkillForm
            onCancel={() => onOpenChange?.(false)}
            onCreated={() => {
              onAdded()
              onOpenChange?.(false)
            }}
          />
        ) : (
          <HubInstallPanel onInstalled={onAdded} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function WriteSkillForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: () => void }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [instructions, setInstructions] = useState('')
  const [saving, setSaving] = useState(false)

  const name = slugify(title)
  const valid = SKILL_NAME_RE.test(name) && description.trim().length > 0 && instructions.trim().length > 0

  async function save() {
    setSaving(true)

    try {
      await createCustomSkill({ name, description: description.trim(), instructions })
      notify({ kind: 'success', title: 'Skill added', message: `${name} applies to new sessions.` })
      setTitle('')
      setDescription('')
      setInstructions('')
      onCreated()
    } catch (err) {
      notifyError(err, `Failed to add ${name}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="grid gap-3"
      onSubmit={e => {
        e.preventDefault()

        if (valid) {
          void save()
        }
      }}
    >
      <div className="grid gap-1.5">
        <Input onChange={e => setTitle(e.target.value)} placeholder="Name, e.g. NDA review checklist" value={title} />
        {name && <p className="font-mono text-[0.65rem] text-muted-foreground">{name}</p>}
      </div>
      <Input
        onChange={e => setDescription(e.target.value)}
        placeholder="When to use it, e.g. Review NDAs against our firm's playbook."
        value={description}
      />
      <Textarea
        className="min-h-40 font-mono text-xs"
        onChange={e => setInstructions(e.target.value)}
        placeholder={
          'Step-by-step instructions in Markdown.\n\n## Procedure\n1. Identify the parties and term.\n2. Flag non-standard confidentiality carve-outs.\n3. ...'
        }
        value={instructions}
      />
      <DialogFooter>
        <Button onClick={onCancel} type="button" variant="ghost">
          Cancel
        </Button>
        <Button disabled={!valid || saving} type="submit">
          {saving ? 'Adding…' : 'Add skill'}
        </Button>
      </DialogFooter>
    </form>
  )
}

function HubInstallPanel({ onInstalled }: { onInstalled: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SkillHubResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [action, setAction] = useState<string | null>(null)
  const [actionLog, setActionLog] = useState<string[]>([])
  const [actionRunning, setActionRunning] = useState(false)

  async function runSearch() {
    const q = query.trim()

    if (!q) {
      return
    }

    setSearching(true)

    try {
      setResults((await searchSkillsHub(q)).results)
    } catch (err) {
      notifyError(err, 'Hub search failed')
      setResults([])
    } finally {
      setSearching(false)
    }
  }

  async function install(identifier: string) {
    try {
      const res = await installSkillFromHub(identifier)
      setActionLog([])
      setActionRunning(true)
      setAction(res.name)
    } catch (err) {
      notifyError(err, `Failed to install ${identifier}`)
    }
  }

  // Tail the background install until it exits, then refresh the skill list.
  useEffect(() => {
    if (!action) {
      return
    }

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const status = await getActionStatus(action, 200)

        if (cancelled) {
          return
        }

        setActionLog(status.lines)
        setActionRunning(status.running)

        if (status.running) {
          timer = setTimeout(() => void poll(), 1200)
        } else if (status.exit_code === 0) {
          notify({ kind: 'success', title: 'Skill installed', message: 'It applies to new sessions.' })
          onInstalled()
        }
      } catch {
        if (!cancelled) {
          setActionRunning(false)
        }
      }
    }

    void poll()

    return () => {
      cancelled = true

      if (timer) {
        clearTimeout(timer)
      }
    }
  }, [action, onInstalled])

  return (
    <div className="grid gap-3">
      <form
        className="flex gap-2"
        onSubmit={e => {
          e.preventDefault()
          void runSearch()
        }}
      >
        <Input onChange={e => setQuery(e.target.value)} placeholder="Search the skill hub…" value={query} />
        <Button disabled={searching || !query.trim()} type="submit" variant="outline">
          <Codicon name={searching ? 'loading' : 'search'} size="0.875rem" spinning={searching} />
          Search
        </Button>
      </form>

      {action && (
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-(--ui-bg-quinary) p-2 font-mono text-[0.65rem] text-(--ui-text-tertiary)">
          {actionLog.length ? actionLog.join('\n') : actionRunning ? 'Starting…' : 'Done.'}
        </pre>
      )}

      {results && (
        <div className="max-h-72 divide-y divide-(--ui-stroke-quaternary) overflow-y-auto">
          {results.length === 0 ? (
            <p className="py-6 text-center text-xs text-muted-foreground">No matching skills in the hub.</p>
          ) : (
            results.map(result => (
              <div className="flex items-start gap-3 py-2.5" key={result.identifier}>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{result.name}</div>
                  <p className="mt-0.5 text-xs text-muted-foreground">{result.description || 'No description.'}</p>
                  <p className="mt-0.5 truncate font-mono text-[0.65rem] text-(--ui-text-tertiary)">
                    {result.source} · {result.trust_level} · {result.identifier}
                  </p>
                </div>
                <Button
                  disabled={actionRunning}
                  onClick={() => void install(result.identifier)}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  Install
                </Button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
