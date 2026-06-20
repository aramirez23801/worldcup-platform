import { flagCode } from '../lib/flags';
import styles from './FlagChip.module.css';

type Ring = 'none' | 'win';

/**
 * Circular flag chip: a `flag-icons` square cropped into a circle. The flag is derived from the
 * team NAME, so a resolving knockout slot fills in the right flag automatically. With no team yet
 * (unresolved knockout), a neutral crest is shown instead of a wrong flag.
 */
export function FlagChip({
  team,
  size = 44,
  ring = 'none',
}: {
  team?: string;
  size?: number;
  ring?: Ring;
}) {
  const code = flagCode(team);
  const ringClass = ring === 'win' ? styles.win : '';

  return (
    <span
      className={`${styles.chip} ${ringClass}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {code ? (
        <span className={`fi fi-${code} fis ${styles.flag}`} />
      ) : (
        <svg className={styles.neutral} viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5" opacity="0.6" />
          <path d="M3 12h18M12 3c2.5 2.4 2.5 15.6 0 18M12 3c-2.5 2.4-2.5 15.6 0 18"
            stroke="currentColor" strokeWidth="1.2" opacity="0.45" />
        </svg>
      )}
    </span>
  );
}
