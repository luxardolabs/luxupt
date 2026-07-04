# Web Interface Guide

LuxUPT's web interface provides complete control over your timelapse system. This guide covers every page, panel, and setting.

## Navigation

The interface has four main pages, accessible from the top navigation bar:

- **Cameras** — Capture settings and camera management
- **Timelapses** — Video library and creation
- **Images** — Browse captured snapshots
- **System** — Status, configuration, and users

---

## Cameras Page

Your main dashboard for managing cameras and capture settings.

### Statistics Section

Three cards at the top show system health at a glance:

| Stat | Description | Click Action |
|------|-------------|--------------|
| **Captures** | Total successful image captures across all cameras and intervals | Opens Capture Statistics Panel |
| **Failed** | Total failed capture attempts — high numbers indicate problems | — |
| **Cameras** | Shows connected cameras (e.g., "8 connected" means 8 of your cameras are online) | — |

### Capture Statistics Panel

Click the "Captures" stat card to open detailed statistics.

**Filters:**
- **Camera**: View stats for a specific camera or all cameras
- **Interval**: Filter by capture interval
- **Period**: Last hour, 6 hours, 24 hours, or 7 days

**Charts and Metrics:**
- Success rate over time (line chart)
- Captures per camera (bar chart)
- Failure breakdown by error type
- Average capture time

### Recent Failures Section

A collapsible section showing the most recent capture failures. Helps diagnose problems quickly.

| Column | Description |
|--------|-------------|
| **Time** | When the failure occurred |
| **Camera** | Which camera failed |
| **Int** | Capture interval that failed |
| **Error** | Error message explaining what went wrong |

**Behavior:**
- Automatically expands when new failures occur
- Remembers your collapse preference between sessions
- Shows most recent failures first (up to 10)

**Common Errors:**
- "Connection timeout" — Camera or network is slow
- "Camera disconnected" — Camera went offline
- "Rate limited" — Too many requests, increase offset settings
- "Bad request" — Camera doesn't support requested quality

*Example: If you see repeated "rate limited" errors for the same camera, go to Capture Settings → Camera Distribution and increase Min Offset and Max Offset to spread captures further apart.*

### Camera Grid

Visual cards for each discovered camera, arranged in a responsive grid.

**Card Contents:**
- **Camera Name**: Display name from UniFi Protect
- **Status Badge**: Green "Connected" or red "Disconnected"
- **Thumbnail**: Most recent captured image (updates periodically)
- **Stats**: Success rate and capture count

**Card Actions:**
- **Click card**: Opens Camera Settings Panel for that camera
- **Click camera name**: Opens Camera Detail Page

**Auto-Refresh:**
The camera grid refreshes automatically every 30 seconds to show updated thumbnails and status.

---

## Capture Settings Panel

Access via the **Capture Settings** button on the Cameras page. Configures global capture behavior.

### Capture Section

| Setting | Description | Default |
|---------|-------------|---------|
| **Capture Enabled** | Master switch for all image capture. Turn off to pause the entire system without losing settings. | On |
| **High Quality** | When enabled, requests 1080p+ resolution snapshots from cameras that support it. Some older cameras only support standard resolution. | On |
| **Capture Intervals** | How often to capture images from each camera. Add multiple intervals (e.g., 60s and 180s) to create timelapses at different speeds. Shorter intervals = smoother video but more storage. | 60s |
| **Default Method** | **Auto**: Let LuxUPT choose based on camera capabilities. **API Snapshot**: Fast, uses UniFi Protect's snapshot API. **RTSP Stream**: Captures a frame from the video stream using FFmpeg — use this for full resolution on cameras with limited API resolution. | Auto |
| **RTSP Quality** | When using RTSP capture: **High** = full resolution, **Medium** = 720p equivalent, **Low** = 480p equivalent. Higher quality uses more bandwidth and storage. | High |
| **RTSP Format** | **PNG**: Lossless compression, larger files, best for timelapse quality. **JPEG**: Lossy compression, smaller files, may show artifacts in timelapses. | PNG |
| **PNG Compression** | 0-9 scale. **0** = no compression (fastest, largest files). **6** = balanced. **9** = maximum compression (slowest, smallest files). | 6 |
| **RTSP Timeout** | How long to wait for RTSP capture before giving up. Increase if you have slow network connections or cameras that take time to respond. | 15s |

