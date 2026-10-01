import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $desktopOnboarding,
  $jollyAccess,
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
    stage: 'signin',
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
    $jollyAccess.set({ step: 'idle' })
  })

  afterEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    $jollyAccess.set({ step: 'idle' })
    vi.restoreAllMocks()
  })

  it('completes onboarding when connected and Jolly is already the model', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path.includes('/api/mcp/oauth/') && path.endsWith('/status')) {
        return { present: true, auth: 'oauth', authenticated: true }
      }

      if (path === '/api/model/info') {
        return { provider: 'anrak', model: 'anraklegal/jolly' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)

    const ready = await refreshOnboarding(onboardingContext())

    expect(ready).toBe(true)
    expect($desktopOnboarding.get().configured).toBe(true)
    expect(window.localStorage.getItem('jolly-anrak-legal-onboarded-v2')).toBe('1')
  })

  it('activates Jolly through the Anrak sign-in when it is not the model yet', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path.includes('/api/mcp/oauth/') && path.endsWith('/status')) {
        return { present: true, auth: 'oauth', authenticated: true }
      }

      if (path === '/api/model/info') {
        return { provider: 'openai-codex', model: 'gpt-5.5' }
      }

      if (path === '/api/providers/anrak/activate') {
        return { ok: true, reason: 'ok', source: 'profile:anrak' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)

    const ready = await refreshOnboarding(onboardingContext())

    expect(ready).toBe(true)
    expect($desktopOnboarding.get().configured).toBe(true)
    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/providers/anrak/activate',
        body: { model: 'anraklegal/jolly', api_key: '' }
      })
    )
  })

  it('asks for a Jolly API key when the sign-in has no Jolly access', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path.includes('/api/mcp/oauth/') && path.endsWith('/status')) {
        return { present: true, auth: 'oauth', authenticated: true }
      }

      if (path === '/api/model/info') {
        return { provider: 'openai-codex', model: 'gpt-5.5' }
      }

      if (path === '/api/providers/anrak/activate') {
        return { ok: false, reason: 'unauthorized', status: 401, source: 'profile:anrak' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)

    const ready = await refreshOnboarding(onboardingContext())

    expect(ready).toBe(false)
    expect($desktopOnboarding.get()).toMatchObject({ configured: false, stage: 'jolly' })
    expect($jollyAccess.get().step).toBe('needs_key')
    expect($jollyAccess.get().message).toMatch(/api key/i)
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
