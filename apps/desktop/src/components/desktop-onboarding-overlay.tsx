import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { pollOAuthSession, startMcpConnectorOAuth } from '@/hermes'
import { ExternalLink, KeyRound, Loader2 } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $desktopBoot, type DesktopBootState } from '@/store/boot'
import {
  ANRAK_MCP_SERVER,
  $desktopOnboarding,
  type OnboardingContext,
  refreshOnboarding
} from '@/store/onboarding'

interface DesktopOnboardingOverlayProps {
  enabled: boolean
  onCompleted?: () => void
  requestGateway: OnboardingContext['requestGateway']
}

type AnrakStep = 'connecting' | 'done' | 'error' | 'offer' | 'unknown'

export function DesktopOnboardingOverlay({ enabled, onCompleted, requestGateway }: DesktopOnboardingOverlayProps) {
  const onboarding = useStore($desktopOnboarding)
  const boot = useStore($desktopBoot)
  const ctxRef = useRef<OnboardingContext>({ requestGateway, onCompleted })
  ctxRef.current = { requestGateway, onCompleted }

  const ctx = useMemo<OnboardingContext>(
    () => ({
      requestGateway: (...args) => ctxRef.current.requestGateway(...args),
      onCompleted: () => ctxRef.current.onCompleted?.()
    }),
    []
  )

  useEffect(() => {
    if (enabled || onboarding.requested) {
      void refreshOnboarding(ctx)
    }
  }, [ctx, enabled, onboarding.requested])

  const [anrak, setAnrak] = useState<{ message?: string; step: AnrakStep }>({
    step: 'unknown'
  })
  const anrakPoll = useRef<null | number>(null)

  useEffect(
    () => () => {
      if (anrakPoll.current !== null) {
        window.clearInterval(anrakPoll.current)
      }
    },
    []
  )

  const connectAnrak = async () => {
    setAnrak({ step: 'connecting' })
    try {
      const start = await startMcpConnectorOAuth(ANRAK_MCP_SERVER)
      if (start.flow !== 'device_code') {
        throw new Error('unexpected sign-in flow')
      }
      await window.hermesDesktop?.openExternal(start.verification_url)
      anrakPoll.current = window.setInterval(() => {
        void pollOAuthSession(`mcp:${ANRAK_MCP_SERVER}`, start.session_id)
          .then(({ error_message, status }) => {
            if (status === 'approved') {
              if (anrakPoll.current !== null) window.clearInterval(anrakPoll.current)
              setAnrak({ step: 'done' })
              void refreshOnboarding(ctxRef.current)
            } else if (status !== 'pending') {
              if (anrakPoll.current !== null) window.clearInterval(anrakPoll.current)
              setAnrak({ step: 'error', message: error_message || `Sign-in ${status}.` })
            }
          })
          .catch(() => {
            /* transient poll failure — keep polling */
          })
      }, (start.poll_interval || 3) * 1000)
    } catch (error) {
      setAnrak({ step: 'error', message: error instanceof Error ? error.message : String(error) })
    }
  }

  if (onboarding.configured === true) {
    return null
  }

  const showSignIn = enabled && onboarding.configured === false
  const step: 'connecting' | 'error' | 'offer' =
    anrak.step === 'connecting' || anrak.step === 'error' ? anrak.step : 'offer'

  return (
    <div className="fixed inset-0 z-1300 flex items-center justify-center bg-(--ui-chat-surface-background) p-6">
      <div className="w-full max-w-[45rem] overflow-hidden rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-chat-bubble-background) shadow-sm">
        <div className="border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-bubble-background) px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-(--ui-bg-tertiary) text-(--ui-text-tertiary)">
              <KeyRound className="size-5" />
            </div>
            <div>
              <h2 className="text-[0.9375rem] font-semibold tracking-tight">Sign in to Anrak Legal</h2>
              <p className="mt-1 max-w-xl text-[0.8125rem] leading-5 text-(--ui-text-tertiary)">
                Use your Anrak Legal account to unlock research, case files, drafting, and the rest of your paralegal
                tools. This is the only sign-in Jolly Anrak uses.
              </p>
            </div>
          </div>
        </div>
        <div className="grid gap-3 p-5">
          {showSignIn ? (
            <AnrakSignIn
              message={anrak.message}
              onConnect={() => void connectAnrak()}
              step={step}
            />
          ) : (
            <Preparing boot={boot} />
          )}
        </div>
      </div>
    </div>
  )
}

function AnrakSignIn({
  message,
  onConnect,
  step
}: {
  message?: string
  onConnect: () => void
  step: 'connecting' | 'error' | 'offer'
}) {
  return (
    <div className="grid gap-3">
      {step === 'error' && message ? (
        <div className="rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {message}
        </div>
      ) : null}
      {step === 'connecting' ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Waiting for you to finish signing in — a browser window has opened…
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          We will open Anrak Legal in your browser. Authorize Jolly Anrak there, then this window continues
          automatically.
        </p>
      )}
      <div className="flex justify-end">
        <Button disabled={step === 'connecting'} onClick={onConnect}>
          {step === 'error' ? 'Try again' : 'Sign in with Anrak Legal'}
          <ExternalLink className="ml-1.5 size-3.5" />
        </Button>
      </div>
    </div>
  )
}

function Preparing({ boot }: { boot: DesktopBootState }) {
  const progress = Math.max(2, Math.min(100, Math.round(boot.progress)))
  const hasError = Boolean(boot.error)
  const installing = boot.phase.startsWith('runtime.')

  return (
    <div className="grid gap-3" role="status">
      <p className="text-sm text-muted-foreground">
        {installing
          ? 'Jolly Anrak is finishing install. This usually takes under a minute on first run.'
          : 'Starting Jolly Anrak…'}
      </p>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            'h-full rounded-full bg-primary transition-[width] duration-300 ease-out',
            hasError && 'bg-destructive'
          )}
          style={{ width: `${progress}%` }}
        />
      </div>
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span className="truncate">{boot.message}</span>
        <span>{progress}%</span>
      </div>
      {hasError ? <p className="text-xs text-destructive">{boot.error}</p> : null}
    </div>
  )
}
