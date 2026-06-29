/**
 * ErrorBoundary — catches render errors with retry support.
 * Wraps individual widgets so one crash doesn't take down the whole app.
 */
import { Component, type ReactNode } from "react"
import { AlertTriangle, RefreshCw } from "lucide-react"

interface Props {
  children: ReactNode
  fallbackTitle?: string
}

interface State {
  hasError: boolean
  error: Error | null
  retryCount: number
}

const MAX_RETRIES = 3

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, retryCount: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  handleRetry = () => {
    this.setState(prev => ({
      hasError: false,
      error: null,
      retryCount: prev.retryCount + 1,
    }))
  }

  render() {
    if (this.state.hasError) {
      const exhausted = this.state.retryCount >= MAX_RETRIES
      return (
        <div className="h-full flex items-center justify-center bg-card p-6">
          <div className="flex flex-col items-center gap-3 text-center max-w-xs">
            <div className="rounded-full bg-destructive/10 p-3">
              <AlertTriangle className="h-5 w-5 text-destructive" />
            </div>
            <div>
              <h3 className="text-sm font-medium text-foreground">
                {this.props.fallbackTitle || "组件加载失败"}
              </h3>
              <p className="mt-1 text-xs text-muted-foreground leading-relaxed">
                {exhausted
                  ? "多次重试后仍然失败，请检查控制台日志"
                  : this.state.error?.message || "发生未知错误"}
              </p>
            </div>
            {!exhausted && (
              <button
                onClick={this.handleRetry}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
                重试 ({MAX_RETRIES - this.state.retryCount} 次剩余)
              </button>
            )}
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