*Example: With intervals set to 60s and 300s, each camera captures every minute AND every 5 minutes. The 60s interval gives you smooth timelapses; the 300s interval uses less storage for longer-term archival.*

### API Connection Section

| Setting | Description | Default |
|---------|-------------|---------|
| **Base URL** | Your UniFi Protect API endpoint. Format: `https://[IP-or-hostname]/proxy/protect/integration/v1`. Find your IP in the UniFi Protect web interface. | — |
| **API Key** | The API key you generated in UniFi Protect. Go to Control Plane → Integrations → Your API Keys to create one. | — |
| **Verify SSL** | Enable to validate SSL certificates. **Disable** if using self-signed certificates (common with local UniFi installations). | Off |
| **Camera Refresh** | How often (in seconds) to check UniFi Protect for new or reconnected cameras. Lower values detect changes faster but increase API usage. | 300s |

Shows **"Using ENV"** badge if credentials are set via environment variables. You can override ENV values by entering new values here.

*Example: If your UniFi Protect is at 192.168.1.1, the Base URL would be `https://192.168.1.1/proxy/protect/integration/v1`. If you get SSL errors, turn off Verify SSL — most local UniFi installations use self-signed certificates.*

### Camera Distribution Section

Camera distribution staggers captures across time so each camera fires at its own designated time slot within the interval. Cameras fire independently — a slow camera never blocks the next one from starting on time.

| Setting | Description | Default |
|---------|-------------|---------|
| **Min Offset** | Seconds between each camera's capture start time. Each camera is assigned a consecutive slot (Camera 1 at offset 2s, Camera 2 at 4s, Camera 3 at 6s, etc.). | 2s |
| **Max Offset** | Maximum offset value. Prevents captures from spreading too far apart within an interval. | 15s |

Distribution is always active when you have more than one camera. Each capture cycle fires as a background task, so the interval loop always runs on time even if individual cameras take longer than expected.

If the system falls behind (e.g., two full capture cycles are still running when a third would start), the third cycle is skipped and an error is logged to the activity feed.

*Example: With 7 cameras and 2s offset, cameras fire at 2s, 4s, 6s, 8s, 10s, 12s, 14s within each interval. Each camera runs independently — if the camera at 4s takes 10 seconds, the camera at 6s still fires at 6s.*

### Performance & Reliability Section

| Setting | Description | Default |
|---------|-------------|---------|
| **Timeout** | How long to wait for a single API request before giving up. Increase if you have slow network or cameras timing out. | 30s |
| **Retries** | Number of retry attempts when a capture fails. Each retry uses exponential backoff. | 3 |
| **Retry Delay** | Base delay (seconds) between retry attempts. Actual delay increases with each retry. | 2s |
| **Rate Limit** | UniFi Protect's API limit (typically 10 requests/second). Don't increase unless you know your system supports more. | 10 |
| **Buffer** | Safety margin for rate limiting. **0.8** means use only 80% of the rate limit. Lower values are safer but may slow capture on large systems. | 0.8 |

*Example: With Rate Limit at 10 and Buffer at 0.8, LuxUPT limits itself to 8 requests/second, leaving headroom for other API activity. If you're seeing rate limit errors, try lowering Buffer to 0.6.*

---

## Camera Settings Panel

Click any camera card to configure individual camera settings. These override the global defaults for this specific camera.

### Camera Info Section

Displays read-only information about the camera from UniFi Protect:

