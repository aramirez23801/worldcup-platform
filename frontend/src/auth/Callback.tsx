import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import { FullScreenStatus } from '../components/FullScreenStatus';

/**
 * Cognito redirect target (`/callback`). AuthProvider exchanges the code automatically; once the
 * user is authenticated we route Home. onSigninCallback (oidcConfig) has already stripped the
 * ?code/?state from the URL by this point.
 */
export default function Callback() {
  const auth = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (auth.isAuthenticated) {
      navigate('/', { replace: true });
    }
  }, [auth.isAuthenticated, navigate]);

  if (auth.error) {
    return (
      <FullScreenStatus
        tone="error"
        title="Sign-in didn't complete"
        message={auth.error.message}
        action={{ label: 'Back to sign in', onClick: () => void auth.signinRedirect() }}
      />
    );
  }

  return <FullScreenStatus title="Signing you in…" />;
}
