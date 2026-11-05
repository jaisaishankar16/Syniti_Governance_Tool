import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertCircle, RotateCcw } from 'lucide-react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('Unhandled React render error in ErrorBoundary:', error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-[#F8F9FB] flex items-center justify-center p-4">
          <div className="card-base p-8 max-w-lg w-full text-center space-y-4">
            <div className="w-12 h-12 bg-red-100 text-red-600 rounded-full mx-auto flex items-center justify-center">
              <AlertCircle className="w-6 h-6" />
            </div>
            <h1 className="text-xl font-bold text-[#111827]">Application Encountered an Error</h1>
            <p className="text-xs sm:text-sm text-[#6B7280]">
              An unexpected error occurred during rendering. Please reload the application.
            </p>
            <div className="pt-2">
              <button
                type="button"
                onClick={this.handleReset}
                className="inline-flex items-center space-x-2 px-4 py-2 gradient-brand text-white text-sm font-semibold rounded-lg hover:opacity-90 shadow-sm"
              >
                <RotateCcw className="w-4 h-4" />
                <span>Reload Application</span>
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
