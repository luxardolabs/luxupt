# Configuration

Most LuxUPT settings are configured through the web UI. The compose file only needs basic settings to get started. This guide covers all configuration options for advanced setups.

## Basic Configuration

The minimal compose file:

```yaml
services:
  luxupt:
    image: ghcr.io/luxardolabs/luxupt:latest
    container_name: luxupt
    restart: always
    ports:
      - "8080:8080"
    volumes:
      - ./output:/app/luxupt/output
    environment:
      TZ: America/Chicago
```

| Setting | Required | Description |
|---------|----------|-------------|
| `TZ` | Yes | Your timezone (e.g., `America/Chicago`, `Europe/London`) |
| Volume mount | Yes | Where images, videos, and database are stored |

## Full Configuration Example

```yaml
services:
  luxupt:
    image: ghcr.io/luxardolabs/luxupt:latest
    container_name: luxupt
    restart: always
    ports:
      - "8080:8080"
    volumes:
      - ./output:/app/luxupt/output
    environment:
      TZ: America/Chicago

      # Authentication (skip setup wizard)
      WEB_USERNAME: admin
      WEB_PASSWORD: your-secure-password

      # UniFi Protect connection
      UNIFI_PROTECT_API_KEY: your-api-key
      UNIFI_PROTECT_BASE_URL: https://192.168.1.1/proxy/protect/integration/v1
      UNIFI_PROTECT_VERIFY_SSL: "false"
```

---

## Environment Variables Reference

### Authentication

| Variable | Description | Default |
|----------|-------------|---------|
| `WEB_USERNAME` | Admin username — if set, skips setup wizard | — |
| `WEB_PASSWORD` | Admin password — if set, skips setup wizard | — |
| `WEB_SESSION_SECRET` | Session encryption key — auto-generated if not set | Random |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Session duration in minutes | `10080` (7 days) |
| `WEB_LOGIN_RATE_LIMIT` | Maximum login attempts before rate limiting | `5` |
| `WEB_LOGIN_RATE_WINDOW_SECONDS` | Rate limit window in seconds | `60` |

### UniFi Protect Connection

| Variable | Description | Default |
|----------|-------------|---------|
| `UNIFI_PROTECT_BASE_URL` | API endpoint (e.g., `https://192.168.1.1/proxy/protect/integration/v1`) | — |
| `UNIFI_PROTECT_API_KEY` | Your generated API key | — |
| `UNIFI_PROTECT_VERIFY_SSL` | Verify SSL certificates (`true` or `false`) | `false` |

### Storage Paths

| Variable | Description | Default |
|----------|-------------|---------|
| `IMAGE_OUTPUT_PATH` | Where captured images are stored | `output/images` |
| `VIDEO_OUTPUT_PATH` | Where timelapse videos are stored | `output/videos` |
| `THUMBNAIL_CACHE_PATH` | Where thumbnail cache is stored | `output/thumbnails` |
| `DATABASE_DIR` | Directory for the SQLite database file | Same as `OUTPUT_DIR` |

### Web Server

| Variable | Description | Default |
|----------|-------------|---------|
| `WEB_PORT` | Port for the web interface | `8080` |
| `WEB_CORS_ORIGINS` | Allowed CORS origins (comma-separated) | — |
| `WEB_TRUST_PROXY_HEADERS` | Trust X-Forwarded-* headers from reverse proxy | `true` |
| `WEB_COOKIE_SECURE_MODE` | Cookie security: `auto`, `always`, or `never` | `auto` |

### Logging

| Variable | Description | Default |
|----------|-------------|---------|
| `LOGGING_LEVEL` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `LOGGING_FORMAT` | Log format: `json` or `text` | `json` |

