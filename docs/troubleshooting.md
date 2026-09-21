# Troubleshooting

Common issues and solutions for LuxUPT.

______________________________________________________________________

## Camera Issues

### Low Resolution / Tiny Images

**Symptom:** Captured images are small (640x360) even though your camera supports higher resolution.

**Cause:** Some UniFi cameras return reduced resolution through the snapshot API. This affects G5 models in particular.

**Solution:**

1. Open the camera's settings panel (click the camera card on the Cameras page)
1. Click **Re-detect** to test both capture methods
1. Compare the resolutions:
   - **API Snapshot**: Shows what the API returns
   - **RTSP Stream**: Shows your camera's true resolution
1. If RTSP shows higher resolution, change **Capture Method** to "RTSP Stream"
1. Click **Save**

**Cameras typically affected:**

- G5 Bullet, G5 Pro, G5 Turret Ultra, G5 Dome
- G3 Instant

**Cameras typically unaffected:**

- G3 Flex
- G4 Doorbell, G4 PTZ
- G6 cameras

For technical background, see this [community discussion](https://community.ui.com/questions/G5-camera-snapshot-resolution/cb0063d0-b534-4320-a96b-ac2e9a546eaf).

______________________________________________________________________

### No Cameras Discovered

**Symptom:** The Cameras page shows no cameras after configuring API connection.

**Possible Causes:**

1. **Incorrect API key**

   - Regenerate the API key in UniFi Protect
   - Make sure you copied the entire key

1. **Incorrect Base URL**

   - Format should be: `https://[IP]/proxy/protect/integration/v1`
   - Try using the IP address instead of hostname
   - Verify you can reach the URL from the Docker host

1. **Network issues**

   - Container can't reach the UniFi Protect host
   - Try: `docker exec luxupt curl -k https://your-protect-ip/`

1. **SSL certificate issues**

   - Set `UNIFI_PROTECT_VERIFY_SSL: "false"` in your compose file
   - Or configure it in Capture Settings → API Connection → Verify SSL

**Debugging:**

Check the container logs for error messages:

```bash
docker logs luxupt | grep -i "error\|failed\|camera"
```

______________________________________________________________________

### Camera Shows "Disconnected"

**Symptom:** Camera appears in LuxUPT but shows "Disconnected" status.

**Causes:**

- Camera is offline in UniFi Protect
- Camera was removed from UniFi Protect
- Network connectivity issues between camera and NVR

**Solution:**

1. Check the camera status in UniFi Protect directly
1. If the camera is online in Protect but shows disconnected in LuxUPT, click the camera and use **Re-detect** to refresh
1. Wait for the next camera refresh (default: 5 minutes) or restart the container

______________________________________________________________________

### RTSP Capture Failing

**Symptom:** RTSP capture fails but API capture works. Camera shows "RTSP Stream Not Tested" after detection.

**Possible Causes:**

1. **Camera set to Enhanced encoding (most common)**

   - Enhanced encoding uses H.265/HEVC, which does not provide compatible RTSP streams
   - In UniFi Protect: Device Settings → Video → Encoding → set to **Standard** (H.264)
   - This is a UniFi Protect platform requirement that affects all third-party RTSP integrations (Home Assistant, Frigate, etc.)
   - See [FAQ: Why do some cameras show "RTSP Stream Not Tested"?](#why-do-some-cameras-show-rtsp-stream-not-tested) below

1. **RTSP not enabled on camera**

   - In UniFi Protect: Device Settings → Advanced → Enable RTSP
   - Make sure to enable the quality level you want (High, Medium, or Low)

1. **RTSP timeout too short**

   - Increase **RTSP Timeout** in Capture Settings
   - Default is reasonable, but slow networks may need more

1. **FFmpeg issues**

   - Check logs: `docker logs luxupt | grep -i ffmpeg`
   - RTSP capture requires FFmpeg, which is included in the container

______________________________________________________________________

## Capture Issues

### Rate Limit Errors (429)

**Symptom:** Captures fail with "rate limited" errors. You may see 429 errors in logs.

**Cause:** Too many API requests in a short time. UniFi Protect limits API requests (typically 10/second).

**Solutions:**

1. **Increase camera distribution offset**

   - Capture Settings → Camera Distribution
   - Increase Min Offset and Max Offset
   - This staggers captures across time

1. **Reduce capture frequency**

   - Use longer intervals (e.g., 120s instead of 60s)
   - Remove intervals you don't need

1. **Lower the rate limit buffer**

   - Capture Settings → Performance → Buffer
   - Lower values (e.g., 0.6) are more conservative

1. **Reduce concurrent cameras**

   - If you have many cameras, the system may be hitting limits
   - Disable capture on less important cameras

______________________________________________________________________

### High Failure Rate

**Symptom:** Many captures failing, high number in "Failed" statistic.

**Debugging:**

1. Check the **Activity Feed** on the dashboard — capture failures are logged with the camera name, interval, and error message
1. Check **Recent Failures** section on Cameras page for error messages
1. Check container logs: `docker logs luxupt --tail 100`
1. Click individual cameras to see per-camera success rates

**Common causes and solutions:**

| Error               | Cause                  | Solution                                |
| ------------------- | ---------------------- | --------------------------------------- |
| Connection timeout  | Slow network or camera | Increase Timeout in Capture Settings    |
| Camera disconnected | Camera offline         | Check camera in UniFi Protect           |
| Rate limited        | Too many requests      | See "Rate Limit Errors" above           |
| Bad request         | Unsupported quality    | Try different capture method or quality |

______________________________________________________________________

### Missing Captures / Low Capture Rate

**Symptom:** Capture rate is below 100%, some timestamps are missing cameras.

**Possible Causes:**

1. **Capture cycles falling behind**

   - Check the **Activity Feed** for "Capture cycle skipped" errors
   - This means the previous capture cycle was still running when the next one was scheduled
   - The system allows up to 2 concurrent cycles per interval before skipping to prevent resource exhaustion

1. **Individual camera timeouts**

   - Check the **Activity Feed** for "Failed to capture" errors for specific cameras
   - RTSP captures can timeout if the camera or network is slow
   - Increase RTSP Timeout in Capture Settings if needed

1. **Camera disabled or disconnected**

   - Check if the camera is active (Camera Settings → Camera Active)
   - Check if the camera is connected in UniFi Protect

**Solutions:**

- If cycles are being skipped: Check if RTSP capture times are too long. Consider switching slow cameras to API capture or reducing the number of cameras on short intervals.
- If individual cameras fail: Check Recent Failures on the Cameras page for specific error messages.
- Verify camera distribution is working: Check container logs for `"distributed": true` in capture cycle messages.

______________________________________________________________________

### Captures Stop Working

**Symptom:** Captures were working but stopped.

**Check:**

1. **Is capture enabled?**

   - Capture Settings → Capture Enabled should be ON

1. **Did the API key expire or get revoked?**

   - Check UniFi Protect → Your API Keys
   - Regenerate if needed

1. **Is the container healthy?**

   ```bash
   docker ps
   docker logs luxupt --tail 50
   ```

1. **Is the disk full?**

   - Check System page → Storage
   - Or: `df -h` on the Docker host

______________________________________________________________________

## Video Creation Issues

### Timelapse Creation Fails

**Symptom:** Jobs show "Failed" status on the Jobs page.

**Check the error message** on the Jobs page — it usually indicates the cause.

**Common causes:**

1. **Not enough images**

   - Need at least 2 images to create a video
   - Check if capture was working on that date

1. **Disk full**

   - Check available space: System page → Storage
   - Delete old images or videos to free space

1. **Out of memory**

   - FFmpeg memory usage scales with video resolution and number of source images
   - If FFmpeg is killed by the OS (OOM), the error will show "FFmpeg process killed (no output)"
   - Common in LXC containers or memory-constrained environments
   - **Solution:** Increase container/host memory — 16GB is a safe target for many high-resolution cameras
   - Manual creation may succeed while scheduled runs fail if other processes are competing for memory at the scheduled time

1. **FFmpeg timeout**

   - Large timelapses (thousands of images) may exceed the timeout
   - Increase Timeout in Scheduler → Video Encoding

1. **Corrupted images**

   - Some images may be corrupted
   - Check the source images in the Images browser

______________________________________________________________________

### Videos Are Too Short or Long

**Symptom:** Video duration doesn't match expectations.

**Explanation:**

Video duration = (Number of images) / (Frame rate)

Example: 1,440 images at 30 fps = 48 seconds of video

**Adjustments:**

- **Longer video**: Lower the frame rate (e.g., 15 fps instead of 30)
- **Shorter video**: Higher the frame rate (e.g., 60 fps)

Configure in Scheduler → Video Encoding → Frame Rate.

______________________________________________________________________

### Poor Video Quality

**Symptom:** Videos look blocky, blurry, or have artifacts.

**Solutions:**

1. **Lower the CRF value** (better quality)

   - Scheduler → Video Encoding → Quality (CRF)
   - Try 18-20 for high quality
   - Lower numbers = better quality, larger files

1. **Use a slower preset** (better compression)

   - Scheduler → Video Encoding → Preset
   - "slow" or "slower" produce better quality
   - Trade-off: encoding takes longer

1. **Check source image quality**

   - If source images are low resolution, video will be too
   - See "Low Resolution / Tiny Images" above

______________________________________________________________________

### Historical Timelapse Fails or the Option Is Unavailable

**The Recordings source is greyed out, or the Historical panel says something is missing.**

Historical timelapses read Protect's recording stream, which the integration API key cannot reach. They need `UNIFI_PROTECT_USERNAME` and `UNIFI_PROTECT_PASSWORD` as well.

It must be a **local Protect user**, not a Ubiquiti SSO account — SSO credentials will not authenticate against the recording API. Create a local user in the Protect UI with view access to the cameras you want.

**The job runs but returns no frames for some days.** Your NVR only holds recordings for as long as its retention allows. The panel shows the available range for the camera you picked; anything outside it has been overwritten. A camera added recently has a shorter range than the others.

**The end of my range was pulled back.** Protect needs roughly a minute before a frame is in the recording stream, so an end time too close to now is clamped. This is deliberate — without it the last few frames would 404.

**It is slow.** Fetching is rate-limited the same way live capture is, deliberately, so a long range takes a while. It runs as a normal job — watch it on the **Jobs** tab rather than waiting on the panel.

### Everything Is Filed Under the Wrong Day

Captures after a certain hour appear under the next day, or Today / Yesterday labels look off by one.

This is `TZ` (or `DISPLAY_TIMEZONE`) not matching your actual local zone. Times are stored as UTC; these settings decide which calendar day a capture belongs to. Left at UTC while you are in US Central, everything after about 6pm is filed under tomorrow.

Set it to your real zone and restart. Note that this does **not** move what is already on disk — the existing archive keeps the boundary it was written with, so expect a seam at the point you changed it.

### Scheduler Not Running

**Symptom:** No automatic timelapses being created.

**Check:**

1. **Is scheduler enabled?**

   - Timelapses page → Scheduler → Enable Scheduler should be ON

1. **Are cameras selected?**

   - Scheduler → Cameras & Intervals
   - At least one camera and one interval must be checked

1. **Is the run time in the past today?**

   - If you set it to 01:00 and it's now 14:00, it won't run until tomorrow

1. **Check container logs at the scheduled time:**

   ```bash
   docker logs luxupt | grep -i scheduler
   ```

______________________________________________________________________

## Connection Issues

### SSL Certificate Errors

**Symptom:** Connection fails with SSL/certificate errors.

**Solution:**

Set `UNIFI_PROTECT_VERIFY_SSL: "false"` in your compose file:

```yaml
environment:
  UNIFI_PROTECT_VERIFY_SSL: "false"
```

Or configure in the UI: Capture Settings → API Connection → Verify SSL → OFF

This is common with local UniFi installations that use self-signed certificates.

______________________________________________________________________

### Can't Access Web Interface

**Symptom:** Browser can't connect to `http://your-server:8080`.

**Check:**

1. **Is the container running?**

   ```bash
   docker ps | grep luxupt
   ```

1. **Is the port mapped correctly?**

   ```bash
   docker port luxupt
   ```

1. **Is something else using port 8080?**

   ```bash
   sudo lsof -i :8080
   ```

1. **Firewall blocking the port?**

   ```bash
   sudo ufw status  # Ubuntu
   sudo firewall-cmd --list-ports  # CentOS/RHEL
   ```

1. **Check container logs:**

   ```bash
   docker logs luxupt
   ```

______________________________________________________________________

## Authentication Issues

### Forgot Password

**Symptom:** Can't log in, don't remember password.

**If you set credentials in compose file:**

- Check your compose.yaml for `WEB_USERNAME` and `WEB_PASSWORD`

**If you created credentials in setup wizard:**

- You'll need to reset the database or add credentials to compose file

**Reset by adding env vars:**

Add to your compose.yaml:

```yaml
environment:
  WEB_USERNAME: admin
  WEB_PASSWORD: new-password
```

Restart the container. The env var credentials take priority.

______________________________________________________________________

### Login Rate Limited

**Symptom:** "Too many login attempts" error.

**Cause:** Failed login attempts triggered rate limiting.

**Solutions:**

1. **Wait** — Rate limit resets after 60 seconds (default)

1. **Restart container** — Clears rate limit state

   ```bash
   docker compose restart luxupt
   ```

1. **Adjust rate limit** (if you have many users):

   ```yaml
   environment:
     WEB_LOGIN_RATE_LIMIT: "10"  # Default is 5
   ```

______________________________________________________________________

### Session Expires Too Quickly

**Symptom:** Getting logged out frequently.

**Solution:**

Increase session duration in compose file:

```yaml
environment:
  ACCESS_TOKEN_EXPIRE_MINUTES: "20160"  # 14 days instead of 7
```

Default is 10080 minutes (7 days).

______________________________________________________________________

## Storage Issues

### Slow Web UI / Database on NFS

**Symptom:** Pages load slowly, deletion or other database operations take a long time or timeout. Most noticeable with large datasets.

**Cause:** The SQLite database is on NFS-mounted storage with high fsync latency. SQLite issues an fsync on every write transaction. On NAS devices with spinning disks and no SSD write cache (ZFS SLOG), each fsync can take 10-20ms vs under 2ms on local SSD. This adds up quickly across multiple queries per page load.

**Solution:** Move the database to local storage using the `DATABASE_DIR` environment variable. See [Separating Database from Output Storage](configuration.md#separating-database-from-output-storage-nfs) in the Configuration guide.

______________________________________________________________________

### Permission Denied / Read-Only Database After Migration

**Symptom:** Container crashes on startup with `sqlite3.OperationalError: attempt to write a readonly database`.

**Cause:** The database file or its directory is owned by `root` (or another user) but the container runs as `appuser` (uid 1000, gid 1000).

This commonly happens after copying the database to a new location (e.g., migrating from NFS to local disk with `cp`), since `cp` creates files owned by the user running the command.

**Solution:**

```bash
chown -R 1000:1000 /path/to/data/directory
```

All volume mounts that the container writes to must be writable by uid 1000.

______________________________________________________________________

### Disk Full

**Symptom:** Captures or video creation failing, disk usage at 100%.

**Solutions:**

1. **Delete old images**

   - Images page → Delete button
   - Filter by old dates and delete

1. **Delete old videos**

   - Timelapses page → Delete unwanted videos

1. **Enable automatic cleanup**

   - Scheduler → Keep Source Images → OFF
   - Images are deleted after successful video creation

1. **Add more storage**

   - Change the volume mount to a larger disk

______________________________________________________________________

### Images Not Appearing in Browser

**Symptom:** Captures show in statistics but images don't appear in browser.

**Possible causes:**

1. **Filter mismatch** — Check your filters (camera, date, interval)
1. **Images on disk but not in database** — Rare, but can happen if database was reset
1. **Thumbnail generation failing** — Check logs for thumbnail errors

______________________________________________________________________

## FAQ

### Why do some cameras show "RTSP Stream Not Tested"?

This means the RTSP detection test could not capture a frame from the camera's RTSP stream. The most common cause is that the camera is set to **Enhanced** encoding in UniFi Protect.

**Background:** UniFi Protect offers two encoding modes:

- **Standard** — Uses H.264, which is universally compatible with RTSP clients
- **Enhanced** — Uses H.265/HEVC, which provides better compression but does not work with most third-party RTSP integrations

This is not specific to LuxUPT. Home Assistant, Frigate, Homebridge, go2rtc, and other tools that consume RTSP streams from UniFi Protect all require Standard encoding.

**Fix:**

1. Open UniFi Protect
1. Go to each affected camera's Settings → Video
1. Change Encoding from **Enhanced** to **Standard**
1. In LuxUPT, click **Re-detect** on the camera to re-test RTSP

After switching to Standard, the RTSP stream test should succeed and show the camera's full resolution.

**Note:** You may have cameras with identical models and settings where some work and some don't. Double-check that encoding is set to Standard on every camera individually — this setting is per-camera, not global.

______________________________________________________________________

### Can I use Enhanced encoding for recording and Standard for RTSP?

No. UniFi Protect applies the encoding setting to both recording and RTSP streams. If you need RTSP access (for LuxUPT or any other integration), the camera must use Standard encoding.

______________________________________________________________________

### Does this affect API snapshot capture?

No. The API snapshot endpoint works regardless of encoding setting. Only RTSP stream capture is affected. If you don't need the higher resolution that RTSP provides, you can use API capture method instead and keep Enhanced encoding.

______________________________________________________________________

## Getting Help

If you can't resolve an issue:

1. **Check the logs:**

   ```bash
   docker logs luxupt --tail 200
   ```

1. **Include in your bug report:**

   - LuxUPT version (System page → Version)
   - UniFi Protect version
   - Camera models affected
   - Error messages from logs
   - Steps to reproduce

1. **Open an issue:** [GitHub Issues](https://github.com/luxardolabs/luxupt/issues)

______________________________________________________________________

## Documentation

- [Getting Started](getting-started.md) — Setup, first-run wizard, connecting to UniFi Protect
- [Web Interface Guide](web-interface.md) — Every page and panel explained
- [Configuration](configuration.md) — Environment variables, multi-site setup, storage
- **Troubleshooting** (this page)
