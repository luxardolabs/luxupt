"""A file route serves only a path inside its own output directory."""

import logging
from pathlib import Path

import pytest

from app.core import config
from app.services.core import _path_security as path_security


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Point each output directory at its own folder under tmp_path."""
    paths = {
        "IMAGE_OUTPUT_PATH": tmp_path / "images",
        "VIDEO_OUTPUT_PATH": tmp_path / "videos",
        "THUMBNAIL_CACHE_PATH": tmp_path / "thumbnails",
    }
    for name, path in paths.items():
        path.mkdir()
        monkeypatch.setattr(config, name, path)
    return paths


def test_a_path_inside_its_root_is_returned(roots: dict[str, Path]) -> None:
    image = str(roots["IMAGE_OUTPUT_PATH"] / "cam" / "60s" / "a.jpg")
    video = str(roots["VIDEO_OUTPUT_PATH"] / "2026" / "11" / "cam.mp4")
    thumb = str(roots["THUMBNAIL_CACHE_PATH"] / "t.jpg")

    assert path_security.validate_image_path(image, camera_id="c", timestamp=1) == image
    assert path_security.validate_video_path(video, timelapse_id=1) == video
    assert path_security.validate_thumbnail_path(thumb, timelapse_id=1) == thumb


@pytest.mark.parametrize("value", [None, ""])
def test_a_missing_path_is_refused(roots: dict[str, Path], value: str | None) -> None:
    assert path_security.validate_image_path(value, camera_id="c", timestamp=1) is None
    assert path_security.validate_video_path(value, timelapse_id=1) is None
    assert path_security.validate_thumbnail_path(value, timelapse_id=1) is None
    assert path_security.is_safe_image_path(value) is False
    assert path_security.is_safe_video_path(value) is False
    assert path_security.is_safe_thumbnail_path(value) is False


def test_a_traversal_out_of_its_root_is_refused_and_logged(
    roots: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    escape = str(roots["IMAGE_OUTPUT_PATH"] / ".." / ".." / "etc" / "passwd")
    with caplog.at_level(logging.WARNING, logger=path_security.__name__):
        assert (
            path_security.validate_image_path(escape, camera_id="c", timestamp=1)
            is None
        )
        assert (
            path_security.validate_video_path(escape, timelapse_id=7, kind="thumb")
            is None
        )
        assert path_security.validate_thumbnail_path(escape, timelapse_id=7) is None
    assert [r.getMessage() for r in caplog.records] == [
        "Path traversal attempt blocked for image",
        "Path traversal attempt blocked for video",
        "Path traversal attempt blocked for thumbnail",
    ]


def test_a_file_in_another_output_root_is_refused(roots: dict[str, Path]) -> None:
    image = str(roots["IMAGE_OUTPUT_PATH"] / "a.jpg")
    assert path_security.validate_video_path(image, timelapse_id=1) is None
    assert path_security.validate_thumbnail_path(image, timelapse_id=1) is None


def test_a_symlink_out_of_its_root_is_refused(
    roots: dict[str, Path], tmp_path: Path
) -> None:
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")
    link = roots["IMAGE_OUTPUT_PATH"] / "link.jpg"
    link.symlink_to(outside)
    assert (
        path_security.validate_image_path(str(link), camera_id="c", timestamp=1) is None
    )


def test_a_path_that_cannot_be_resolved_is_refused(roots: dict[str, Path]) -> None:
    assert path_security.is_safe_image_path("bad\x00path") is False
