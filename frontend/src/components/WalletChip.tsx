import { useWallet } from '../state/WalletProvider';
import { formatCoins } from '../lib/format';
import styles from './WalletChip.module.css';

/** Wallet balance chip — fetched on load, updated live from `bets.settled` pushes. */
export function WalletChip() {
  const { balance, loading, error, refresh } = useWallet();

  return (
    <div className={styles.chip} title="Wallet balance">
      <span className={styles.label}>Balance</span>
      {loading ? (
        <span className={`skeleton ${styles.skeleton}`} aria-hidden="true" />
      ) : error ? (
        <button
          type="button"
          className={styles.retry}
          onClick={() => void refresh()}
          title="Balance unavailable — tap to retry"
          aria-label="Balance unavailable — tap to retry"
        >
          !
        </button>
      ) : (
        <span className={`num ${styles.amount}`}>{balance == null ? '—' : formatCoins(balance)}</span>
      )}
    </div>
  );
}
