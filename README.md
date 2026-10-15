# GambarKita: QR Code Event Photo Sharing

Guests scan a QR code at an event, snap or pick a photo on their phone, confirm it, and it shows up on a live photo wall straight away, with no app and no sign-up.

| Layer     | Stack                                                                  |
|-----------|------------------------------------------------------------------------|
| Backend   | Python 3.12+, FastAPI, Starlette, Pillow, SQLite (metadata), slowapi   |
| Real-time | Native FastAPI WebSockets (per-event broadcast)                        |
| Storage   | Local disk, `backend/storage/events/{event_id}/`                       |
| Frontend  | React 19, Vite 6, Tailwind CSS 4, TypeScript, React Router            |

```
GambarKita/
├── backend/
│   ├── app/
│   │   ├── api/            # routers (events, photos, system, websocket) + dependencies
│   │   ├── core/           # config, errors, logging, rate limiting, security primitives
│   │   ├── middleware/     # body-size limit, security headers, request id, catch-all errors
│   │   ├── services/       # image pipeline, storage, database, realtime, event/photo logic
│   │   ├── media.py        # hardened static-file mount for photos
│   │   └── main.py         # application factory
│   ├── tests/              # pytest suite
│   ├── run.py              # hardened uvicorn launcher
│   ├── requirements.txt / requirements-dev.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── lib/            # API client, uploads (with progress), live socket, photo feed, image prep
    │   ├── hooks/          # useLiveFeed, usePublicConfig, ...
    │   ├── components/     # design system (ui/) + upload, wall and host components
    │   └── pages/          # Home, Upload (guests), Wall (live), Host (dashboard)
    └── vite.config.ts      # dev proxy for /api, /media and /ws
```

---

## Quick start

### 1. Prerequisites

- **Python 3.12 or newer**: `python --version`
- **Node.js 20.12 or newer** for the frontend: `node --version` (Node 22 LTS recommended: Node 20 is end-of-life. With nvm: `nvm install 22` then `nvm use 22`)

### 2. Backend: virtual environment and dependencies

**Windows (PowerShell)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -r requirements-dev.txt   # runtime + test dependencies (use requirements.txt in production)
Copy-Item .env.example .env           # optional: adjust settings
```

**macOS / Linux**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
# Optional, enables the libmagic cross-check: sudo apt install libmagic1  (macOS: brew install libmagic)
```

### 3. Run the API

```bash
python run.py
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger UI): http://127.0.0.1:8000/api/docs
- Health check: http://127.0.0.1:8000/api/health

`run.py` starts uvicorn with hardened settings (small WebSocket frame limit, proxy-header trust restricted, no `Server` header). For auto-reload during development set `GK_RELOAD=true` in `.env`, or run uvicorn directly:

```bash
uvicorn app.main:create_app --factory --reload
```

> **Run a single worker process.** Live-update fan-out and rate-limit counters live in process memory.

### 4. Run the tests

```bash
python -m pytest
```

### 5. Run the frontend (second terminal)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Open http://localhost:5173, create an event, and open the live wall. The dev server forwards `/api`, `/media` and `/ws` to the backend, so both run side by side. Frontend tests: `npm test`. See [frontend/README.md](frontend/README.md).

### Testing with real phones on your Wi-Fi

Phones can't reach `localhost` on your computer. Find your computer's LAN IP (for example `192.168.1.20`), then:

1. In `backend/.env`, point the QR codes at an address phones can reach, and restart the API:
   ```ini
   GK_PUBLIC_APP_URL=http://192.168.1.20:5173
   ```
2. Start the frontend with `npm run dev:lan` (instead of `npm run dev`). Phones only talk to Vite, which forwards to the API, so the API itself can stay on `127.0.0.1`. Vite should print a `Network: http://192.168.1.20:5173/` line; without it, the LAN address is refused, even in a browser on the same computer.
3. On Windows, set your Wi-Fi to **Private network** (Settings → Network & internet → Wi-Fi → your network). On a *Public* network the firewall blocks phones. If Windows asks whether to allow Node.js, click **Allow**.
4. Open the host dashboard via `http://192.168.1.20:5173` and scan the QR code with a phone.

Phone browsers only allow live camera capture on HTTPS pages. Over plain `http://` to a LAN IP the photo picker still works. For the full camera experience, use HTTPS (for example a tunnel or a local reverse proxy with a certificate).

---

## How it works

