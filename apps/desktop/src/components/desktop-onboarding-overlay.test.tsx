import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $desktopOnboarding, type DesktopOnboardingState, type OnboardingContext } from '@/store/onboarding'

import { DesktopOnboardingOverlay } from './desktop-onboarding-overlay'

const ctx: OnboardingContext = { requestGateway: async () => undefined as never }

function setOnboarding(overrides: Partial<DesktopOnboardingState> = {}) {
  $desktopOnboarding.set({
    configured: false,
    reason: 'Sign in with your Anrak Legal account.',
    requested: false,
    ...overrides
  } satisfies DesktopOnboardingState)
}

afterEach(() => {
  cleanup()
  $desktopOnboarding.set({
    configured: null,
    reason: null,
    requested: false
  })
  vi.restoreAllMocks()
})

describe('DesktopOnboardingOverlay', () => {
  it('asks the user to sign in with Anrak Legal, not ChatGPT, Gemini, or Claude', () => {
    setOnboarding({ configured: false })
    render(<DesktopOnboardingOverlay enabled onCompleted={ctx.onCompleted} requestGateway={ctx.requestGateway} />)

    expect(screen.getByText('Sign in to Anrak Legal')).toBeTruthy()
    expect(screen.getByRole('button', { name: /sign in with anrak legal/i })).toBeTruthy()
    expect(screen.queryByText('ChatGPT')).toBeNull()
    expect(screen.queryByText('Google Gemini')).toBeNull()
    expect(screen.queryByText('Claude')).toBeNull()
    expect(screen.queryByText('Connect a model provider to start chatting')).toBeNull()
  })

  it('does not offer a skip path past Anrak sign-in', () => {
    setOnboarding({ configured: false })
    render(<DesktopOnboardingOverlay enabled onCompleted={ctx.onCompleted} requestGateway={ctx.requestGateway} />)

    expect(screen.queryByRole('button', { name: /skip/i })).toBeNull()
  })

  it('hides the overlay after Anrak is connected', () => {
    setOnboarding({ configured: true })
    const { container } = render(
      <DesktopOnboardingOverlay enabled onCompleted={ctx.onCompleted} requestGateway={ctx.requestGateway} />
    )

    expect(container.innerHTML).toBe('')
  })

  it('starts Anrak OAuth when the user clicks sign in', async () => {
    setOnboarding({ configured: false })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        openExternal: vi.fn(async () => undefined),
        api: vi.fn(async ({ path }: { path: string }) => {
          if (path.includes('/api/mcp/oauth/') && path.endsWith('/start')) {
            return {
              flow: 'device_code',
              session_id: 'sess-1',
              verification_url: 'https://anrak.legal/device',
              poll_interval: 3
            }
          }

          if (path.includes('/status')) {
            return { present: true, auth: 'oauth', authenticated: false }
          }

          throw new Error(`unexpected api path: ${path}`)
        })
      }
    })

    render(<DesktopOnboardingOverlay enabled onCompleted={ctx.onCompleted} requestGateway={ctx.requestGateway} />)
    fireEvent.click(screen.getByRole('button', { name: /sign in with anrak legal/i }))

    expect(await screen.findByText(/waiting for you to finish signing in/i)).toBeTruthy()
  })
})
