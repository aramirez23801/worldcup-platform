import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useAuth } from 'react-oidc-context';
import { getRuntimeConfig } from '../lib/runtimeConfig';
import type { WsMessage } from '../lib/types';

type Listener = (msg: WsMessage) => void;
type Status = 'connecting' | 'open' | 'closed';

interface SocketCtx {
  subscribe: (fn: Listener) => () => void;
  status: Status;
}

const SocketContext = createContext<SocketCtx | null>(null);

const PING_MS = 240_000; // 4 min — comfortably inside the server's ~10 min idle close.
const MAX_BACKOFF_MS = 15_000;

/**
 * Single WebSocket for the authenticated app. Connects to `${VITE_WS_URL}?token=<idToken>`
 * (the handshake can't set headers), keeps it alive with periodic pings, reconnects with backoff
 * on close, and fans incoming messages out to subscribers. Live score and leaderboard pushes flow
 * through here.
 */
export function SocketProvider({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  const token = auth.user?.id_token;
  const tokenRef = useRef(token);
  tokenRef.current = token; // always the freshest token, for reconnects
  const listeners = useRef<Set<Listener>>(new Set());
  const [status, setStatus] = useState<Status>('connecting');

  useEffect(() => {
    if (!token) return;

    let closedByUs = false;
    let retry = 0;
    let pingTimer: ReturnType<typeof setInterval> | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let ws: WebSocket | null = null;

    function connect() {
      // Reconnect with the freshest token: a drop after a failed silent-renew must not loop the
      // handshake with the (now-expired) token captured when the effect ran.
      const current = tokenRef.current;
      if (!current) return;
      setStatus('connecting');
      ws = new WebSocket(`${getRuntimeConfig().wsUrl}?token=${encodeURIComponent(current)}`);

      ws.onopen = () => {
        retry = 0;
        setStatus('open');
        pingTimer = setInterval(() => {
          if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'ping' }));
        }, PING_MS);
      };

      ws.onmessage = (ev) => {
        let data: unknown;
        try {
          data = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (!data || typeof data !== 'object') return;
        const msg = data as Record<string, unknown>;
        if (msg.action === 'pong') return; // keepalive ack
        if (typeof msg.type !== 'string') return;
        listeners.current.forEach((fn) => fn(msg as unknown as WsMessage));
      };

      ws.onclose = () => {
        if (pingTimer) clearInterval(pingTimer);
        setStatus('closed');
        if (closedByUs) return;
        const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** retry);
        retry += 1;
        reconnectTimer = setTimeout(connect, delay);
      };

      ws.onerror = () => ws?.close();
    }

    connect();

    return () => {
      closedByUs = true;
      if (pingTimer) clearInterval(pingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [token]);

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  const value = useMemo<SocketCtx>(() => ({ subscribe, status }), [subscribe, status]);
  return <SocketContext.Provider value={value}>{children}</SocketContext.Provider>;
}

function useSocket(): SocketCtx {
  const ctx = useContext(SocketContext);
  if (!ctx) throw new Error('useSocket must be used inside <SocketProvider>');
  return ctx;
}

/** Live connection status, for a small indicator. */
export function useSocketStatus(): Status {
  return useSocket().status;
}

/** Subscribe to every WebSocket message. The handler may change freely between renders. */
export function useSocketMessage(handler: Listener): void {
  const { subscribe } = useSocket();
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => subscribe((msg) => ref.current(msg)), [subscribe]);
}

/**
 * Refetch on reconnect: calls `reload` whenever the socket comes back up after having been up once
 * before (a network drop or token-renewal reconnect), so updates pushed during the disconnected
 * window aren't lost. The first connection does not trigger it — consumers already fetch on mount.
 */
export function useResyncOnReconnect(reload: () => void): void {
  const status = useSocketStatus();
  const reloadRef = useRef(reload);
  reloadRef.current = reload;
  const hasOpened = useRef(false);
  useEffect(() => {
    if (status !== 'open') return;
    if (hasOpened.current) reloadRef.current();
    else hasOpened.current = true;
  }, [status]);
}
