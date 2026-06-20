import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useApi } from '../hooks/useApi';
import { errorMessage } from '../lib/api';
import { useSocketMessage, useResyncOnReconnect } from '../realtime/SocketProvider';

interface WalletCtx {
  balance: number | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const WalletContext = createContext<WalletCtx | null>(null);

/**
 * Holds the caller's wallet balance for the whole app: fetched once on mount, updated live from
 * `bets.settled` pushes, and refreshable after placing a bet. Drives the nav wallet chip.
 */
export function WalletProvider({ children }: { children: React.ReactNode }) {
  const api = useApi();
  const [balance, setBalance] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const wallet = await api.getWallet();
      setBalance(wallet.balance);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useSocketMessage((msg) => {
    if (msg.type !== 'bets.settled') return;
    // The push carries the new balance, except when notify couldn't read it (balance:null) —
    // in that case reconcile from /wallet so the chip doesn't go stale.
    if (typeof msg.balance === 'number') setBalance(msg.balance);
    else void refresh();
  });

  // Re-pull the balance after a reconnect (a bets.settled push may have been missed while down).
  useResyncOnReconnect(refresh);

  const value = useMemo<WalletCtx>(
    () => ({ balance, loading, error, refresh }),
    [balance, loading, error, refresh],
  );
  return <WalletContext.Provider value={value}>{children}</WalletContext.Provider>;
}

export function useWallet(): WalletCtx {
  const ctx = useContext(WalletContext);
  if (!ctx) throw new Error('useWallet must be used inside <WalletProvider>');
  return ctx;
}
