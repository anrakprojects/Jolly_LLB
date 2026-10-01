import { atom } from 'nanostores'

import { activateProvider, getGlobalModelInfo, getMcpConnectorStatus } from '@/hermes'
import type { ProviderActivationResult } from '@/types/hermes'

export const ANRAK_MCP_SERVER = 'Anrak Legal'
export const JOLLY_PROVIDER = 'anrak'
export const JOLLY_MODEL = 'anraklegal/jolly'
export const JOLLY_KEYS_URL = 'https://developers.anrak.legal/developers/jolly'

export interface DesktopOnboardingState {
  /** null until the first Anrak auth check resolves. Seeded from localStorage so
   *  returning users skip the boot overlay instead of flashing it every reload. */
  configured: boolean | null
  reason: null | string
  requested: boolean
  /** 'signin' = Anrak Legal account; 'jolly' = signed in, Jolly model access pending. */
  stage: 'jolly' | 'signin'
}

export interface JollyAccessState {
  message?: string
  step: 'checking' | 'idle' | 'needs_key' | 'saving'
}

export interface OnboardingContext {
  onCompleted?: () => void
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

// v2: Jolly became the default model — returning users pass through once more
// so the Jolly activation step runs (it completes silently when it can).
const CONFIGURED_CACHE_KEY = 'jolly-anrak-legal-onboarded-v2'
const JOLLY_SKIPPED_KEY = 'jolly-anrak-model-skipped-v1'
const DEFAULT_ONBOARDING_REASON = 'Sign in with your Anrak Legal account.'

function readFlag(key: string): boolean {
  if (typeof window === 'undefined') {
    return false
  }

  try {
    return window.localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writeFlag(key: string, value: boolean) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    if (value) {
      window.localStorage.setItem(key, '1')
    } else {
      window.localStorage.removeItem(key)
    }
  } catch {
    // localStorage unavailable — degrade silently.
  }
}

const INITIAL: DesktopOnboardingState = {
  configured: readFlag(CONFIGURED_CACHE_KEY) ? true : null,
  reason: null,
  requested: false,
  stage: 'signin'
}

export const $desktopOnboarding = atom<DesktopOnboardingState>(INITIAL)
export const $jollyAccess = atom<JollyAccessState>({ step: 'idle' })

const patch = (update: Partial<DesktopOnboardingState>) =>
  $desktopOnboarding.set({ ...$desktopOnboarding.get(), ...update })

export function requestDesktopOnboarding(reason = DEFAULT_ONBOARDING_REASON) {
  if ($desktopOnboarding.get().configured === true) {
    return
  }

  patch({ reason: reason.trim() || DEFAULT_ONBOARDING_REASON, requested: true })
}

export function completeDesktopOnboarding() {
  writeFlag(CONFIGURED_CACHE_KEY, true)
  $jollyAccess.set({ step: 'idle' })
  $desktopOnboarding.set({
    configured: true,
    reason: null,
    requested: false,
    stage: 'signin'
  })
}

const ACTIVATION_MESSAGES: Record<ProviderActivationResult['reason'], string> = {
  error: 'Jolly could not be reached right now.',
  network: 'Could not reach Anrak Legal. Check your connection and try again.',
  no_credentials: 'Paste a Jolly API key to finish setting up.',
  ok: '',
  plan: 'Jolly is included with Professional and Enterprise plans. Upgrade your plan or paste a Jolly API key.',
  rate_limited: 'Jolly is rate-limited right now. Wait a moment and try again.',
  unauthorized: 'Your Anrak sign-in does not include Jolly access yet. Paste a Jolly API key instead.',
  unsupported: 'Jolly is not available in this runtime. Update Jolly Anrak and try again.'
}

function describeActivation(result: ProviderActivationResult, usedPastedKey: boolean): string {
  if (result.reason === 'unauthorized' && usedPastedKey) {
    return 'That Jolly API key was rejected. Check it and try again.'
  }

  const base = ACTIVATION_MESSAGES[result.reason] || ACTIVATION_MESSAGES.error

  return result.reason === 'error' && result.message ? `${base} (${result.message})` : base
}

/** Make Jolly the primary model. Tries the Anrak sign-in first; a pasted key when given. */
export async function activateJolly(ctx: OnboardingContext, apiKey = ''): Promise<boolean> {
  $jollyAccess.set({ step: apiKey ? 'saving' : 'checking' })

  try {
    const result = await activateProvider(JOLLY_PROVIDER, JOLLY_MODEL, apiKey)

    if (result.ok) {
      writeFlag(JOLLY_SKIPPED_KEY, false)
      completeDesktopOnboarding()
      ctx.onCompleted?.()

      return true
    }

    $jollyAccess.set({ step: 'needs_key', message: describeActivation(result, Boolean(apiKey)) })
  } catch (error) {
    $jollyAccess.set({
      step: 'needs_key',
      message: error instanceof Error ? error.message : ACTIVATION_MESSAGES.error
    })
  }

  patch({ configured: false, stage: 'jolly' })

  return false
}

/** Continue on the auto-detected ChatGPT/Gemini/Claude model; Jolly can be set up later in Settings. */
export function skipJolly(ctx: OnboardingContext) {
  writeFlag(JOLLY_SKIPPED_KEY, true)
  completeDesktopOnboarding()
  ctx.onCompleted?.()
}

async function jollyReady(): Promise<boolean> {
  if (readFlag(JOLLY_SKIPPED_KEY)) {
    return true
  }

  const info = await getGlobalModelInfo()

  return info.provider === JOLLY_PROVIDER
}

export async function refreshOnboarding(ctx: OnboardingContext) {
  try {
    const status = await getMcpConnectorStatus(ANRAK_MCP_SERVER)

    if (!status.authenticated) {
      writeFlag(CONFIGURED_CACHE_KEY, false)
      patch({
        configured: false,
        reason: status.present ? DEFAULT_ONBOARDING_REASON : 'Connect your Anrak Legal account to continue.',
        stage: 'signin'
      })

      return false
    }

    if (await jollyReady()) {
      completeDesktopOnboarding()
      ctx.onCompleted?.()

      return true
    }

    // Signed in but Jolly is not the model yet: try the managed (sign-in) path.
    if ($jollyAccess.get().step === 'idle') {
      return await activateJolly(ctx)
    }

    patch({ configured: false, stage: 'jolly' })

    return false
  } catch {
    // Gateway not ready yet — stay on the preparing state.
    return false
  }
}
