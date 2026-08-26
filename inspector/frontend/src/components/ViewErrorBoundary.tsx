import { Component, type ErrorInfo, type ReactNode } from 'react';

import { EmptyState } from './EmptyState';

interface Props {
  /** Remount key: changing it clears a previous failure (e.g. on view switch). */
  resetKey?: string | number;
  children: ReactNode;
}

interface State {
  message: string | null;
}

/**
 * Owns the old mount() rejection state: any render or fetch error inside a view
 * shows one honest empty state instead of a blank page.
 */
export class ViewErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(error: unknown): State {
    return { message: String((error as Error)?.message || error) };
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.message !== null) {
      this.setState({ message: null });
    }
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error('inspector: view failed', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.message !== null) {
      return <EmptyState icon="🚫" title="Failed to load view" body={this.state.message} />;
    }
    return this.props.children;
  }
}
