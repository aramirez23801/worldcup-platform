import React from 'react';
import ReactDOM from 'react-dom/client';
import { AuthProvider } from 'react-oidc-context';
import { BrowserRouter } from 'react-router-dom';
import { createOidcConfig } from './auth/oidcConfig';
import { consumePendingSignOut } from './auth/signout';
import { loadRuntimeConfig } from './lib/runtimeConfig';
import { ErrorBoundary } from './components/ErrorBoundary';
import App from './App';

// Self-hosted fonts (bundled via @fontsource — no external font CDN).
import '@fontsource/saira-semi-condensed/600.css';
import '@fontsource/saira-semi-condensed/700.css';
import '@fontsource/saira-semi-condensed/800.css';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-mono/500.css';
// National flags, bundled locally (no external flag CDN).
import 'flag-icons/css/flag-icons.min.css';

import './styles/tokens.css';
import './styles/global.css';

// Returning from Cognito /logout? Purge the stale local session before React mounts, so the app
// boots unauthenticated and redirects to Hosted UI (single-click logout, no race). Must run
// before render — kept here at the very start of the startup order.
consumePendingSignOut();

async function bootstrap() {
  // Resolve runtime config before mounting: the OIDC AuthProvider's authority/client_id come from
  // it (env vars in dev, /config.json in prod).
  await loadRuntimeConfig();

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <AuthProvider {...createOidcConfig()}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AuthProvider>
      </ErrorBoundary>
    </React.StrictMode>,
  );
}

void bootstrap().catch((err: unknown) => {
  console.error(err);
  const root = document.getElementById('root');
  if (!root) return;
  const message = err instanceof Error ? err.message : 'Failed to start the app.';
  // Static markup only — no interpolation into innerHTML; the dynamic message goes in via textContent.
  root.innerHTML =
    `<div style="min-height:100vh;display:grid;place-items:center;padding:24px;` +
    `font-family:system-ui,sans-serif;color:#eef2fb;background:#0a0f1c;text-align:center">` +
    `<div><h1 style="font-size:20px;margin:0 0 8px">Couldn't start the app</h1>` +
    `<p id="wc-fatal-msg" style="color:#8e9cb6;margin:0"></p></div></div>`;
  const msgEl = root.querySelector('#wc-fatal-msg');
  if (msgEl) msgEl.textContent = message;
});
