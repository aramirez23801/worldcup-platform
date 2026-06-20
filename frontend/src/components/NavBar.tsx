import { NavLink } from 'react-router-dom';
import { WalletChip } from './WalletChip';
import { ProfileMenu } from './ProfileMenu';
import styles from './NavBar.module.css';

const LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/matches', label: 'Matches', end: false },
  { to: '/bet', label: 'Bet', end: false },
  { to: '/ask', label: 'Ask', end: false },
  { to: '/leaderboard', label: 'Leaderboard', end: false },
];

export function NavBar() {
  return (
    <header className={styles.bar}>
      <div className={styles.inner}>
        <NavLink to="/" className={styles.brand} aria-label="World Cup — Home">
          <span className={styles.wordmark}>World Cup</span>
        </NavLink>

        <nav className={styles.nav} aria-label="Primary">
          {LINKS.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                isActive ? `${styles.link} ${styles.active}` : styles.link
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>

        <div className={styles.right}>
          <WalletChip />
          <ProfileMenu />
        </div>
      </div>
    </header>
  );
}
