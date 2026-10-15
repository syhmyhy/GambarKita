import { ArrowRight, Camera, ChevronRight, LayoutDashboard, QrCode, Sparkles } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";

import { AppShell } from "../components/AppShell";
import { Button } from "../components/ui/Button";
import { Card, CardDescription, CardTitle } from "../components/ui/Card";
import { TextField } from "../components/ui/TextField";
import { useToast } from "../components/ui/Toast";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { usePublicConfig } from "../hooks/usePublicConfig";
import { ApiError, api } from "../lib/api";
import { routes } from "../lib/env";
import { describeError } from "../lib/errors";
import { timeAgo } from "../lib/format";
import { hostTokens, hostedEvents } from "../lib/storage";

/** Accept a pasted event link or a bare 12-character code. */
export function parseEventCode(input: string): string | null {
  const value = input.trim().toLowerCase();
  const fromLink = value.match(/\/e\/([a-z0-9]{12})(?:[/?#]|$)/);
  if (fromLink?.[1]) return fromLink[1];
  return /^[a-z0-9]{12}$/.test(value) ? value : null;
}

export function HomePage() {
  useDocumentTitle(null);
  return (
    <AppShell>
      <section className="pt-4 text-center sm:pt-8">
        <p className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-sm font-semibold text-brand-700 ring-1 ring-brand-100 dark:bg-brand-950/50 dark:text-brand-300 dark:ring-brand-900">
          <Sparkles className="size-4" aria-hidden="true" />
          Event photo sharing
        </p>
        <h1 className="mt-5 text-4xl font-extrabold tracking-tight text-balance sm:text-5xl">
          Every guest's photos, <span className="text-brand-600 dark:text-brand-400">on one live wall.</span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-lg text-stone-600 text-pretty dark:text-stone-400">
          Create an event, print the QR code, and watch photos appear the moment guests take them. No app, no sign-up.
        </p>
      </section>

      <CreateEventCard />
      <YourEvents />
      <HowItWorks />
      <JoinEvent />
    </AppShell>
  );
}

function CreateEventCard() {
  const { config } = usePublicConfig();
  const navigate = useNavigate();
  const toast = useToast();
  const [name, setName] = useState("");
  const [creationKey, setCreationKey] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Also shown when the server says a key is needed although /api/config didn't (it may have failed).
  const [needsKey, setNeedsKey] = useState(false);
  const showKey = config.event_creation_requires_key || needsKey;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    setNameError(trimmed ? null : "Give your event a name.");
    setKeyError(null);
    if (!trimmed) return;

    setSubmitting(true);
    try {
      const created = await api.createEvent(trimmed, showKey ? creationKey.trim() : undefined);
      hostTokens.set(created.event.id, created.host_token);
      hostedEvents.add({ id: created.event.id, name: created.event.name, createdAt: created.event.created_at });
      toast.success("Event created", "Share the QR code with your guests.");
      navigate(routes.host(created.event.id), { state: { justCreated: true } });
    } catch (error) {
      if (error instanceof ApiError && error.code === "UNAUTHORIZED") {
        setKeyError(showKey ? "That creation key isn't right." : "This server needs a creation key to create events.");
        setNeedsKey(true);
      } else if (error instanceof ApiError && error.code === "VALIDATION_ERROR") {
        setNameError(describeError(error).message);
      } else {
        toast.fromError(error);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="mt-10 animate-slide-up">
      <CardTitle>Create an event</CardTitle>
      <CardDescription>You'll get a QR code for guests and a private dashboard to manage photos.</CardDescription>
      <form className="mt-5 space-y-4" onSubmit={submit} noValidate>
        <TextField
          label="Event name"
          placeholder="e.g. Aisyah & Daniel's Wedding"
          value={name}
          onChange={(value) => {
            setName(value);
            if (nameError) setNameError(null);
          }}
          maxLength={config.max_event_name_length}
          showCount
          error={nameError}
          autoComplete="off"
          enterKeyHint="go"
        />
        {showKey && (
          <TextField
            label="Creation key"
            type="password"
            value={creationKey}
            onChange={setCreationKey}
            hint="This server only lets people with the key create events."
            error={keyError}
            autoComplete="off"
          />
        )}
        <Button type="submit" size="lg" className="w-full" loading={submitting} icon={<ArrowRight className="size-5" aria-hidden="true" />}>
          Create event
        </Button>
      </form>
    </Card>
  );
}

function YourEvents() {
  const events = useMemo(() => hostedEvents.list().filter((event) => hostTokens.get(event.id)), []);
  if (events.length === 0) return null;
  return (
    <section className="mt-10" aria-labelledby="your-events">
      <h2 id="your-events" className="text-sm font-bold uppercase tracking-wider text-stone-500 dark:text-stone-400">
        Your events
      </h2>
      <ul className="mt-3 divide-y divide-stone-200 overflow-hidden rounded-[var(--radius-card)] bg-white ring-1 ring-stone-200/80 dark:divide-stone-800 dark:bg-stone-900 dark:ring-stone-800">
        {events.map((event) => (
          <li key={event.id}>
            <Link
              to={routes.host(event.id)}
              className="flex items-center gap-3 px-5 py-4 transition hover:bg-stone-50 dark:hover:bg-stone-800/60"
            >
              <LayoutDashboard className="size-5 shrink-0 text-brand-600 dark:text-brand-400" aria-hidden="true" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-semibold">{event.name}</span>
                <span className="block text-sm text-stone-500 dark:text-stone-400">Created {timeAgo(event.createdAt)}</span>
              </span>
              <ChevronRight className="size-5 text-stone-400" aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

const STEPS = [
  { icon: Sparkles, title: "Create", text: "Name your event and get a unique QR code in seconds." },
  { icon: QrCode, title: "Share", text: "Print it or put it on screen. Guests scan it with their phone camera." },
  { icon: Camera, title: "Watch", text: "Photos appear instantly on the live wall for everyone to enjoy." },
];

function HowItWorks() {
  return (
    <section className="mt-14" aria-labelledby="how-it-works">
      <h2 id="how-it-works" className="text-center text-2xl font-bold tracking-tight">
        How it works
      </h2>
      <ol className="mt-6 grid gap-4 sm:grid-cols-3">
        {STEPS.map(({ icon: Icon, title, text }, index) => (
          <li key={title} className="rounded-[var(--radius-card)] bg-white p-5 ring-1 ring-stone-200/80 dark:bg-stone-900 dark:ring-stone-800">
            <div className="flex items-center gap-3">
              <span className="grid size-10 place-items-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-400">
                <Icon className="size-5" aria-hidden="true" />
              </span>
              <span className="text-sm font-bold text-stone-500 dark:text-stone-400">Step {index + 1}</span>
            </div>
            <h3 className="mt-4 font-bold">{title}</h3>
            <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">{text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function JoinEvent() {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <Card className="mt-14">
      <CardTitle>Joining an event?</CardTitle>
      <CardDescription>Scan the QR code with your phone's camera, or enter the event link or code here.</CardDescription>
      <form
        className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start"
        onSubmit={(event) => {
          event.preventDefault();
          const eventId = parseEventCode(code);
          if (!eventId) {
            setError("That doesn't look like an event link or code.");
            return;
          }
          navigate(routes.upload(eventId));
        }}
      >
        <TextField
          className="flex-1"
          label="Event link or code"
          placeholder="e.g. k3x9p2m7q1ab"
          value={code}
          onChange={(value) => {
            setCode(value);
            setError(null);
          }}
          error={error}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
        />
        <Button type="submit" variant="secondary" size="md" className="sm:mt-7">
          Join
        </Button>
      </form>
    </Card>
  );
}
