import React, { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import type { DiagnosticSeverity } from '../types';

export class ResilientPanel extends Component<{
  name: string;
  children: ReactNode;
  onDiagnostic: (scope: string, error: unknown, action?: string, severity?: DiagnosticSeverity) => void;
}, { failed: boolean; error?: Error }> {
  state = { failed: false, error: undefined as Error | undefined };

  static getDerivedStateFromError(error: Error) {
    return { failed: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onDiagnostic(
      this.props.name,
      `${error.message}\n${info.componentStack}`,
      'Reset this widget or bypass it and continue elsewhere'
    );
  }

  render() {
    if (this.state.failed) {
      return (
        <section className="panel crash-panel">
          <AlertTriangle size={22} />
          <div>
            <h2>{this.props.name} crashed locally</h2>
            <p>{this.state.error?.message || 'A widget-level render error occurred.'}</p>
            <div className="button-row">
              <button className="fallback-button" onClick={() => this.setState({ failed: false, error: undefined })}>
                Reset local state
              </button>
              <button onClick={() => window.location.hash = '#diagnostics'}>Bypass / inspect diagnostics</button>
            </div>
          </div>
        </section>
      );
    }
    return this.props.children;
  }
}
