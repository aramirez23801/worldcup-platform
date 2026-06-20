import { getRuntimeConfig } from './runtimeConfig';
import type {
  AgentReply,
  Bet,
  Forecast,
  LeaderboardRow,
  Match,
  PlaceBetBody,
  Standings,
  Wallet,
} from './types';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Turn any thrown value into a user-facing sentence. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return 'Something went wrong.';
}

function friendly(status: number, backendMessage: string): string {
  if (status === 401) return 'Your session expired. Sign in again.';
  if (status === 403) return "You don't have access to that.";
  if (status === 404) return 'Not found.';
  if (status >= 500) return 'The server had a problem. Try again.';
  return backendMessage || `Request failed (${status}).`;
}

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getRuntimeConfig().apiUrl}${path}`, {
    ...init,
    headers: {
      // Raw Cognito ID token (no "Bearer" prefix) — what the deployed authorizer expects.
      Authorization: token,
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let backendMessage = '';
    try {
      const body = await res.json();
      if (body && typeof body.error === 'string') backendMessage = body.error;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, friendly(res.status, backendMessage));
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** REST client bound to the caller's current ID token. */
export function createApi(token: string | undefined) {
  function req<T>(path: string, init?: RequestInit): Promise<T> {
    if (!token) return Promise.reject(new ApiError(401, 'Your session expired. Sign in again.'));
    return request<T>(path, token, init);
  }

  return {
    listMatches: (status?: string) =>
      req<Match[]>(`/matches${status ? `?status=${encodeURIComponent(status)}` : ''}`),
    getStandings: () => req<Standings>('/standings'),
    getForecast: (id: string) => req<Forecast>(`/forecast/${encodeURIComponent(id)}`),
    getWallet: () => req<Wallet>('/wallet'),
    getLeaderboard: () => req<{ leaderboard: LeaderboardRow[] }>('/leaderboard'),
    setDisplayName: (name: string) =>
      req<{ displayName: string }>('/leaderboard/name', {
        method: 'PUT',
        body: JSON.stringify({ name }),
      }),
    listBets: () => req<Bet[]>('/bets'),
    placeBet: (body: PlaceBetBody) =>
      req<Bet>('/bets', { method: 'POST', body: JSON.stringify(body) }),
    askAgent: (message: string, sessionId?: string) =>
      req<AgentReply>('/agent', {
        method: 'POST',
        body: JSON.stringify(sessionId ? { message, sessionId } : { message }),
      }),
  };
}

export type Api = ReturnType<typeof createApi>;
