import { useCallback, useEffect, useState } from 'react';
import { useApi } from './useApi';
import { errorMessage } from '../lib/api';
import { useSocketMessage, useResyncOnReconnect } from '../realtime/SocketProvider';
import type { Standings } from '../lib/types';

/** Group standings, refetched whenever a match goes final (which changes the tables). */
export function useStandings() {
  const api = useApi();
  const [standings, setStandings] = useState<Standings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setStandings(await api.getStandings());
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
    if (msg.type === 'match.final') void load();
  });

  useResyncOnReconnect(load);

  return { standings, loading, error, reload: load };
}
