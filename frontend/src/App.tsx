import { lazy, type ReactNode, Suspense } from "react";
import { Route, Routes, useLocation, useParams } from "react-router";

import { PageErrorBoundary } from "./components/PageErrorBoundary";
import { LoadingScreen } from "./components/ui/StatusScreen";
import { EVENT_ID_PATTERN } from "./lib/env";
import { HomePage } from "./pages/HomePage";
import { NotFoundPage } from "./pages/NotFoundPage";

// Each screen is its own chunk: a guest scanning the QR code only downloads the upload page.
const UploadPage = lazy(() => import("./pages/UploadPage"));
const WallPage = lazy(() => import("./pages/WallPage"));
const HostPage = lazy(() => import("./pages/HostPage"));

/** Renders ``children`` only for a well-formed event id in the URL. */
function WithEventId({ render }: { render: (eventId: string) => ReactNode }) {
  const { eventId = "" } = useParams();
  const normalized = eventId.toLowerCase();
  if (!EVENT_ID_PATTERN.test(normalized)) return <NotFoundPage kind="event" />;
  return <>{render(normalized)}</>;
}

export function App() {
  const { pathname } = useLocation();
  return (
    <PageErrorBoundary resetKey={pathname}>
    <Suspense fallback={<LoadingScreen className="min-h-dvh" />}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/e/:eventId" element={<WithEventId render={(id) => <UploadPage key={id} eventId={id} />} />} />
        <Route path="/e/:eventId/wall" element={<WithEventId render={(id) => <WallPage key={id} eventId={id} />} />} />
        <Route path="/e/:eventId/host" element={<WithEventId render={(id) => <HostPage key={id} eventId={id} />} />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
    </PageErrorBoundary>
  );
}
