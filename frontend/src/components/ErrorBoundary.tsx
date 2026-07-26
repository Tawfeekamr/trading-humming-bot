import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Confine chart/render crashes to a red inline message instead of a white screen. */
export class ErrorBoundary extends Component<{ label?: string; children: ReactNode }, { err: Error | null }> {
  state: { err: Error | null } = { err: null }
  static getDerivedStateFromError(err: Error) { return { err } }
  componentDidCatch(err: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary:', this.props.label, err, info.componentStack)
  }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: '28px', color: 'var(--ox)', fontFamily: "var(--mono)", fontSize: 13 }}>
          {this.props.label ? `${this.props.label} — ` : ''}{this.state.err.message}
        </div>
      )
    }
    return this.props.children
  }
}
