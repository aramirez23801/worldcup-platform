import { Routes, Route, Navigate } from 'react-router-dom';
import Callback from './auth/Callback';
import { AuthGate } from './auth/AuthGate';
import { AppShell } from './components/AppShell';
import { SocketProvider } from './realtime/SocketProvider';
import { WalletProvider } from './state/WalletProvider';
import Home from './pages/Home';
import Matches from './pages/Matches';
import Bet from './pages/Bet';
import Ask from './pages/Ask';
import Leaderboard from './pages/Leaderboard';

/** Auth gate → one WebSocket → wallet state → the nav shell. Providers run only once signed in. */
function ProtectedShell() {
  return (
    <AuthGate>
      <SocketProvider>
        <WalletProvider>
          <AppShell />
        </WalletProvider>
      </SocketProvider>
    </AuthGate>
  );
}

export default function App() {
  return (
    <Routes>
      {/* OIDC redirect target — completes sign-in, then routes Home. */}
      <Route path="/callback" element={<Callback />} />

      {/* Everything else is gated on auth and wrapped in the nav shell. */}
      <Route element={<ProtectedShell />}>
        <Route path="/" element={<Home />} />
        <Route path="/matches" element={<Matches />} />
        <Route path="/bet" element={<Bet />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/leaderboard" element={<Leaderboard />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
