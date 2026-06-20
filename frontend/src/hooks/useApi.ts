import { useMemo } from 'react';
import { useAuth } from 'react-oidc-context';
import { createApi, type Api } from '../lib/api';

/** REST client bound to the current Cognito ID token. */
export function useApi(): Api {
  const auth = useAuth();
  const token = auth.user?.id_token;
  return useMemo(() => createApi(token), [token]);
}
