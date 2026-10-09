"""ffmpeg / ffprobe invocation: the encode command, its pipes, probing and thumbnails.

Pure process plumbing with no service state, shared by the timelapse worker
(``app/workers/timelapse_service.py``) and the job processor
(``app/services/core/job_core_service.py``), which used to carry identical private copies of
the probe and thumbnail code.
"""

import asyncio
import json
import logging
import re
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.utils import async_fs

logger = logging.getLogger(__name__)

_FRAME_PATTERN = re.compile(r"frame=\s*(\d+)")


async def concat_input_args(
    concat_file: Path, image_files: list[Path], frame_rate: int
) -> list[str]:
    """Write an ffconcat list of ``image_files`` and return the input args that read it.

    The list carries no per-file ``duration`` lines; the input framerate is set with ``-r`` so
    each image maps 1:1 to an output frame. Per-file durations caused cumulative
    floating-point drift that dropped ~16% of frames on long sequences.
    """

    def _write() -> None:
        with concat_file.open("w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\n")
            for src in image_files:
                escaped = str(src.resolve()).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

    await asyncio.to_thread(_write)
    return ["-r", str(frame_rate), "-f", "concat", "-safe", "0", "-i", str(concat_file)]


def glob_input_args(input_pattern: str, frame_rate: int) -> list[str]:
    """Input args that read every file matching a glob ``input_pattern``."""
    return ["-r", str(frame_rate), "-pattern_type", "glob", "-i", input_pattern]


def encode_command(
    input_args: list[str],
    output_path: Path,
    *,
    crf: int,
    preset: str,
    pixel_format: str,
) -> list[str]:
    """The HEVC encode: progress on stdout (``-progress pipe:1``), warnings only on stderr."""
    return [
        "ffmpeg",
        "-y",  # Always overwrite (callers check existence before encoding)
        "-loglevel",
        "warning",
        "-nostats",  # progress comes from -progress pipe:1, not the stats line
        "-progress",
        "pipe:1",
        *input_args,
        "-c:v",
        "libx265",
        "-x265-params",
        f"log-level=0:crf={crf}",
        "-preset",
        preset,
        "-pix_fmt",
        pixel_format,
        "-tag:v",
        "hvc1",
        "-movflags",
        "+faststart",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        str(output_path),
    ]


async def drain_stderr(stderr: asyncio.StreamReader) -> str:
    """Drain stderr to prevent pipe buffer deadlock.

    FFmpeg blocks if the stderr pipe buffer fills up (64KB), so this reads it concurrently
    during encoding. Returns the collected output for error reporting on failure.
    """
    chunks: list[str] = []
    try:
        while True:
            chunk = await stderr.read(4096)
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="ignore"))
    except asyncio.CancelledError:
        # The drain task is cancelled when the process ends -- expected, not a failure.
        pass
    except OSError as read_error:
        logger.warning("Failed reading ffmpeg stderr", extra={"error": str(read_error)})
    return "".join(chunks)


async def read_progress(
    stdout: asyncio.StreamReader, on_frame: Callable[[int], Awaitable[None]]
) -> None:
    """Parse ``-progress pipe:1`` output and report each frame count to ``on_frame``."""
    try:
        buffer = ""
        while True:
            chunk = await stdout.read(1024)
            if not chunk:
                break

            buffer += chunk.decode("utf-8", errors="ignore")
            lines = buffer.split("\n")
            buffer = lines[-1]  # keep the incomplete line

            for line in lines[:-1]:
                frame_match = _FRAME_PATTERN.search(line)
                if frame_match:
                    await on_frame(int(frame_match.group(1)))

    except asyncio.CancelledError:
        # Normal when the process completes
        pass
    except Exception as e:
        logger.exception("Error tracking FFmpeg progress", extra={"error": str(e)})


async def probe_video_metadata(
    output_path: Path, frame_rate: int, probe_timeout: int
) -> tuple[float, str | None, int]:
    """Probe a video file. Returns (duration_seconds, resolution, frame_count)."""
    duration_seconds = 0.0
    resolution = None
    frame_count = 0

    def run_ffprobe() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=probe_timeout,
        )

    try:
        result = await asyncio.to_thread(run_ffprobe)
        if result.returncode == 0:
            probe_data = json.loads(result.stdout)

            if "format" in probe_data and "duration" in probe_data["format"]:
                duration_seconds = float(probe_data["format"]["duration"])

            for stream in probe_data.get("streams", []):
                if stream.get("codec_type") == "video":
                    width = stream.get("width", 0)
                    height = stream.get("height", 0)
                    if width and height:
                        resolution = f"{width}x{height}"
                    if "nb_frames" in stream:
                        frame_count = int(stream["nb_frames"])
                    elif duration_seconds > 0:
                        frame_count = int(duration_seconds * frame_rate)
                    break
    except Exception as e:
        logger.warning("Could not probe video metadata", extra={"error": str(e)})

    return duration_seconds, resolution, frame_count


async def generate_thumbnail(
    output_path: Path, duration_seconds: float, probe_timeout: int
) -> str | None:
    """Extract one frame as a 480px JPEG beside the video. Returns its path or None."""
    thumb_path = output_path.parent / (output_path.stem + "_thumb.jpg")
    seek_time = min(1.0, duration_seconds * 0.1) if duration_seconds > 0 else 0

    def run_ffmpeg_thumb() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(seek_time),
                "-i",
                str(output_path),
                "-vframes",
                "1",
                "-vf",
                "scale=480:-1",
                "-q:v",
                "3",
                str(thumb_path),
            ],
            capture_output=True,
            text=True,
            timeout=probe_timeout,
        )

    try:
        result = await asyncio.to_thread(run_ffmpeg_thumb)
        if result.returncode == 0 and await async_fs.path_exists(thumb_path):
            logger.info("Generated thumbnail", extra={"path": str(thumb_path)})
            return str(thumb_path)
        logger.warning("Thumbnail generation failed", extra={"stderr": result.stderr})
    except Exception as e:
        logger.warning("Could not generate thumbnail", extra={"error": str(e)})

    return None


def format_file_size(size_bytes: int) -> str:
    """Format a byte count as B / KB / MB / GB / TB."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_duration(seconds: float) -> str:
    """Format seconds as `1h 2m 3s` / `2m 3s` / `3s`."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