| Field | Description |
|-------|-------------|
| **Status** | Connected or Disconnected |
| **Model** | Camera model (e.g., G4-Pro, G3-Instant) |
| **MAC** | Hardware MAC address |
| **Video Mode** | Current recording mode |
| **HDR Type** | HDR capability if supported |
| **Full HD API** | Whether the camera supports high-resolution API snapshots |
| **Features** | Badges showing HDR, Mic, Speaker, and Smart Detection capabilities (Person, Vehicle, Animal, etc.) |

### Detected Capabilities Section

LuxUPT tests each camera to determine the actual resolution you'll get from each capture method.

| Field | Description |
|-------|-------------|
| **API Snapshot** | Maximum resolution achieved via API. Cameras with limited API support may show low resolution here (e.g., 640x360). |
| **RTSP Stream** | Maximum resolution achieved via RTSP (e.g., "2688x1512"). This is your camera's true resolution. |
| **Recommended** | Which method LuxUPT recommends. Will recommend RTSP when API resolution is insufficient. |
| **Re-detect** | Click to re-run capability detection. Useful after firmware updates or if initial detection failed. |

**Run detection on all your cameras** to see which ones have limited API resolution. If API shows a tiny resolution like 640x360 but RTSP shows 1920x1080 or higher, switch that camera to RTSP.

*Example: Your G5 Bullet shows API Snapshot at 640x360 but RTSP Stream at 2688x1512. The recommendation says "RTSP". Change Capture Method to "RTSP Stream" and you'll get full 4K resolution in your timelapses instead of thumbnail-quality images.*

### Capture Method

| Option | When to Use |
|--------|-------------|
| **Auto** | LuxUPT chooses based on detected capabilities. Recommended if you've run detection. |
| **API Snapshot** | Force API capture. Use when API detection shows acceptable resolution (1080p+). |
| **RTSP Stream** | Force RTSP capture. Use for cameras where API resolution is limited. Full resolution, but slower. |

### RTSP Quality

Only applies when using RTSP capture method.

| Option | Description |
|--------|-------------|
| **High** | Full camera resolution. Best quality but largest files and most bandwidth. |
| **Medium** | Reduced resolution (~720p). Good balance of quality and size. |
| **Low** | Lowest resolution (~480p). Smallest files, fastest capture. |

### Enabled Intervals

Select which capture intervals apply to this camera. Useful for:
- Excluding a camera from frequent captures (e.g., only capture every 5 minutes instead of every minute)
- Reducing storage for less important cameras
- Different capture strategies per camera

### Camera Active

Master toggle for this camera. When disabled:
- No images are captured from this camera
- Camera remains in the database
- Existing images and timelapses are preserved
- Re-enable anytime to resume capture

### Danger Zone

| Action | What Happens |
|--------|--------------|
| **Delete Camera** | Removes the camera from LuxUPT's database. Images and videos on disk are NOT deleted. If the camera is still in UniFi Protect, it will be re-discovered on the next camera refresh. |

---

## Camera Detail Page

Click a camera's name to view detailed information:

- **Total captures**: Lifetime capture count for this camera
- **Today's captures**: Captures since midnight
- **Timelapse count**: Number of timelapse videos created
- **Latest capture**: Preview with timestamp
- **Quick actions**: Links to view images or timelapses for this camera

---

## Timelapses Page

Browse and manage timelapse videos. Two tabs: **Browser** for viewing videos and **Jobs** for monitoring creation progress.

### Browser Tab

#### Statistics Section

| Stat | Description |
|------|-------------|
| **Videos** | Total completed timelapse videos |
| **Pending** | Jobs queued but not yet started |
| **Failed** | Jobs that encountered errors |
| **Storage** | Total disk space used by video files |

#### Filters

| Filter | Description |
|--------|-------------|
| **Camera** | Show timelapses from a specific camera only |
| **Date** | Filter to timelapses from a specific date |
| **Interval** | Filter by capture interval (e.g., 60s, 300s) |
| **Status** | Filter by status: Completed, Pending, or Failed |

