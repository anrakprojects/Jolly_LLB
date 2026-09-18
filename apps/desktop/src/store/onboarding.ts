import { atom } from 'nanostores'

import { getMcpConnectorStatus } from '@/hermes'

export const ANRAK_MCP_SERVER = 'Anrak Legal'

export interface DesktopOnboardingState {
  /** null until the first Anrak auth check resolves. Seeded from localStorage so
   *  returning users skip the boot overlay instead of flashing it every reload. */
  configured: boolean | null
  reason: null | string
  requested: boolean
}

export interface OnboardingContext {
  onCompleted?: () => void
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

const CONFIGURED_CACHE_KEY = 'jolly-anrak-legal-onboarded-v1'
const DEFAULT_ONBOARDING_REASON = 'Sign in with your Anrak Legal account.'

function readCachedConfigured(): boolean | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    return window.localStorage.getItem(CONFIGURED_CACHE_KEY) === '1' ? true : null
  } catch {
    return null
  }
}

function writeCachedConfigured(value: boolean) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    if (value) {
      window.localStorage.setItem(CONFIGURED_CACHE_KEY, '1')
    } else {
      window.localStorage.removeItem(CONFIGURED_CACHE_KEY)
    }
  } catch {
    // localStorage unavailable — degrade silently.
  }
}

const INITIAL: DesktopOnboardingState = {
  configured: readCachedConfigured(),
  reason: null,
  requested: false
}

export const $desktopOnboarding = atom<DesktopOnboardingState>(INITIAL)

const patch = (update: Partial<DesktopOnboardingState>) =>
  $desktopOnboarding.set({ ...$desktopOnboarding.get(), ...update })

export function requestDesktopOnboarding(reason = DEFAULT_ONBOARDING_REASON) {
  if ($desktopOnboarding.get().configured === true) {
    return
  }

  patch({ reason: reason.trim() || DEFAULT_ONBOARDING_REASON, requested: true })
}

export function completeDesktopOnboarding() {
  writeCachedConfigured(true)
  $desktopOnboarding.set({
    configured: true,
    reason: null,
    requested: false
  })
}

export async function refreshOnboarding(ctx: OnboardingContext) {
  try {
    const status = await getMcpConnectorStatus(ANRAK_MCP_SERVER)

    if (status.authenticated) {
      completeDesktopOnboarding()
      ctx.onCompleted?.()

      return true
    }

    writeCachedConfigured(false)
    patch({
      configured: false,
      reason: status.present ? DEFAULT_ONBOARDING_REASON : 'Connect your Anrak Legal account to continue.'
    })

    return false
  } catch {
    // Gateway not ready yet — stay on the preparing state.
    return false
  }
}
