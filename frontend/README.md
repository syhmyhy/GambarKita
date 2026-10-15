# GambarKita frontend

React 19 + Vite 6 + Tailwind CSS 4 + TypeScript, mobile-first.

| Route | Who | What |
|---|---|---|
| `/` | host | Create an event; find your events again. Guest pages don't link here (no clickable logo or "Go to home" button) |
| `/e/:id` | guests | Take or choose photos, check them in a preview, confirm, and upload them (the QR code points here) |
| `/e/:id/wall` | everyone | Live photo wall: grid, slideshow, lightbox and an on-screen QR code |
| `/e/:id/host` | host | Private dashboard: QR code (download or print), uploads on/off, rename, delete photos, delete the event |

## Development

The backend must be running (see the root [README](../README.md)). Then:

```bash
npm install
npm run dev          # http://localhost:5173
npm run dev:lan      # same, reachable from phones on your Wi-Fi
```

Vite proxies `/api`, `/media` and `/ws` to the API (default `http://127.0.0.1:8000`, override with `VITE_DEV_API_TARGET`), so the browser only ever talks to one origin.

| Command | |
|---|---|
| `npm test` | Unit and component tests (Vitest + Testing Library) |
| `npm run typecheck` | TypeScript, strict |
| `npm run lint` | ESLint, including React hooks rules |
| `npm run build` | Production build into `dist/` |
| `npm run preview` | Serve the production build on http://localhost:4173. Add `http://localhost:4173` to `GK_CORS_ORIGINS` so the live WebSocket's Origin is accepted |

## How it fits together

- `src/lib/`: framework-free logic.
  - `api.ts` is the typed client with the backend's error envelope (`ApiError`).
  - `upload.ts` uploads over XHR so it can report progress.
  - `image.ts` shrinks oversized photos and converts HEIC in the browser, when the browser can decode it.
  - `realtime.ts` is a self-healing WebSocket: backoff, heartbeat, and a reconnect when the device comes back online.
  - `feed.ts` is a pure reducer that merges REST pages with live pushes.
- `src/hooks/useLiveFeed.ts` subscribes first and then loads, resyncing on every reconnect, so no photo is ever missed. It falls back to polling if WebSockets are unavailable.
- **Security:**
  - The host token lives only in `localStorage` on the host's device. It is read from the `#token=` URL fragment, which is never sent to servers, and the fragment is stripped at once.
  - Images only load from server-generated `/media/...` paths (`mediaUrl`).
  - User text is always rendered as text.

## Production

The easiest route is Docker + Caddy: [`deploy/README.md`](../deploy/README.md) builds this app and serves it with HTTPS next to the API. To do it by hand, build with `npm run build` and serve `dist/` from the same origin as the API; the root README has an nginx example. If the API is on a different origin, set `VITE_API_BASE_URL` at build time and add the frontend origin to `GK_CORS_ORIGINS`.