### Performance

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_TIMEOUT` | Database connection timeout in seconds | `30` |
| `DATABASE_BUSY_TIMEOUT` | SQLite busy timeout in milliseconds | `30000` |
| `THUMBNAIL_WORKERS` | Parallel workers for thumbnail generation | `4` |
| `SETTINGS_RELOAD_INTERVAL` | How often to reload settings from database (seconds) | `15` |

### Pagination

| Variable | Description | Default |
|----------|-------------|---------|
| `DEFAULT_PAGE_SIZE` | Default items per page | `100` |
| `MAX_PAGE_SIZE` | Maximum items per page | `1000` |
| `RECENT_ITEMS_LIMIT` | Items shown in "recent" lists | `10` |

---

## Multiple UniFi Protect Systems

If you have more than one UniFi Protect system (e.g., home and office), run separate LuxUPT containers for each:

```yaml
services:
  luxupt-home:
    image: ghcr.io/luxardolabs/luxupt:latest
    container_name: luxupt-home
    restart: always
    ports:
      - "8081:8081"
    volumes:
      - ./output-home:/app/luxupt/output
    environment:
      TZ: America/Chicago
      WEB_PORT: "8081"
      WEB_USERNAME: admin
      WEB_PASSWORD: home-password
      UNIFI_PROTECT_API_KEY: home-api-key
      UNIFI_PROTECT_BASE_URL: https://192.168.1.1/proxy/protect/integration/v1
      UNIFI_PROTECT_VERIFY_SSL: "false"

  luxupt-office:
    image: ghcr.io/luxardolabs/luxupt:latest
    container_name: luxupt-office
    restart: always
    ports:
      - "8082:8082"
    volumes:
      - ./output-office:/app/luxupt/output
    environment:
      TZ: America/Chicago
      WEB_PORT: "8082"
      WEB_USERNAME: admin
      WEB_PASSWORD: office-password
      UNIFI_PROTECT_API_KEY: office-api-key
      UNIFI_PROTECT_BASE_URL: https://10.0.0.1/proxy/protect/integration/v1
      UNIFI_PROTECT_VERIFY_SSL: "false"