1. A host creates an event (`POST /api/events`). The API returns a random **event ID**, the guest **upload URL** (to encode in the QR code), the **wall URL**, and a one-time **host token** for moderation.
2. Guests open the upload URL, pick photos, check them in a preview and confirm, and only then does each one upload (`POST /api/events/{id}/photos`). Guest pages have no link to the home page, which is for hosts.
3. The backend validates, sanitises and stores the photo, records it in SQLite, and broadcasts `photo.created` to every WebSocket subscribed to that event.
4. The wall (`/ws/events/{id}`) prepends the new photo without a page refresh.

## Security design

| Requirement | Implementation |
|---|---|
| **File type validation (never trust extensions)** | Magic-byte sniffing for JPEG / PNG / WebP (`services/image_processing.py`). Pillow may then use **only** that format's decoder (`Image.open(..., formats=[...])`), which blocks decoder-confusion attacks, and the image must fully decode. The client filename and `Content-Type` are ignored. Camera JPEGs with multi-picture (MPF) data are accepted as JPEG. |
| **Sanitised output** | Every photo is **re-encoded**, and **no byte of the upload is copied**. This strips EXIF (including **GPS location**), XMP, comments, ICC profiles and appended or polyglot payloads (e.g. JPEG + HTML/PHP/ZIP, or a ZIP disguised as an ICC profile). EXIF orientation is applied first, and pixels are converted to sRGB using the embedded profile. |
| **libmagic** | Optional second opinion on Linux/macOS (`GK_LIBMAGIC=auto`). It is never loaded on Windows, where `python-magic` can hard-crash the interpreter by loading Git's MSYS DLL. The built-in checks are authoritative on every platform. |
| **Size limits / DoS** | An ASGI middleware rejects bodies over the limit **before reading them** (declared `Content-Length`) or **while streaming** (chunked). The endpoint re-checks the file size (10 MiB default). Dimensions are read from the file header (WebP parsed by hand) and checked **before any pixel buffer exists**, with a lower cap for PNG/WebP, whose tiny files can declare huge canvases. Decoding is admitted against a **pixel budget** plus a job cap, which bounds RAM and CPU. The *stored* copy is size-capped too, so a 30 KB input cannot become a 12 MB file. The per-event photo count is capped, and uploads are refused when free disk space runs low. |
| **Secure file naming** | Stored names are `secrets.token_hex(16)` plus the extension of the **detected** format. Files are created with `O_CREAT \| O_EXCL` (never overwrite) and `O_NOFOLLOW` where supported. |
| **Path traversal** | Every path segment must match a strict allow-list (no separators, `..`, drive letters, NUL or Windows device names). The resolved path must stay strictly inside `storage/events`. The media mount serves only `<event>/(photos\|thumbs)/<32-hex>.(jpg\|png\|webp)` and refuses non-canonical request paths. The SQLite file lives outside the served tree. |
| **Safe serving** | Fixed `Content-Type` per extension, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, immutable caching. |
| **CORS** | Explicit origin allow-list (the configured frontend origin is added automatically), no credentials, limited methods and headers. |
| **Rate limiting** | slowapi per endpoint and per client network (IPv4 address, or IPv6 **/64**, so one host can't rotate addresses) on event creation, uploads, reads and host actions. Counters expire, so memory stays bounded. WebSockets add a connection-attempt limit, caps per network, per event and globally, and an inbound message budget per connection. Defaults are sized for a venue where every guest shares one Wi-Fi IP. |
| **WebSocket hijacking and flooding** | `Origin` is checked against the allow-list (browsers don't apply CORS to WebSockets). Each subscriber has a bounded send queue, and slow clients are disconnected so they can't stall broadcasts. Per-event slots are **reserved for the host's wall display**, so anonymous viewers can't lock the host out. |
| **Deletion really deletes** | A host delete commits a **pending-deletion record in the same database transaction**, so the intent survives crashes, restarts and file locks. Walls are notified immediately. The media is **tombstoned**: it stops being served at once, even while Windows keeps a file locked for an in-flight download. A background janitor retries with backoff until the file is gone. Media is cached `private`, so no shared cache or CDN keeps serving deleted photos. |
| **No guessing, no data loss** | Every file's lifecycle is **journaled in SQLite**. An upload intent is recorded before a photo's files are written and removed in the same transaction that inserts the photo. At startup, the only files ever removed are those named by an intent that never committed, i.e. an interrupted upload. Nothing is deleted just because the database doesn't know about it, so restoring an older backup, losing the database or renaming files can never wipe photos. Event directories without a record (or all of them, if the database is new) are kept on disk but not served. Links and junctions are never followed, and housekeeping errors never stop the server from starting. |
| **Single instance** | An OS-level lock on `storage/db/gambarkita.lock` stops a second server process (a double start, or a service plus a manual launch) from using the same storage. |
| **Host authorisation** | 256-bit host token, stored only as a SHA-256 hash and compared in constant time. The host link carries it in the URL **fragment** (`#token=…`), which browsers never send to servers. When it is sent as a WebSocket subprotocol, it is redacted from all server logs, including uvicorn's DEBUG handshake logging. Photo deletion is scoped to its event, which prevents IDOR. |
| **Event IDs** | 12 characters from a CSPRNG (~62 bits). They are unguessable, so holding the link is what grants access. Optional `GK_EVENT_CREATION_KEY` restricts who can create events. |
| **Headers and errors** | `nosniff`, `X-Frame-Options: DENY`, a strict CSP, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on API responses. Optional HSTS and trusted-host checks. Every error, including 413, 429 and 500, returns the same JSON envelope with a request ID and CORS headers. Stack traces are never exposed. |

## API reference

All errors share one shape:

```json
{ "error": { "code": "FILE_TOO_LARGE", "message": "Photos must be 10 MB or smaller.", "details": {"max_bytes": 10485760}, "request_id": "4f1c…" } }
```

| Method & path | Auth | Description |
|---|---|---|
| `GET /api/health` | none | Liveness plus database check |
| `GET /api/config` | none | Upload limits and allowed types (the frontend mirrors these for client-side validation) |
| `POST /api/events` | optional creation key | Create an event: `{"name": "..."}` returns `{event, host_token, host_url}` |
| `GET /api/events/{id}` | none | Event details, photo count and links |
| `PATCH /api/events/{id}` | host | Rename or open/close uploads: `{"name"?, "uploads_open"?}` |
| `DELETE /api/events/{id}` | host | Delete the event and every photo |
| `POST /api/events/{id}/host/verify` | host | Check a host token (204 or 401) |
| `POST /api/events/{id}/host/rotate` | creation key | Server admin only: issue a **new** host token and link for an event whose host lost theirs; the old token stops working. Disabled unless `GK_EVENT_CREATION_KEY` is set |
| `GET /api/events/{id}/photos?limit=50&cursor=` | none | Photos, newest first; follow `next_cursor` for older pages |
| `POST /api/events/{id}/photos` | none | `multipart/form-data`: `file` (required), `uploader_name`, `caption` (optional). Optional header `X-Guest-Key` (see below) |
| `GET /api/events/{id}/guest-quota` | `X-Guest-Key` | This guest's allowance: `{limit, used, remaining}` (`limit`/`remaining` are null without a per-guest limit) |
| `DELETE /api/events/{id}/photos/{photo_id}` | host | Remove a photo |
| `GET /media/events/{id}/photos/{file}` | none | Display image (thumbnails under `/thumbs/`) |
| `WS /ws/events/{id}` | none | Live updates (see below) |

Host endpoints expect `Authorization: Bearer <host_token>`.

**Per-guest limit.** With `GK_MAX_PHOTOS_PER_GUEST` above 0, each guest may add that many photos per event; more get `409 GUEST_PHOTO_LIMIT`. A guest is identified by `X-Guest-Key`, a random 32-128 character key (`A-Z a-z 0-9 - _`) the browser makes per event and keeps in `localStorage`; a malformed key is a `422`. While the limit is on, the server stores only `SHA-256(event_id:key)` with each photo, never returns it, and keeps it out of Caddy's logs. While it is off, the key is ignored and nothing about the guest is stored, so the limit counts from the moment it is switched on. Uploads without a key are accepted and not counted (pages opened before the limit existed don't send one; refusing them would gain nothing, as any client can make up a key). The limit is checked before the body is read and again inside the insert transaction, so parallel uploads can't overshoot it. Photos the host deletes free their slot. Clearing site data or switching browsers yields a fresh key, so this curbs camera-roll dumps; it is not a hard quota.

### WebSocket protocol

JSON text frames. Server to client: `hello`, `photo.created`, `photo.deleted`, `event.updated`, `event.deleted`, `pong`. The client may send `{"type": "ping"}` as a heartbeat. Connect first, then fetch the photo list and de-duplicate by `id`, so nothing uploaded in between is missed.

Clients should offer the subprotocol `gambarkita.v1`. The host's wall display may also offer `gambarkita.host.<host_token>`, e.g. `new WebSocket(url, ["gambarkita.v1", "gambarkita.host." + token])`, to use the connection slots reserved for hosts. The token travels in a handshake header, never in the URL.

| Close code | Meaning | Client action |
|---|---|---|
| 4404 | Event not found | Stop |
| 4410 | Event deleted | Stop |
| 4429 / 1013 | Rate or capacity limited | Retry with backoff |
| 1008 / 1009 / 1003 | Policy violation, frame too big, binary frame | Fix the client |

## Configuration

All settings are environment variables prefixed `GK_`, which can also go in `backend/.env`. See [`backend/.env.example`](backend/.env.example) for the full, commented list: upload and image limits, rate limits, CORS, WebSocket caps, the storage path and more.

## Production checklist

**Recommended: Docker + Caddy.** [`deploy/`](deploy/) runs the API and the website behind Caddy, which gets and renews a free HTTPS certificate automatically. All the settings below are already applied there. Follow the step-by-step guide in [`deploy/README.md`](deploy/README.md):

```bash
cd deploy
cp .env.example .env     # set SITE_ADDRESS, PUBLIC_URL and GK_EVENT_CREATION_KEY
docker compose up -d --build
```

**Manual alternative.** Build the frontend (`cd frontend && npm run build`) and serve `frontend/dist/` and the API from **one origin** behind a TLS reverse proxy. Example nginx site:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name photos.example.com;
    # ssl_certificate / ssl_certificate_key ...

    client_max_body_size 11m;                  # slightly above GK_MAX_UPLOAD_BYTES

    root /srv/gambarkita/frontend/dist;
    location / { try_files $uri /index.html; } # SPA routes (/e/...)
    location /assets/ { add_header Cache-Control "public, max-age=31536000, immutable"; }

    location ~ ^/(api|media)/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 1h;
    }

    add_header Content-Security-Policy "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Permissions-Policy "camera=(self), microphone=(), geolocation=()" always;
}
```

With the API on `127.0.0.1:8000` and nginx on the same host, uvicorn's default trust of `127.0.0.1` for `X-Forwarded-For` is exactly right. Set `GK_PUBLIC_APP_URL=https://photos.example.com`.


