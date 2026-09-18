import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $desktopOnboarding,
  type DesktopOnboardingState,
  type OnboardingContext,
  refreshOnboarding,
  requestDesktopOnboarding
} from './onboarding'

function baseState(overrides: Partial<DesktopOnboardingState> = {}): DesktopOnboardingState {
  return {
    configured: false,
    reason: null,
    requested: false,
    ...overrides
  }
}

function installApiMock(api: (request: { path: string }) => Promise<unknown>) {
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { api }
  })
}

function onboardingContext(): OnboardingContext {
  return { requestGateway: async () => undefined as never }
}

describe('refreshOnboarding', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  it('completes onboarding when the Anrak Legal account is already connected', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path.includes('/api/mcp/oauth/') && path.endsWith('/status')) {
        return { present: true, auth: 'oauth', authenticated: true }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)

    const ready = await refreshOnboarding(onboardingContext())

    expect(ready).toBe(true)
    expect($desktopOnboarding.get().configured).toBe(true)
    expect(window.localStorage.getItem('jolly-anrak-legal-onboarded-v1')).toBe('1')
  })

  it('keeps the Anrak sign-in open when the account is not connected', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path.includes('/api/mcp/oauth/') && path.endsWith('/status')) {
        return { present: true, auth: 'oauth', authenticated: false }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    requestDesktopOnboarding()

    const ready = await refreshOnboarding(onboardingContext())

    expect(ready).toBe(false)
    expect($desktopOnboarding.get().configured).toBe(false)
    expect($desktopOnboarding.get().reason).toMatch(/anrak legal/i)
  })

  it('does not reopen sign-in after Anrak is already connected', () => {
    $desktopOnboarding.set(baseState({ configured: true }))
    requestDesktopOnboarding('Add a provider credential before sending your first message.')
    expect($desktopOnboarding.get().requested).toBe(false)
    expect($desktopOnboarding.get().configured).toBe(true)
  })
})
