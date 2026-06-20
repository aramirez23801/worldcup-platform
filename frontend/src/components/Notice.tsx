import styles from './Notice.module.css';

/** Centered status block for empty / error states. Copy gives direction, not mood. */
export function Notice({
  title,
  message,
  action,
  tone = 'info',
}: {
  title: string;
  message?: string;
  action?: { label: string; onClick: () => void };
  tone?: 'info' | 'error';
}) {
  return (
    <div
      className={`${styles.notice} ${tone === 'error' ? styles.error : ''}`}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <h2 className={styles.title}>{title}</h2>
      {message && <p className={styles.message}>{message}</p>}
      {action && (
        <button className="btn btn--ghost" onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  );
}
