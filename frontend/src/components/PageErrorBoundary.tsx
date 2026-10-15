import { WifiOff } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "./ui/Button";
import { StatusScreen } from "./ui/StatusScreen";

interface State {
  failed: boolean;
}

/**
 * Last line of defence for a render error or a page chunk that failed to load
 * (flaky venue Wi-Fi, captive portals, a redeploy while a tab was open): show
 * a way out instead of a blank page.
 */
export class PageErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error("Page failed to render", error, info.componentStack);
  }

  componentDidUpdate(previous: { resetKey?: string }): void {
    // Navigating elsewhere gives the new page a fresh chance.
    if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <StatusScreen
          icon={WifiOff}
          tone="danger"
          title="Couldn't load this page"
          message="Check your connection and try again."
          actions={<Button onClick={() => window.location.reload()}>Reload</Button>}
        />
      </div>
    );
  }
}
