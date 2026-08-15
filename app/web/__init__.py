"""
LuxUPT Web Interface

Provides a clean, modern web interface for:
- Monitoring service status
- Browsing captured images
- Creating timelapses on-demand
- Viewing existing videos

All operations are async and non-blocking to ensure
the core timelapse functionality is never impacted.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

# The VERSION file is the single source of truth (repo.version_single_source). Prefer the
# installed package metadata (built from it); fall back to reading VERSION in a source checkout.
try:
    __version__ = _pkg_version("luxupt")
except PackageNotFoundError:
    _version_file = Path(__file__).resolve().parents[2] / "VERSION"
    __version__ = (
        _version_file.read_text(encoding="utf-8").strip()
        if _version_file.is_file()
        else "dev"
    )
