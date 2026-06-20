/**
 * Single-click sign-out across the Cognito /logout round-trip.
 *
 * The logout click's first and only action is a full-page navigation to Cognito /logout, so nothing
 * can race a sign-in redirect ahead of it. Because that navigation wipes in-memory React state, the
 * intent is persisted in sessionStorage just before leaving. On the next app load — after /logout has
 * cleared Cognito's session cookie and redirected back — consumePendingSignOut() purges the now-stale
 * local tokens BEFORE React renders, so AuthProvider boots unauthenticated and the normal flow sends
 * the user to Hosted UI with credentials required. No second click, no timing race.
 */
export const SIGNOUT_KEY = 'wc.signout';

/**
 * If a sign-out is pending, drop the stale oidc-client-ts session from localStorage and clear the
 * flag. Returns true if one was consumed. Call once at startup, before React mounts.
 */
export function consumePendingSignOut(): boolean {
  try {
    if (sessionStorage.getItem(SIGNOUT_KEY) !== '1') return false;
    sessionStorage.removeItem(SIGNOUT_KEY);
    // oidc-client-ts (WebStorageStateStore) keys its user under an "oidc." prefix.
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key && key.startsWith('oidc.')) localStorage.removeItem(key);
    }
    return true;
  } catch {
    return false;
  }
}
