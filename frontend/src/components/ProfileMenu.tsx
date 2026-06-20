import { useEffect, useRef, useState } from 'react';
import { useAuth } from 'react-oidc-context';
import { SIGNOUT_KEY } from '../auth/signout';
import { getRuntimeConfig } from '../lib/runtimeConfig';
import styles from './ProfileMenu.module.css';

/** Profile icon → small menu containing only "Log out" (§4). */
export function ProfileMenu() {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const firstItemRef = useRef<HTMLButtonElement>(null);
  const prevOpen = useRef(false);

  const email = auth.user?.profile.email ?? '';
  const initial = (email || '?').charAt(0).toUpperCase();

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Keyboard a11y: move focus into the menu on open; return it to the trigger on close.
  useEffect(() => {
    if (open && !prevOpen.current) firstItemRef.current?.focus();
    else if (!open && prevOpen.current) triggerRef.current?.focus();
    prevOpen.current = open;
  }, [open]);

  function logout() {
    const { cognitoDomain: domain, cognitoClientId: clientId } = getRuntimeConfig();
    // logout_uri = origin root (no trailing slash) — the registered sign-out URL.
    const logoutUrl =
      `${domain}/logout?client_id=${clientId}&logout_uri=${encodeURIComponent(window.location.origin)}`;

    // Persist the intent, then leave immediately. The stale local tokens are purged on the next app
    // load (main.tsx) — nothing async or state-mutating runs before the navigation, so no re-render
    // can race a sign-in redirect ahead of /logout.
    try {
      sessionStorage.setItem(SIGNOUT_KEY, '1');
    } catch {
      /* sessionStorage unavailable — non-fatal */
    }
    window.location.assign(logoutUrl);
  }

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.avatar} aria-hidden="true">
          {initial}
        </span>
      </button>

      {open && (
        <div className={styles.menu} role="menu">
          {email && <div className={styles.email} title={email}>{email}</div>}
          <button
            ref={firstItemRef}
            type="button"
            className={styles.item}
            role="menuitem"
            onClick={logout}
          >
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
