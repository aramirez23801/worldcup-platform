import styles from './FullScreenStatus.module.css';

type Action = { label: string; onClick: () => void };

/**
 * Centered full-viewport status used for the auth states (redirecting, signing in, errors).
 * The breathing dot is a functional loading cue, not the live pulse — it stays out of the
 * score bug's territory and degrades to static under prefers-reduced-motion.
 */
export function FullScreenStatus({
  title,
  message,
  tone = 'info',
  action,
}: {
  title: string;
  message?: string;
  tone?: 'info' | 'error';
  action?: Action;
}) {
  return (
    <div className={styles.wrap} role={tone === 'error' ? 'alert' : 'status'} aria-live="polite">
      <div className={styles.card}>
        {tone === 'info' && <span className={styles.dot} aria-hidden="true" />}
        <h1 className={styles.title}>{title}</h1>
        {message && <p className={styles.message}>{message}</p>}
        {action && (
          <button className={styles.action} onClick={action.onClick}>
            {action.label}
          </button>
        )}
      </div>
    </div>
  );
}
