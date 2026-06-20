import { useCallback, useEffect, useState } from 'react';
import { useApi } from './useApi';
import { errorMessage } from '../lib/api';
import { useSocketMessage, useResyncOnReconnect } from '../realtime/SocketProvider';
import type { Match } from '../lib/types';

/**
 * The full fixture list, with live score/status patched in from `match.live` / `match.final`
 * pushes. Knockout slots resolve themselves: when the backend fills teamHome/teamAway, a refetch
 * (reload) or the next render shows the right flags — flags are a pure function of the name.
 */
export function useMatches() {
  const api = useApi();
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const list = await api.listMatches();
      setMatches(list);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  useSocketMessage((msg) => {
    if (msg.type !== 'match.live' && msg.type !== 'match.final') return;
    const nextStatus = msg.type === 'match.live' ? 'LIVE' : 'FINAL';
    setMatches((prev) =>
      prev
        ? prev.map((m) =>
            m.matchId === msg.matchId
              ? { ...m, scoreHome: msg.scoreHome, scoreAway: msg.scoreAway, status: nextStatus }
              : m,
          )
        : prev,
    );
  });

  // After a reconnect, re-pull the list so updates missed while disconnected aren't lost.
  useResyncOnReconnect(load);

  return { matches, loading, error, reload: load };
}
