import { Component, type ErrorInfo, type ReactNode } from 'react';
import { FullScreenStatus } from './FullScreenStatus';

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/**
 * Catches render-time exceptions anywhere in the app so a crash shows a minimal fallback instead of
 * a blank screen. (main.tsx's catch only covers the pre-mount config load.) Standard React API — no
 * library.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('App crashed:', error, info);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <FullScreenStatus
          tone="error"
          title="Something went wrong"
          message="The app hit an unexpected error. Reloading usually fixes it."
          action={{ label: 'Reload', onClick: () => window.location.reload() }}
        />
      );
    }
    return this.props.children;
  }
}