Filters apply immediately when changed — no need to click a button.

*Example: To find all failed timelapse jobs from this week, filter by Status: Failed. Check the error messages to diagnose — common issues include not enough images or disk space.*

#### Timelapse Library

Video cards display:
- Camera name and date
- Capture interval used
- Video duration and file size
- Creation timestamp
- Status indicator (completed/pending/failed)

**Playing Videos:**
- Click any video card to open the lightbox player
- Video plays directly in browser (no download required)
- Use lightbox controls to navigate between videos
- Click outside or press Escape to close

**Deleting Videos:**
- Click the delete icon on a video card
- Confirm deletion in the popup
- Removes video file from disk and database record

---

## Create Timelapse Panel

Access via **Create Timelapse** button on the Timelapses page. Manually create a timelapse video on-demand.

### Step 1: Select Camera

Choose which camera's images to use. Only cameras with captured images appear in the list.

### Step 2: Select Date

After selecting a camera, available dates are populated. Only dates with images for that camera are shown.

### Step 3: Select Interval

After selecting a date, available intervals are populated. Only intervals with images for that camera and date are shown.

### Step 4: Preview

Once all selections are made, a preview appears showing:
- **First Frame**: The earliest image that will be included
- **Last Frame**: The latest image that will be included
- **Image Count**: Total number of images to compile
- **Estimated Duration**: Approximate video length based on frame rate

This helps you verify you're creating the right timelapse before starting.

### Step 5: Create

Click **Create Timelapse** to queue the job. The panel closes and you can:
- Switch to the Jobs tab to monitor progress
- Continue browsing — you'll see the new video when it completes

**Notes:**
- Creating uses the encoding settings from the Scheduler Panel (frame rate, CRF, preset)
- If a timelapse already exists for this camera/date/interval, it will be replaced
- Large timelapses (thousands of images) may take several minutes to encode

*Example: To create a timelapse of last Saturday's garden activity: select your garden camera, pick Saturday's date, choose the 60s interval. The preview shows 1,440 images spanning 6 AM to 8 PM — click Create Timelapse and check the Jobs tab for progress.*

---

## Scheduler Panel

Access via **Scheduler** button on the Timelapses page. Configures automatic daily timelapse creation. The scheduler runs once per day at the configured time.

### Schedule Section

| Setting | Description | Default |
|---------|-------------|---------|
| **Enable Scheduler** | Master switch for automatic timelapse creation. When disabled, no automatic videos are created (you can still create them manually). | On |
| **Run Time** | What time of day the scheduler runs (24-hour format). Recommended: late night when system load is low. | 01:00 |
| **Days Back** | Which day to process. **1** = yesterday (most common), **2** = two days ago, etc. Using 1 ensures a full day of captures is available. | 1 |
| **Concurrent** | Number of videos to encode simultaneously. Higher values finish faster but use more CPU/RAM. | 2 |
| **Keep Source Images** | When enabled, images are preserved after video creation. When disabled, images are deleted after successful video creation to save storage. | On |
| **Recreate Existing** | When enabled, existing videos for the same camera/date/interval are overwritten. When disabled, existing videos are skipped. Disable if you want to skip dates that already have videos. | On |

*Example: With Run Time at 01:00, Days Back at 1, and Concurrent at 2, LuxUPT creates yesterday's timelapses at 1 AM, encoding 2 videos at a time. A system with 8 cameras at one interval would create 8 videos, 2 at a time.*

### Cameras & Intervals Section

Control which timelapses are created:

| Setting | Description | Default |
|---------|-------------|---------|
| **Cameras** | Check cameras to include in automatic timelapse creation. Uncheck cameras you don't want daily videos for (e.g., low-priority cameras). Click "Select All" to quickly enable all. | All cameras |
| **Intervals** | Check which capture intervals to create videos for. If you capture at 60s and 180s, you can choose to only create videos for one interval to save processing time and storage. | 60s |

