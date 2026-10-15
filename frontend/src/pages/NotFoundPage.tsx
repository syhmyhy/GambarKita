import { CalendarX2, Compass } from "lucide-react";

import { AppShell } from "../components/AppShell";
import { StatusScreen } from "../components/ui/StatusScreen";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

// No link to the home page: guests land here from mistyped links, and the home page is for hosts.
export function NotFoundPage({ kind = "page" }: { kind?: "page" | "event" }) {
  useDocumentTitle(kind === "event" ? "Event not found" : "Page not found");
  return (
    <AppShell homeLink={false}>
      {kind === "event" ? (
        <StatusScreen
          icon={CalendarX2}
          title="Event not found"
          message="This event link doesn't look right. Check the link or scan the QR code again."
        />
      ) : (
        <StatusScreen icon={Compass} title="Page not found" message="The page you're looking for doesn't exist." />
      )}
    </AppShell>
  );
}