- Put a TLS-terminating reverse proxy (nginx, Caddy) in front. Set `GK_FORWARDED_ALLOW_IPS` to the proxy's IP or CIDR (for example the Docker network) so rate limits see the real client IP. **Never use `*`**: uvicorn then trusts the *left-most* `X-Forwarded-For` entry, which the client controls, because nginx `$proxy_add_x_forwarded_for` and HAProxy `option forwardfor` append to it. Anyone could then forge an IP per request and bypass every rate limit. The server logs a warning if it sees `*`.
- Proxy WebSockets (`Upgrade`/`Connection` headers) and set the proxy's body limit slightly above `GK_MAX_UPLOAD_BYTES` (for example nginx `client_max_body_size 11m;`).
- Set `GK_ENVIRONMENT=production` (disables `/api/docs`), `GK_PUBLIC_APP_URL`, `GK_CORS_ORIGINS`, `GK_TRUSTED_HOSTS`, `GK_HSTS_ENABLED=true`, and preferably `GK_EVENT_CREATION_KEY`.
- Run **one** worker process. Horizontal scaling needs a shared pub/sub (such as Redis) for broadcasts and a shared rate-limit store. `run.py` sets a 10 s graceful-shutdown timeout. Pass `--timeout-graceful-shutdown 10` if you launch uvicorn yourself.
- Validate file size and type in the frontend before uploading. When a body is too large the server answers 413 and closes the connection early, and some browsers then report a network error instead of the response.
- Guests often share one public IP on venue Wi-Fi. Tune `GK_RATE_LIMIT_UPLOAD` and `GK_WS_MAX_CONNECTIONS_PER_IP` for your crowd size.
- Back up `backend/storage/` (with Docker: the `gambarkita_gambarkita-data` volume, see [`deploy/README.md`](deploy/README.md#backups)): it holds both the photos and the SQLite database.
- **A host lost their link.** The server only stores a hash of the host token, so it cannot be shown again. As the server admin you can issue a new one with your creation key; the old token stops working at once. Send the host the `host_url` from the response:

  ```bash
  curl -X POST https://photos.example.com/api/events/<event-id>/host/rotate -H "X-Event-Creation-Key: $GK_EVENT_CREATION_KEY"
  ```
