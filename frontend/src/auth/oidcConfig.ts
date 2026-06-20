import type { AuthProviderProps } from 'react-oidc-context';
import { WebStorageStateStore } from 'oidc-client-ts';
import { getRuntimeConfig } from '../lib/runtimeConfig';

/**
 * Cognito Hosted UI auth-code flow via react-oidc-context.
 *
 * authority and client_id come from the resolved runtime config, so this must be built AFTER
 * loadRuntimeConfig() (main.tsx does this before mounting AuthProvider). The registered callback
 * is `${origin}/callback`; logout is handled manually (see ProfileMenu) with logout_uri = origin.
 */
export function createOidcConfig(): AuthProviderProps {
  const cfg = getRuntimeConfig();
  return {
    authority: cfg.cognitoAuthority,
    client_id: cfg.cognitoClientId,
    redirect_uri: `${window.location.origin}/callback`,
    response_type: 'code',
    scope: 'openid email profile',
    // Persist tokens so a page refresh in dev doesn't bounce back through Hosted UI.
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    automaticSilentRenew: true,
    // Strip ?code & ?state from the URL after the exchange, staying on /callback;
    // <Callback> then navigates Home once the user is stored.
    onSigninCallback: () => {
      window.history.replaceState({}, document.title, window.location.pathname);
    },
  };
}
