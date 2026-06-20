import { useEffect, useRef, useState } from 'react';
import { useApi } from '../hooks/useApi';
import { useWallet } from '../state/WalletProvider';
import { errorMessage } from '../lib/api';
import styles from './Ask.module.css';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text?: string;
  pending?: boolean;
  error?: boolean;
  specialist?: string;
  retryText?: string; // on an errored assistant turn, the prompt to resend
}

const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function Ask() {
  const api = useApi();
  const wallet = useWallet();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);

  const sessionId = useRef<string | undefined>(undefined);
  const idCounter = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  const nextId = () => String(idCounter.current++);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  // Shared request path for an initial send and for a retry of the same prompt.
  async function runRequest(assistantId: string, text: string) {
    setSending(true);
    setMessages((prev) =>
      prev.map((m) =>
        m.id === assistantId ? { ...m, pending: true, error: false, text: undefined } : m,
      ),
    );
    try {
      const reply = await api.askAgent(text, sessionId.current);
      sessionId.current = reply.sessionId; // reuse for conversation continuity
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                pending: false,
                text: reply.response ?? "I don't have an answer for that one.",
                specialist: reply.specialist ?? undefined,
              }
            : m,
        ),
      );
      // The agent can place bets via its tool, so the balance may have changed.
      void wallet.refresh();
    } catch (e) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, pending: false, error: true, text: errorMessage(e), retryText: text }
            : m,
        ),
      );
    } finally {
      setSending(false);
    }
  }

  function send() {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    const assistantId = `a-${nextId()}`;
    setMessages((prev) => [
      ...prev,
      { id: `u-${nextId()}`, role: 'user', text },
      { id: assistantId, role: 'assistant', pending: true },
    ]);
    void runRequest(assistantId, text);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    send();
  }

  return (
    <div className={styles.wrap}>
      <h1 className="page-title">Ask</h1>

      <div className={styles.chat}>
        <div className={styles.messages} aria-live="polite">

          {messages.length === 0 ? (
            <div className={styles.empty}>
              <p className={styles.emptyTitle}>Ask about teams, forecasts, or place a bet.</p>
              <p className={styles.emptyHint}>
                Try “How do Brazil and France compare?” or “Put 50 coins on a draw in the next Spain match.”
              </p>
            </div>
          ) : (
            messages.map((m) =>
              m.role === 'user' ? (
                <div key={m.id} className={`${styles.row} ${styles.rowUser}`}>
                  <div className={`${styles.bubble} ${styles.bubbleUser}`}>{m.text}</div>
                </div>
              ) : (
                <div key={m.id} className={`${styles.row} ${styles.rowBot}`}>
                  <div
                    className={`${styles.bubble} ${styles.bubbleBot} ${m.error ? styles.bubbleError : ''}`}
                  >
                    {m.specialist && !m.error && (
                      <span className={styles.specialist}>{titleCase(m.specialist)}</span>
                    )}
                    {m.pending ? (
                      <Thinking />
                    ) : (
                      <span className={styles.text}>{m.text}</span>
                    )}
                    {m.error && m.retryText && (
                      <button
                        type="button"
                        className={styles.retry}
                        onClick={() => void runRequest(m.id, m.retryText!)}
                        disabled={sending}
                      >
                        Try again
                      </button>
                    )}
                  </div>
                </div>
              ),
            )
          )}
          <div ref={bottomRef} />
        </div>

        <form className={styles.composer} onSubmit={onSubmit}>
          <input
            className={styles.input}
            type="text"
            placeholder="Ask the assistant…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            aria-label="Message"
          />
          <button className="btn btn--accent" type="submit" disabled={sending || !input.trim()}>
            {sending ? 'Thinking…' : 'Send'}
          </button>
        </form>
      </div>
    </div>
  );
}

function Thinking() {
  return (
    <span className={styles.thinking} role="status" aria-label="Thinking">
      <span className={styles.dot} />
      <span className={styles.dot} />
      <span className={styles.dot} />
    </span>
  );
}