*Example: If you have 10 cameras but only want daily timelapses for your 3 front-facing cameras, uncheck the 7 indoor cameras. They still capture images, but the scheduler won't create videos for them automatically.*

### Video Encoding Section

These settings control FFmpeg video encoding. Changes affect all future timelapses.

| Setting | Description | Default |
|---------|-------------|---------|
| **Frame Rate** | Output video frames per second. Higher = smoother playback but shorter video duration. | 30 |
| **Quality (CRF)** | Constant Rate Factor (0-51). **Lower = better quality, larger files**. 0 is lossless, 51 is worst. | 23 |
| **Timeout** | Maximum seconds to wait for encoding. Increase for very long timelapses (thousands of images). | 14400 |
| **Pixel Format** | Video color format. **yuv420p** is most compatible. **yuv444p** preserves more color but larger files. **rgb24** for maximum quality. | yuv420p |
| **Preset** | Encoding speed vs compression tradeoff. Slower presets = smaller files but longer encoding time. | medium |

#### Encoding Presets Explained

| Preset | Speed | File Size | When to Use |
|--------|-------|-----------|-------------|
| ultrafast | Fastest | Largest | Testing, previews |
| superfast | Very fast | Very large | Quick processing needed |
| veryfast | Fast | Large | Balance toward speed |
| faster | Above average | Above average | Slight speed preference |
| fast | Slightly fast | Slightly large | Minor speed preference |
| medium | Balanced | Balanced | **Recommended default** |
| slow | Slow | Small | Quality preference |
| slower | Very slow | Very small | High quality preference |
| veryslow | Slowest | Smallest | Maximum compression, archival |

#### CRF Quality Guide

| CRF Value | Quality | Use Case |
|-----------|---------|----------|
| 0 | Lossless | Archival, editing source |
| 17-18 | Visually lossless | High quality viewing |
| 19-23 | Excellent | **Recommended range** |
| 24-28 | Good | Balance quality/size |
| 29-35 | Fair | Small file priority |
| 36+ | Poor | Not recommended |

*Example: For a construction site timelapse you'll share publicly, use CRF 20 and preset "slow" for excellent quality. For internal review copies, CRF 28 and preset "fast" saves encoding time and storage.*

---

## Jobs Tab

Monitor timelapse creation jobs in real-time. The page auto-updates every 5 seconds.

### Statistics Section

Same statistics as Browser tab, but updates automatically to reflect job progress.

### Job Queue

Jobs are organized by status:

**Active Jobs:**
- Currently encoding videos
- Shows progress bar with percentage
- Displays camera name, date, and interval
- Progress updates in real-time

**Pending Jobs:**
- Queued and waiting to start
- Will begin when an active slot opens (based on "Concurrent" setting in Scheduler)
- Shows position in queue

**Recently Completed:**
- Finished jobs from the current session
- Shows success or failure status
- Failed jobs display error message
- Successful jobs show file size and duration

### Job States

| State | Description |
|-------|-------------|
| **Pending** | Queued, waiting for available encoding slot |
| **In Progress** | Currently encoding — progress bar shows completion percentage |
| **Completed** | Successfully finished — video is available in Browser |
| **Failed** | Encountered an error — check error message for details |

**Common Failure Reasons:**
- Not enough images (need at least 2 images to create video)
- Disk full
- FFmpeg timeout (increase timeout in Scheduler settings)
- Images corrupted or unreadable

---

## Images Page

Browse and manage captured snapshots. Images are stored on disk and tracked in the database.

### Filters

| Filter | Description |
|--------|-------------|
| **Camera** | Show images from a specific camera only, or "All Cameras" to see everything |
| **Date** | Filter to a specific capture date — dates shown are those with available images |
| **Interval** | Filter by capture interval (e.g., 60s, 180s) — useful when you capture at multiple frequencies |
| **Per Page** | How many images to show per page: 36, 72, or 108 — higher values load more thumbnails at once |

