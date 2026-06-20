import { Outlet } from 'react-router-dom';
import { NavBar } from './NavBar';
import styles from './AppShell.module.css';

export function AppShell() {
  return (
    <div className={styles.app}>
      <NavBar />
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
