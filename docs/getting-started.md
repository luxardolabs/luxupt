# Getting Started

This guide walks you through setting up LuxUPT from scratch.

## Prerequisites

- **Docker and Docker Compose** — LuxUPT runs as a Docker container
- **UniFi Protect system** — Latest version recommended (tested with 6.2.88)
- **Camera encoding set to Standard** — UniFi Protect cameras must use **Standard** (H.264) encoding for RTSP streams to work. Enhanced (H.265) encoding does not provide compatible RTSP streams. Set this in UniFi Protect under each camera's Settings → Video → Encoding
- **API key** — Generated from your UniFi Protect system

## Getting Your API Key

LuxUPT connects to UniFi Protect through its REST API. You'll need to generate an API key:

1. Log in to your UniFi Protect web interface
2. Go to **Settings** (gear icon)
3. Navigate to **Control Plane** → **Integrations** → **Your API Keys**
4. Click **Generate API Key**
5. Give it a descriptive name (e.g., "LuxUPT Timelapse")
6. Copy the generated API key — you won't be able to see it again

Keep this key secure. Anyone with the key can access your camera feeds.

## Deployment

### Basic Setup

Create a `compose.yaml` file:

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
      TZ: America/Chicago  # Your timezone
```

Start the container:

```bash
docker compose up -d
```

### What Each Setting Does

| Setting | Purpose |
|---------|---------|
| `image` | The LuxUPT Docker image from Docker Hub |
| `container_name` | Name for your container (used in commands like `docker logs luxupt`) |
| `restart: always` | Automatically restart if the container stops or system reboots |
| `ports` | Maps port 8080 inside the container to your host |
| `volumes` | Where images, videos, and the database are stored |
| `TZ` | Your timezone — used for scheduling and file timestamps |

### Volume Mount

The volume mount (`./output:/app/luxupt/output`) is critical. This is where LuxUPT stores:

- Captured images (ephemeral — deleted after timelapse compilation)
- Generated timelapse videos (archival — kept long-term)
- Thumbnail cache
- SQLite database

Make sure this directory:
- Has enough storage space for your needs
- Is backed up if you want to preserve your timelapses
- Has appropriate permissions for Docker to write

For advanced setups with mixed storage (e.g., NVMe + HDD on a NAS), each storage path can be configured independently via environment variables and mounted to separate volumes. See [Configuration — Tiered Storage](configuration.md#tiered-storage-nvme--hddnas) for details.

## First-Run Setup

When you first access LuxUPT at `http://your-server:8080`, you'll go through a setup wizard.

### Step 1: Create Admin Account

If you haven't set `WEB_USERNAME` and `WEB_PASSWORD` in your compose file, you'll see the setup wizard:

1. Enter your desired username
2. Enter a password (minimum 8 characters recommended)
3. Confirm the password
4. Click **Create Account**

You'll be redirected to the login page.

### Step 2: Log In

Enter the credentials you just created.

### Step 3: Configure API Connection

After login, you'll land on the **Cameras** page. Since no API connection is configured yet, you'll see a prompt to set it up:

1. Click **Capture Settings**
2. In the **API Connection** section:
   - **Base URL**: Your UniFi Protect API endpoint
     - Format: `https://[IP-or-hostname]/proxy/protect/integration/v1`
     - Example: `https://192.168.1.1/proxy/protect/integration/v1`
   - **API Key**: Paste the key you generated earlier
   - **Verify SSL**: Disable if using self-signed certificates (common for local UniFi installations)
3. Click **Save**

### Step 4: Camera Discovery

Once connected, LuxUPT automatically discovers all cameras from your UniFi Protect system. Within a few seconds, you should see camera cards appear on the dashboard.

LuxUPT will also test each camera to detect its capabilities:
- API snapshot resolution
- RTSP stream resolution
- Recommended capture method

### Step 5: Verify Capture

Capture is enabled by default. Once cameras are discovered and the API connection is configured, capturing begins automatically.

You'll see the statistics update as images are captured. If you want to adjust settings:

1. Click **Capture Settings**
2. Adjust **Capture Intervals** (e.g., 60 seconds, 180 seconds)
3. Click **Save**

### Step 6: Scheduler (Optional Adjustments)

The scheduler is enabled by default:
- **Run Time**: 1:00 AM
- **Cameras**: All cameras
- **Intervals**: 60 seconds

Tomorrow at 1 AM, LuxUPT will automatically create timelapse videos from the previous day's captures.

To adjust these defaults:

1. Go to **Timelapses** page
2. Click **Scheduler**
3. Change run time, cameras, or intervals as needed
4. Click **Save**

## Skipping the Setup Wizard

If you prefer to pre-configure credentials, add them to your compose file:

```yaml
environment:
  TZ: America/Chicago
  WEB_USERNAME: admin
  WEB_PASSWORD: your-secure-password
  UNIFI_PROTECT_API_KEY: your-api-key
  UNIFI_PROTECT_BASE_URL: https://your-protect-host/proxy/protect/integration/v1
  UNIFI_PROTECT_VERIFY_SSL: "false"
```

With these set, you can log in immediately and cameras will be discovered automatically.

## Verifying It's Working

After setup, verify everything is working:

1. **Cameras page**: All your cameras should appear with "Connected" status
2. **Statistics**: The "Captures" count should increase every interval
3. **Recent Failures**: Should be empty or show only transient errors
4. **Images page**: Browse to see captured images appearing

If you see issues, check the [Troubleshooting Guide](troubleshooting.md).

---

## Documentation

- **Getting Started** (this page)
- [Web Interface Guide](web-interface.md) — Every page and panel explained
- [Configuration](configuration.md) — Environment variables, multi-site setup, storage
- [Troubleshooting](troubleshooting.md) — Common issues and solutions