```

Each instance needs:
- Different container name
- Different port mapping
- Different output directory (volume mount)
- Its own API credentials
- **Different `WEB_PORT` value** — Session cookies include the port number (`access_token_{WEB_PORT}`), so each instance must have a unique port to prevent login conflicts when accessing multiple instances from the same browser

---

## HTTPS with Reverse Proxy

LuxUPT works behind any reverse proxy (Nginx, Traefik, Caddy, etc.) for SSL termination.

### Proxy Header Support

LuxUPT automatically detects HTTPS when running behind a proxy by reading standard headers:

| Header | Purpose |
|--------|---------|
| `X-Forwarded-Proto` | Detects if original request was HTTPS |
| `X-Forwarded-For` | Gets real client IP |
| `X-Real-IP` | Alternative for client IP |

### Cookie Security Modes

The `WEB_COOKIE_SECURE_MODE` setting controls how session cookies are secured:

| Mode | Behavior |
|------|----------|
| `auto` | Secure cookies when proxy indicates HTTPS (recommended) |
| `always` | Always use secure cookies — requires HTTPS |
| `never` | Never use secure cookies — not recommended for production |

### Nginx Configuration

```nginx
server {
    listen 443 ssl http2;
    server_name timelapse.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (for real-time updates)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### Traefik Configuration

```yaml
# docker-compose with Traefik labels
services:
  luxupt:
    image: ghcr.io/luxardolabs/luxupt:latest
    container_name: luxupt
    restart: always
    volumes:
      - ./output:/app/luxupt/output
    environment:
      TZ: America/Chicago
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.luxupt.rule=Host(`timelapse.example.com`)"
      - "traefik.http.routers.luxupt.entrypoints=websecure"
      - "traefik.http.routers.luxupt.tls.certresolver=letsencrypt"
      - "traefik.http.services.luxupt.loadbalancer.server.port=8080"
```

### Caddy Configuration

```
timelapse.example.com {
    reverse_proxy localhost:8080
}
```

Caddy automatically provisions SSL certificates via Let's Encrypt.

---

## Storage Structure

LuxUPT organizes files in a predictable structure:

```
output/
├── images/                ← captured snapshots (write-heavy, ephemeral)
│   └── {camera_name}/
│       └── {interval}s/
│           └── YYYY/MM/DD/
│               └── {camera_name}_{timestamp}.jpg
├── videos/                ← compiled timelapse videos (write-once, archival)
│   └── YYYY/MM/
│       └── {camera_name}/
│           └── {interval}s/
│               └── {camera_name}_YYYYMMDD_{interval}s.mp4
├── thumbnails/            ← cached thumbnails for web UI (ephemeral)
│   └── {cached thumbnail files}
├── backups/
│   └── timelapse_YYYYMMDD_HHMMSS.db
└── timelapse.db
```

### Example

For a camera named "Front Yard" capturing at 60-second intervals:

```
output/
├── images/
│   └── Front_Yard/
│       └── 60s/
│           └── 2025/01/26/
│               ├── Front_Yard_20250126_080000.jpg
│               ├── Front_Yard_20250126_080100.jpg
│               └── ...
├── videos/
│   └── 2025/01/
│       └── Front_Yard/
│           └── 60s/
│               └── Front_Yard_20250125_60s.mp4
└── timelapse.db
```

### Storage Considerations

- **Images**: Accumulate quickly. A single camera at 60-second intervals captures 1,440 images per day. Images are ephemeral — they are deleted after timelapse compilation.
- **Videos**: Much smaller. A day's timelapse is typically 10-50 MB depending on quality settings. Videos are archival and kept long-term.
- **Thumbnails**: Cached versions of images for the web UI. Regenerated as needed.
- **Database**: Grows slowly. Contains metadata only, not image data.

Plan storage accordingly. Use the scheduler's "Keep Source Images" setting to automatically delete images after video creation if storage is limited.

### Tiered Storage (NVMe + HDD/NAS)

By default, all data lives in a single `output/` directory. This is the simplest setup and works well when all storage is on the same disk or NAS volume.

For higher-performance setups — especially NAS devices with both NVMe and HDD storage — you can split data across storage tiers by setting each path to a separate container mount point using environment variables:

```
NVMe (fast, ephemeral)              HDD/NAS (bulk, archival)
┌─────────────────────┐             ┌──────────────────────────┐
│ /app/luxupt/images  │──FFmpeg──→  │ /app/luxupt/videos       │
│ /app/luxupt/thumbs  │             │                          │
│ /app/luxupt/data    │             │ (long-term storage)      │
└─────────────────────┘             └──────────────────────────┘
     write-heavy                         write-once, read-many
     deleted daily                       kept indefinitely
```

**Why this helps:**

| Data | I/O Pattern | Best Storage |
|------|-------------|-------------|
| **Images** | High-volume writes (hundreds of GB/day with many cameras), deleted after compilation | NVMe — fast scratch space, reduces wear on archival drives |
| **Thumbnails** | Random reads for web UI, burst writes on generation | NVMe — faster page loads |
| **Videos** | Large sequential writes (once), sequential reads (streaming) | HDD/NAS — capacity matters more than speed |
| **Database** | Small random reads/writes with fsync | NVMe — lower latency for web UI responsiveness |

**Single mount (default — all data on one disk):**

```yaml
services:
  luxupt:
    image: ghcr.io/luxardolabs/luxupt:latest
    container_name: luxupt
    restart: always
    ports:
      - "8080:8080"
    volumes:
      - ./output:/app/luxupt/output
    environment:
      TZ: America/Chicago
```

**Tiered storage (NVMe scratch + HDD/NAS archive):**

Each storage path is configured via environment variables and mapped to its own volume mount:

```yaml
services:
  luxupt:
    image: ghcr.io/luxardolabs/luxupt:latest
    container_name: luxupt
    restart: always
    ports:
      - "8080:8080"
    volumes:
      # Database + backups on NVMe
      - /mnt/nvme/luxupt/data:/app/luxupt/data
      # Capture scratch on NVMe (high-volume writes, deleted daily)
      - /mnt/nvme/luxupt/images:/app/luxupt/images
      # Thumbnail cache on NVMe (random reads for web UI)
      - /mnt/nvme/luxupt/thumbnails:/app/luxupt/thumbnails
      # Archival videos on HDD/NAS (large files, kept long-term)
      - /mnt/nas/luxupt/videos:/app/luxupt/videos
    environment:
      TZ: America/Chicago
      DATABASE_DIR: "/app/luxupt/data"
      IMAGE_OUTPUT_PATH: "/app/luxupt/images"
      VIDEO_OUTPUT_PATH: "/app/luxupt/videos"
      THUMBNAIL_CACHE_PATH: "/app/luxupt/thumbnails"
```

Each path is independently configurable — mount each to whatever storage tier makes sense for your hardware.

**Setup steps:**

1. Create the directories on each storage tier:
   ```bash
   # NVMe storage
   mkdir -p /mnt/nvme/luxupt/data
   mkdir -p /mnt/nvme/luxupt/images
   mkdir -p /mnt/nvme/luxupt/thumbnails

   # HDD/NAS storage
   mkdir -p /mnt/nas/luxupt/videos
   ```

2. Set ownership to uid 1000 (the container runs as `appuser` with uid 1000):
   ```bash
   chown -R 1000:1000 /mnt/nvme/luxupt
   chown -R 1000:1000 /mnt/nas/luxupt
   ```

3. Update your compose file with the volume mounts and environment variables (as shown above) and start the container.

**NAS-specific notes:**

- **NAS with NVMe slots** (Ugreen DXP, Synology with M.2 slots, etc.): Use the NVMe for images, thumbnails, and database. Use the HDD pool for videos.
- **ZFS users**: If your NAS has a ZFS SLOG (ZIL on NVMe), synchronous NFS write latency is already improved. Tiered storage still helps by reducing write volume on spinning disks.
- **NVMe sizing**: Images are ephemeral (deleted after compilation), so NVMe capacity only needs to hold 1-2 days of captures. A 256GB-1TB NVMe is sufficient for most setups.

---

## CLI Commands

For advanced users, LuxUPT supports command-line modes for testing and manual operation:

```bash
# Validate configuration against rate limits
docker exec luxupt python3 main.py validate

# Test camera connectivity
docker exec luxupt python3 main.py test

# Create timelapses immediately (all cameras, yesterday)
docker exec luxupt python3 main.py create

# Run image capture only (no scheduler)
docker exec luxupt python3 main.py fetch

# Run video creation only (no capture)
docker exec luxupt python3 main.py timelapse
```

These are useful for:
- Testing configuration before enabling automatic operation
- Running one-off timelapse creation
- Debugging connectivity issues

---

## Database Management

LuxUPT uses SQLite for metadata storage. The database file is at `output/timelapse.db`.

### Automatic Backups

LuxUPT includes a built-in database backup system that runs automatically. Configure it from the web UI under **System → Backup Settings**.

| Setting | Description | Default |
|---------|-------------|---------|
| **Retention** | Number of backups to keep. Set to 0 to disable automatic backups. | 0 (disabled) |
| **Interval** | Seconds between backups | 3600 (1 hour) |
| **Backup Directory** | Subdirectory within the output volume for backup files | `backups` |

Backups use SQLite's native hot-backup API — they run safely while the application is active with no downtime required. Old backups are automatically pruned based on the retention count.

Backup files are stored as `output/backups/timelapse_YYYYMMDD_HHMMSS.db`.

### Manual Backup

For a one-off manual backup without the automatic system:

```bash
# No need to stop the container — SQLite hot backup is safe
docker exec luxupt python3 -c "
import sqlite3
src = sqlite3.connect('/app/luxupt/output/timelapse.db')
dst = sqlite3.connect('/app/luxupt/output/timelapse.db.backup')
src.backup(dst)
dst.close()
src.close()
print('Backup complete')
"
```

### Database Location

By default, the database is at `/app/luxupt/output/timelapse.db` inside the container. If you set `DATABASE_DIR`, it will be at `{DATABASE_DIR}/timelapse.db` instead (e.g., `/app/luxupt/data/timelapse.db`).

To run SQL commands:

```bash
# Default location
docker exec luxupt python3 -c "
import sqlite3
conn = sqlite3.connect('/app/luxupt/output/timelapse.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM captures')
print(f'Total captures: {cursor.fetchone()[0]}')
conn.close()
"

# If using DATABASE_DIR
docker exec luxupt python3 -c "
import sqlite3
conn = sqlite3.connect('/app/luxupt/data/timelapse.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM captures')
print(f'Total captures: {cursor.fetchone()[0]}')
conn.close()
"
```

---

## Health Checks

The Docker image includes a health check. You can verify the container is healthy:

```bash
docker inspect --format='{{.State.Health.Status}}' luxupt
```

For orchestration tools, the health endpoint is available at `/health`.

---

## Documentation

- [Getting Started](getting-started.md) — Setup, first-run wizard, connecting to UniFi Protect
- [Web Interface Guide](web-interface.md) — Every page and panel explained
- **Configuration** (this page)
- [Troubleshooting](troubleshooting.md) — Common issues and solutions