Filters apply immediately when changed — no need to click a button.

*Example: To review yesterday's captures from your front door camera at 60s interval, select that camera, pick yesterday's date, select 60s, and browse through the thumbnails to verify capture quality.*

### Statistics Section

| Stat | Description |
|------|-------------|
| **Total Images** | Number of images matching current filters (or total if no filters) |
| **Storage** | Total disk space used by captured images |
| **Cameras** | Number of cameras with captured images |
| **Dates** | Number of unique dates with images |
| **Intervals** | Number of different capture intervals in use |

### Image Browser

- **Thumbnail Grid**: Responsive grid that adjusts to screen size. Thumbnails are generated automatically for fast loading.
- **Lightbox**: Click any image to open full-size in a lightbox overlay. Use arrow keys or on-screen buttons to navigate between images.
- **Full Resolution**: Click the link in lightbox to open the original full-resolution image in a new tab.
- **Pagination**: Navigate through large collections with page controls at the bottom.

---

## Delete Images Panel

Access via the red **Delete** button on the Images page. **Use with caution — deletions are permanent.**

### Step 1: Select Images

Use filters to narrow down which images to delete:

| Filter | Effect |
|--------|--------|
| **Camera** | Delete only from specific camera, or leave blank for all cameras |
| **Date** | Delete only from specific date, or leave blank for all dates |
| **Interval** | Delete only specific interval, or leave blank for all intervals |

### Step 2: Preview

Before deletion, you'll see:
- Total count of images that will be deleted
- Sample thumbnails of affected images
- Warning if the count is large

### Step 3: Confirm

Click the confirm button to permanently delete. This action:
- Removes image files from disk
- Removes records from database
- Cannot be undone

**Common Use Cases:**
- Delete old images to reclaim storage (filter by old dates)
- Remove failed captures from a problematic camera
- Clean up a specific interval you no longer need

*Example: To free up space by deleting last month's images while keeping this month's, filter to dates from last month (one date at a time, or delete all from a specific camera). After verifying the preview shows the right images, confirm deletion.*

---

## System Page

System status and configuration overview. Two tabs: **Status** and **Users**.

### Status Tab

#### Statistics Section

Quick overview of system health:

| Stat | Description |
|------|-------------|
| **Cameras** | Total cameras in the system |
| **Captures** | Total images captured since installation |
| **Disk Used** | Percentage of disk space used on the output volume |
| **Database** | Size of the SQLite database file |

#### System Status Section

Four cards showing detailed system information:

**Service Status Card:**

| Field | Description |
|-------|-------------|
| **Web Interface** | Always enabled when you can see this page |
| **Image Capture** | Whether automatic capture is running — shows "Enabled" or "Disabled" |
| **Timelapse Creation** | Whether the scheduler is enabled for automatic video creation |
| **Capture Method** | Current default capture method (API or RTSP) |
| **Fetch Intervals** | Active capture intervals (e.g., "60, 180s") |
| **Rate Limit** | Current API rate limit setting (requests per second) |

**Storage Card:**

| Field | Description |
|-------|-------------|
| **Disk Space** | Visual progress bar showing used vs total disk space — color changes from green to yellow to red as disk fills |
| **Images** | Total size of all captured images on disk |
| **Videos** | Total size of all timelapse videos on disk |
| **Output Path** | Filesystem path where data is stored (inside container) |

*Example: If disk usage shows 85% with 50GB of images and 5GB of videos, you're running low on space. Either enable "Delete images after video creation" in Scheduler settings, or use Delete Images to remove old captures.*

**Database Card:**

| Field | Description |
|-------|-------------|
| **Captures** | Number of capture records in database (may differ from files on disk if files were deleted externally) |
| **Timelapses** | Number of timelapse records in database |
| **Cameras** | Number of cameras tracked in database |
| **Database Size** | SQLite database file size |

**Version Card:**

