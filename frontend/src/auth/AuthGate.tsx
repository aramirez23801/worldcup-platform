import { useEffect } from 'react';
import { useAuth, hasAuthParams } from 'react-oidc-context';
import { FullScreenStatus } from '../components/FullScreenStatus';

/**
 * Gates the app on Cognito auth. An unauthenticated visitor is sent straight to Hosted UI (no
 * custom login form — §4.0). Sign-out is handled entirely by the navigate-first logout in
 * ProfileMenu plus the startup purge in main.tsx, so there's nothing special to do here.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const auth = useAuth();

  useEffect(() => {
    if (
      !hasAuthParams() &&
      !auth.isAuthenticated &&
      !auth.activeNavigator &&
      !auth.isLoading &&
      !auth.error
    ) {
      void auth.signinRedirect();
    }
  }, [auth.isAuthenticated, auth.activeNavigator, auth.isLoading, auth.error, auth]);

  if (auth.error) {
    return (
      <FullScreenStatus
        tone="error"
        title="Couldn't sign you in"
        message={auth.error.message}
        action={{ label: 'Try again', onClick: () => void auth.signinRedirect() }}
      />
    );
  }

  if (auth.isAuthenticated) {
    return <>{children}</>;
  }

  return <FullScreenStatus title="Redirecting to sign in…" />;
}
