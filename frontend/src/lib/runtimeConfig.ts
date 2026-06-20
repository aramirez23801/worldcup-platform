/**
 * Runtime configuration — resolved once at startup, then read synchronously everywhere.
 *
 * Dev (import.meta.env.DEV): build from the VITE_* vars in .env.local — unchanged dev behavior.
 * Prod: fetch /config.json (written to the site root by the CDK FrontendStack at deploy time),
 * so the build itself stays account-agnostic — no backend URLs or Cognito values are baked in.
 *
 * Call loadRuntimeConfig() once before React mounts (main.tsx awaits it, since the OIDC
 * AuthProvider's authority/client_id come from here); after that use getRuntimeConfig() anywhere.
 */
export interface RuntimeConfig {
  apiUrl: string; // normalized: no trailing slash, so `${apiUrl}/path` never doubles up
  wsUrl: string;
  cognitoAuthority: string;
  cognitoClientId: string;
  cognitoDomain: string;
  region: string;
}

const REQUIRED_KEYS = [
  'apiUrl',
  'wsUrl',
  'cognitoAuthority',
  'cognitoClientId',
  'cognitoDomain',
  'region',
] as const;

const stripTrailingSlash = (s: string): string => s.replace(/\/+$/, '');

let cached: RuntimeConfig | null = null;

function fromEnv(): RuntimeConfig {
  return {
    apiUrl: stripTrailingSlash(import.meta.env.VITE_API_URL),
    wsUrl: import.meta.env.VITE_WS_URL,
    cognitoAuthority: import.meta.env.VITE_COGNITO_AUTHORITY,
    cognitoClientId: import.meta.env.VITE_COGNITO_CLIENT_ID,
    cognitoDomain: import.meta.env.VITE_COGNITO_DOMAIN,
    region: import.meta.env.VITE_REGION,
  };
}

function fromJson(data: unknown): RuntimeConfig {
  if (!data || typeof data !== 'object') {
    throw new Error('Failed to load /config.json: response was not a JSON object');
  }
  const raw = data as Record<string, unknown>;
  for (const key of REQUIRED_KEYS) {
    if (typeof raw[key] !== 'string' || raw[key] === '') {
      throw new Error(`Failed to load /config.json: missing or invalid "${key}"`);
    }
  }
  return {
    apiUrl: stripTrailingSlash(raw.apiUrl as string),
    wsUrl: raw.wsUrl as string,
    cognitoAuthority: raw.cognitoAuthority as string,
    cognitoClientId: raw.cognitoClientId as string,
    cognitoDomain: raw.cognitoDomain as string,
    region: raw.region as string,
  };
}

/** Resolve runtime config once (cached). Dev → env vars; prod → /config.json. */
export async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  if (cached) return cached;

  if (import.meta.env.DEV) {
    cached = fromEnv();
    return cached;
  }

  let data: unknown;
  try {
    const res = await fetch('/config.json', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    throw new Error(`Failed to load /config.json: ${e instanceof Error ? e.message : String(e)}`);
  }

  cached = fromJson(data);
  return cached;
}

/** The resolved config. Throws if loadRuntimeConfig() hasn't completed yet. */
export function getRuntimeConfig(): RuntimeConfig {
  if (!cached) {
    throw new Error('Runtime config not loaded — call loadRuntimeConfig() before getRuntimeConfig().');
  }
  return cached;
}