| Field | Description |
|-------|-------------|
| **Version** | LuxUPT application version (e.g., 1.1.2) |
| **Build Date** | When this version was built |
| **Timezone** | Configured timezone from TZ environment variable |
| **Platform** | Operating system (typically Linux in Docker) |
| **Python** | Python version running the application |
| **Architecture** | CPU architecture (amd64, arm64, etc.) |

#### Backup Settings Section

Configure automatic database backups. Backups use SQLite's native hot-backup API and run safely while the application is active.

| Setting | Description | Default |
|---------|-------------|---------|
| **Retention** | Number of backups to keep. Set to 0 to disable automatic backups. | 0 (disabled) |
| **Interval** | Seconds between backups | 3600 (1 hour) |
| **Backup Directory** | Subdirectory within the output volume for backup files | `backups` |

Backups are stored as `output/backups/timelapse_YYYYMMDD_HHMMSS.db`. Old backups beyond the retention count are automatically pruned.

#### Activity Feed

The dashboard and activity log show system events including:

- **Capture failures** — Individual camera capture errors (timeouts, connection failures, retry exhaustion) are logged with camera name, interval, and error message
- **Cycle skips** — If the capture system falls behind (previous cycles still running when a new one would start), an error is logged identifying the affected interval

Check the activity feed when diagnosing capture rate issues or investigating why a camera is missing images.

#### Configuration Section

Three cards showing current configuration (read-only view):

**API Settings Card:**

| Field | Description |
|-------|-------------|
| **API URL** | Configured UniFi Protect API endpoint (partially masked) |
| **SSL Verify** | Whether SSL certificate verification is enabled |
| **Capture Method** | Default capture method for new cameras |
| **High Quality** | Whether high-quality snapshots are requested |

**Rate Limiting Card:**

| Field | Description |
|-------|-------------|
| **Rate Limit** | Maximum API requests per second |
| **Safety Buffer** | Percentage of rate limit actually used (e.g., 80%) |
| **Concurrent Limit** | Maximum simultaneous capture requests |
| **Max Retries** | Retry attempts for failed captures |

**Timelapse Settings Card:**

| Field | Description |
|-------|-------------|
| **Frame Rate** | Video output frames per second |
| **Quality (CRF)** | Video quality setting (lower = better) |
| **Preset** | FFmpeg encoding preset |
| **Intervals** | Capture intervals configured for timelapse creation |

---

### Users Tab

Manage user accounts for web interface authentication.

#### User List

Shows all users with their username, creation date, and last login time.

#### Add User

Click **Add User** button to create a new account:
1. Enter username (must be unique)
2. Enter password (minimum 8 characters recommended)
3. Confirm password
4. Click Create

#### Edit User

Click the edit icon next to any user to modify:
- Change username
- Change password
- Changes take effect immediately

#### Delete User

Click the delete icon to remove a user:
- Requires confirmation
- Cannot delete the last remaining user (system requires at least one account)
- Deleted users are immediately logged out

**Notes:**
- If `WEB_USERNAME` and `WEB_PASSWORD` are set in environment variables, that account takes priority over database users
- Multiple users can be logged in simultaneously
- Each user session is independent

*Example: To give a colleague access to view timelapses, create a user account for them. They can log in from their own device and browse videos without affecting your session.*

---

## Login Page

Standard authentication page with:
- Username field
- Password field
- "Sign in" button

**Session Details:**
- Sessions last 7 days by default (configurable via `ACCESS_TOKEN_EXPIRE_MINUTES`)
- Login is rate-limited to prevent brute force attacks (5 attempts per minute by default)
- After successful login, you're redirected to the Cameras page

**Logout:**
Click your username in the top navigation bar and select "Logout" to end your session.

---

## Documentation

- [Getting Started](getting-started.md) — Setup, first-run wizard, connecting to UniFi Protect
- **Web Interface Guide** (this page)
- [Configuration](configuration.md) — Environment variables, multi-site setup, storage
- [Troubleshooting](troubleshooting.md) — Common issues and solutions
