# Deploying GambarKita on your home server

```
Guests' phones ──https──▶ rumahoren.asuscomm.com ──▶ ASUS router (ports 80/443 forwarded)
                                                        │
                                                        ▼
                                  Linux server (Docker):  Caddy ──▶ FastAPI
                                                          (HTTPS,     (photos in a
                                                           website)    Docker volume)
```

Caddy gets a free **HTTPS certificate** from Let's Encrypt automatically and renews it. Docker starts everything again by itself after a reboot or power cut.

---

## 0. Check once, before anything else

1. **You need a real public IP.**
   - Open your router page (http://router.asus.com) and note the **WAN IP**.
   - On a phone connected to your home Wi-Fi, open https://api.ipify.org.
   - Compare the two:

     | Router WAN IP | What it means | What to do |
     |---|---|---|
     | Same as api.ipify.org | 👍 Good | Continue |
     | `100.64.x.x` – `100.127.x.x` | **CGNAT**: your ISP shares one IP between many homes, so port forwarding can't work | Ask your ISP for a public IP, or use a Cloudflare Tunnel instead |
     | `192.168.x.x`, `10.x.x.x` or `172.16–31.x.x` | Usually **another router in front** of the ASUS (often the ISP's modem) | Put the ISP's modem in *bridge mode*, or forward ports 80/443 on it to the ASUS |

2. **The DDNS name points to you.** Run `nslookup rumahoren.asuscomm.com`. The `Address` shown under `Name: rumahoren.asuscomm.com` must be the same as api.ipify.org.
3. **Nothing else on the router may use ports 80/443:**
   - ASUS → Administration → System → *Enable Web Access from WAN*: **No**, or keep its HTTPS port at **8443**, never 443.
   - If **AiCloud** is on, turn it off, or move its port away from 443.

## 1. Prepare the server

**Recommended: run Docker directly on the laptop's Linux.** It's the simplest setup, and it survives reboots on its own. (Using a KVM virtual machine instead? See the box below first.)

- Plug in a **network cable** if you can. It's faster and more reliable than Wi-Fi.
- **Allow logins from your Windows PC:**
  ```bash
  sudo apt install -y openssh-server
  ```
- **Give the laptop a fixed LAN IP:** ASUS → LAN → DHCP Server → *Manually Assigned IP* → choose the laptop.
- **Keep it running with the lid closed:**
  ```bash
  sudo mkdir -p /etc/systemd/logind.conf.d
  printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\n' | sudo tee /etc/systemd/logind.conf.d/lid.conf
  sudo reboot
  ```
- **Install Docker, and turn on automatic security updates:**
  ```bash
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker $USER      # then log out and back in
  sudo apt install -y unattended-upgrades
  ```
- **If the firewall (ufw) is on:**
  ```bash
  sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp
  ```

<details>
<summary><b>Using a KVM virtual machine instead</b></summary>

- The VM needs its own address on your home network, and that only works over a **network cable**. A VM can't be bridged onto Wi-Fi. On Wi-Fi, use Docker directly on the laptop instead.
- In virt-manager: VM → *NIC* → Network source **Macvtap device** → your Ethernet card (e.g. `enp3s0`, see `ip link`) → Source mode **Bridge**. (With macvtap the laptop itself can't open the VM's address, but every other device on your network can.)
- Give the **VM** a fixed IP in the ASUS DHCP list (it has its own MAC address), and forward the ports to the VM's IP in step 2.
- Start the VM automatically when the laptop boots: `sudo virsh autostart YOUR-VM-NAME`
- Give the VM a **50 GB disk**: about 10 GB for the system and Docker, 2 GB kept free, and up to 30 GB of photos (see *Disk space* in step 4).
- Do the lid setting on the **laptop**. Do everything else (SSH, Docker, the steps below) **inside the VM**.

</details>

## 2. Router: forward the ports

ASUS → WAN → *Virtual Server / Port Forwarding*:

1. Set **Enable Port Forwarding** to **ON**.
2. Add two rules pointing at the server's LAN IP, then click **Apply**:

| Service name | External port | Internal IP | Internal port | Protocol |
|---|---|---|---|---|
| gk-http  | 80  | *server IP* | 80  | TCP |
| gk-https | 443 | *server IP* | 443 | BOTH |

## 3. Copy the project to the server

On Windows (PowerShell, in the `GambarKita` folder), pack only what's needed. Your local `.env` files stay behind:

```powershell
tar --exclude=.env --exclude=.venv --exclude=node_modules --exclude=storage --exclude=dist --exclude=__pycache__ --exclude=.idea --exclude=.pytest_cache -czf gambarkita.tgz backend frontend deploy
scp gambarkita.tgz USER@SERVER-IP:~
```

On the server:

```bash
mkdir -p ~/gambarkita && tar -xzf ~/gambarkita.tgz -C ~/gambarkita
```

(Or use Git: push to a *private* repository and `git clone` it on the server.)

## 4. Configure

```bash
cd ~/gambarkita/deploy
cp .env.example .env
openssl rand -hex 24     # makes a random key: copy it
nano .env                # paste it after GK_EVENT_CREATION_KEY=   (Ctrl+O, Enter, Ctrl+X to save)
```

Save the key in your password manager: the home page asks for it when you create an event. If you choose your own key instead, use 12+ letters, digits or dashes. **Don't use `$`**: everything after it gets cut off.

- `deploy/.env` only exists **on the server**: you create it here, and it never goes into the package you copy over. Your Windows PC has only `.env.example`.
- Files starting with `.` are hidden on Linux: use `ls -a` to see it.
- Use a **new** key here, not the one from `backend/.env` on your PC.

**Upload limits.** `.env` already limits each guest to 7 photos per event (`GK_MAX_PHOTOS_PER_GUEST=7`), each event to 2800 photos (400 guests × 7), and keeps photos at full resolution (`GK_MAX_STORED_DIMENSION=0`; set e.g. 3072 to save disk). Change the numbers if you like (`GK_MAX_PHOTOS_PER_GUEST=0` turns the guest limit off), then run `docker compose up -d`. The upload page shows each guest how many photos are left.

- Turn it on **before** the event. Only photos shared after that count.
- It's counted per phone browser, so someone who clears their browser data gets a fresh allowance. It stops camera-roll dumps, not a determined cheater.
- Photos you delete on the host dashboard give the guest that slot back. The guest sees it after tapping **Check again** or reloading the page.

**Disk space.** At full resolution a photo takes about 3–4 MB (never more than 10 MB), so a full event of 2800 photos needs about **11 GB** (28 GB at most). Photos stay until you delete the event, so add up every event you keep. Uploads stop on their own when less than 2 GB is free (`GK_MIN_FREE_DISK_BYTES`). To see how much the photos use:

```bash
sudo du -sh /var/lib/docker/volumes/gambarkita_gambarkita-data/_data
```

## 5. Start

```bash
docker compose up -d --build     # first time takes a few minutes
docker compose ps                # both services should say "Up" (backend: "healthy")
docker compose logs -f web       # wait for "certificate obtained successfully", then Ctrl+C
```

Now open **https://rumahoren.asuscomm.com** on your phone using **mobile data** (Wi-Fi off). That proves it works from outside, the way guests will use it.

---

## Everyday commands (run in `~/gambarkita/deploy`)

| What | Command |
|---|---|
| Status | `docker compose ps` |
| Live logs | `docker compose logs -f` |
| Restart | `docker compose restart` |
| Stop (data is kept) | `docker compose down` |

## Updating

**Security updates** (monthly, and a few days before each event):

```bash
docker compose build --pull && docker compose up -d
```

**A new version of the app:** pack and copy it as in step 3, then on the server:

```bash
tar -xzf ~/gambarkita.tgz -C ~/gambarkita    # your deploy/.env is kept: it isn't in the package
cd ~/gambarkita/deploy
docker compose up -d --build
docker image prune -f                        # remove old, unused images
```

## Backups

Photos and the database live in the Docker volume `gambarkita_gambarkita-data`. To back them up:

```bash
docker compose stop backend
docker run --rm -v gambarkita_gambarkita-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/gambarkita-$(date +%F).tgz -C /data .
docker compose start backend
```

Then copy the file to your Windows PC (PowerShell):

```powershell
scp USER@SERVER-IP:~/gambarkita/deploy/gambarkita-*.tgz .
```

Then delete it from the server with `sudo rm ~/gambarkita/deploy/gambarkita-*.tgz`. It's on the same disk as the photos, and uploads stop when that disk gets nearly full.

**Never delete the `caddy-data` volume**: it holds your certificates, and Let's Encrypt limits how often new ones can be issued.

### Restore

On a new server, do steps 1–5 first. Then copy the backup into `~/gambarkita/deploy` and run the commands below, with your file's name:

```bash
docker compose stop backend
docker run --rm -v gambarkita_gambarkita-data:/data -v "$PWD":/backup alpine sh -c \
  'find /data -mindepth 1 -delete && tar xzf /backup/gambarkita-2026-10-01.tgz -C /data && chown -R 10001:10001 /data'
docker compose start backend
```

## Event-day checklist

- [ ] Test from **mobile data** the day before: create the event, upload, watch the wall.
- [ ] Check your home internet **upload** speed (speedtest.net). Guests and the wall download photos from your house; 20 Mbps+ upload is comfortable for a big event.
- [ ] Server on **wired Ethernet** and plugged into power.
- [ ] Print the QR code from the host dashboard, and **save your host link**.
- [ ] After the event: switch **Accept uploads** off on the host dashboard, then make a backup.

## Troubleshooting

| Problem | Fix |
|---|---|
| No HTTPS certificate / site unreachable | Check step 0 (CGNAT, or a second router in front). In step 2, *Enable Port Forwarding* must be ON and the rules must point to the server's **current** IP. The router's *Web Access from WAN* or AiCloud must not be using 443. |
| `dependency failed to start`, or the backend keeps restarting | Run `docker compose logs backend`. It's usually a bad value in `deploy/.env` (for example a key shorter than 12 characters, or one with `$`). Fix it, then `docker compose up -d`. |
| Works on mobile data but not on home Wi-Fi | Your router doesn't pass requests back into your own network. Guests aren't affected. On your PC, add a line `SERVER-IP rumahoren.asuscomm.com` to `C:\Windows\System32\drivers\etc\hosts` (open Notepad as administrator). On your phone, use mobile data. |
| Guests see "Too many requests" | Everyone at the venue shares one Wi-Fi IP: raise `GK_RATE_LIMIT_UPLOAD` (e.g. `600/minute`) in `deploy/.env`, then `docker compose up -d` |
| Server runs out of memory | Make sure `deploy/.env` has `GK_IMAGE_PROCESSING_CONCURRENCY=2` and `GK_IMAGE_PROCESSING_PIXEL_BUDGET=60000000` (`.env.example` sets them), then `docker compose up -d` |
| A host lost their private host link (cleared browser data, new phone) | The server cannot show the old link again, but you can issue a new one with your creation key. Get the event ID from the guest or wall link (`/e/<event-id>/...`), then on the server run `source .env; curl -X POST "$PUBLIC_URL/api/events/<event-id>/host/rotate" -H "X-Event-Creation-Key: $GK_EVENT_CREATION_KEY"` and send the host the `host_url` from the reply. The old link stops working at once. |
