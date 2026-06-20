import { useCallback, useEffect, useState } from 'react';
import { useApi } from './useApi';
import { errorMessage } from '../lib/api';
import { useSocketMessage, useResyncOnReconnect } from '../realtime/SocketProvider';
import type { LeaderboardRow } from '../lib/types';

/** Tournament standings, fetched once and replaced live from `leaderboard` pushes (full board). */
export function useLeaderboard() {
  const api = useApi();
  const [standings, setStandings] = useState<LeaderboardRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const { leaderboard } = await api.getLeaderboard();
      setStandings(leaderboard);
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
    if (msg.type === 'leaderboard') setStandings(msg.standings);
  });

  useResyncOnReconnect(load);

  return { standings, loading, error, reload: load };
}
