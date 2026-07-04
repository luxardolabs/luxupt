# LuxUPT

**Automatic timelapse videos from your UniFi Protect cameras.**

![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)
![GHCR](https://img.shields.io/badge/ghcr.io-luxardolabs%2Fluxupt-blue?logo=github)

You've invested in UniFi Protect. LuxUPT turns that investment into something more—capturing snapshots around the clock and compiling them into timelapse videos automatically. Watch your construction site progress, your garden grow through the seasons, or simply see what happened while you were away.

<!-- Screenshot: Dashboard with camera grid -->

## Highlights

- **Full-resolution capture** — Pulls frames directly from RTSP video streams, delivering your camera's true resolution
- **Automatic daily videos** — Scheduler creates timelapses every morning from the previous day's captures
- **Works with every UniFi camera** — G3, G4, G5, G6 — automatically detects capabilities and optimizes settings
- **Browser-based control** — Configure everything through the web UI, no config files to edit
- **Runs on your hardware** — Single Docker container, no cloud services, no subscriptions

## Quick Start

**1. Get an API key from UniFi Protect**

Log in to UniFi Protect → Settings → Control Plane → Integrations → Your API Keys → Generate

**2. Create `compose.yaml`**

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

**3. Start it**

```bash
docker compose up -d
```

**4. Open the web UI**

Go to `http://your-server:8080` — the setup wizard walks you through connecting to UniFi Protect and configuring your cameras.

Tomorrow morning, you'll have timelapse videos.

## Full-Resolution Capture

Some UniFi cameras—particularly G5 models—return reduced resolution through the snapshot API. LuxUPT solves this by offering two capture methods:

| Method | How it works | Resolution |
|--------|--------------|------------|
| **API Snapshot** | Requests image from UniFi Protect API | Varies by camera |
| **RTSP Stream** | Captures frame from live video stream | Full camera resolution |

LuxUPT tests each camera and recommends the best method. For cameras where API resolution is limited, RTSP capture delivers full resolution—the same quality you see in the UniFi Protect app.

**Cameras that benefit from RTSP capture:**
- G5 Bullet, G5 Pro, G5 Turret Ultra, G5 Dome
- G3 Instant

For technical background, see this [community discussion](https://community.ui.com/questions/G5-camera-snapshot-resolution/cb0063d0-b534-4320-a96b-ac2e9a546eaf).

## Web Interface

Everything runs through the browser. No SSH, no YAML editing after setup, no restarts when you change settings.

<!-- Screenshot: Camera page overview -->

| Page | What you do there |
|------|-------------------|
| **Cameras** | View all cameras, configure capture settings, monitor success rates |
| **Timelapses** | Browse videos, create on-demand, configure the scheduler |
| **Images** | Browse captured snapshots, view full resolution, manage storage |
| **System** | Monitor health, manage users, view configuration |

## Documentation

Full documentation for every feature, setting, and panel:

- **[Getting Started](docs/getting-started.md)** — Detailed setup, first-run wizard, API key instructions
- **[Web Interface Guide](docs/web-interface.md)** — Every page and panel explained
- **[Configuration](docs/configuration.md)** — Environment variables, multi-site setup, reverse proxy
- **[Troubleshooting](docs/troubleshooting.md)** — Common issues and solutions

## License

LuxUPT is licensed under the [GNU Affero General Public License v3.0](LICENSE).

Released by [Luxardo Labs](https://www.luxardolabs.com/) as a contribution to the UniFi community.

No cloud services. No data collection. No subscriptions. Just software that runs on your hardware.

- [GitHub](https://github.com/luxardolabs/luxupt)
- [GHCR](https://ghcr.io/luxardolabs/luxupt)
- Contact: info@luxardolabs.com

Found a bug? Have a feature idea? [Open an issue](https://github.com/luxardolabs/luxupt/issues). Pull requests welcome.

---

*Built with Python, FastAPI, and FFmpeg.*
