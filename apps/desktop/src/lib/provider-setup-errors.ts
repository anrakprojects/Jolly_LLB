const PROVIDER_SETUP_ERROR_RE =
  /No (?:inference|Jolly LLB) provider(?: is)? configured|no_provider_configured|OPENROUTER_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|set an API key|No usable credentials found/i

// Messages that mean an existing OAuth sign-in has died and the user must sign
// in again — refresh token consumed by another client (providers rotate
// refresh tokens, so the Codex CLI / VS Code and Jolly LLB can invalidate each
// other), tokens stripped after a failed refresh, or credentials missing from
// the store the runtime actually reads.
const PROVIDER_REAUTH_ERROR_RE =
  /refresh token (?:was |is )?already consumed|re-?authenticate|relogin|run `?hermes auth`?|No (?:Codex|Anthropic) (?:OAuth )?credentials|missing (?:access|refresh)_token|sign-?in has expired/i

export function isProviderSetupErrorMessage(message: null | string | undefined): boolean {
  const text = message?.trim()

  if (!text) {
    return false
  }

  return PROVIDER_SETUP_ERROR_RE.test(text) || PROVIDER_REAUTH_ERROR_RE.test(text)
}

/** Friendly one-liner for a dead sign-in, or null when the message is not a
 * re-auth problem. Shown above the provider picker so the user knows why the
 * sign-in screen reappeared instead of seeing a raw runtime error. */
export function describeProviderReauth(message: null | string | undefined): null | string {
  const text = message?.trim()

  if (!text || !PROVIDER_REAUTH_ERROR_RE.test(text)) {
    return null
  }

  const provider = /codex|chatgpt|openai/i.test(text)
    ? 'ChatGPT'
    : /anthropic|claude/i.test(text)
      ? 'Claude'
      : null

  return provider
    ? `Your ${provider} sign-in has expired. Sign in again below to keep chatting.`
    : 'Your sign-in has expired. Sign in again below to keep chatting.'
}
