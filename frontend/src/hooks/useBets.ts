import { useCallback, useEffect, useState } from 'react';
import { useApi } from './useApi';
import { errorMessage } from '../lib/api';
import { useSocketMessage, useResyncOnReconnect } from '../realtime/SocketProvider';
import type { Bet } from '../lib/types';

/** The caller's bets (newest first), reloaded live when a `bets.settled` push arrives. */
export function useBets() {
  const api = useApi();
  const [bets, setBets] = useState<Bet[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setBets(await api.listBets());
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
    if (msg.type === 'bets.settled') void load();
  });

  useResyncOnReconnect(load);

  return { bets, loading, error, reload: load };
}
