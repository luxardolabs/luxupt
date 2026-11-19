"""The boot banner logs the build it runs and the paths it was configured with."""

import logging

import pytest

from app.core import startup


def test_banner_logs_the_build_version(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BUILD_VERSION", "2099.01.0")
    monkeypatch.setenv("BUILD_TIMESTAMP", "2099-01-01T00:00:00Z")
    with caplog.at_level(logging.INFO, logger=startup.__name__):
        startup.print_banner()

    info = next(r for r in caplog.records if r.getMessage() == "Application info")
    assert info.__dict__["version"] == "2099.01.0"
    assert info.__dict__["build_date"] == "2099-01-01T00:00:00Z"
    system = next(r for r in caplog.records if r.getMessage() == "System info")
    assert system.__dict__["python"]


def test_banner_without_build_args_says_dev(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("BUILD_VERSION", raising=False)
    monkeypatch.delenv("BUILD_TIMESTAMP", raising=False)
    with caplog.at_level(logging.INFO, logger=startup.__name__):
        startup.print_banner()

    info = next(r for r in caplog.records if r.getMessage() == "Application info")
    assert info.__dict__["version"] == "dev"
    assert info.__dict__["build_date"] == "unknown"


def test_configuration_logs_the_output_paths(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=startup.__name__):
        startup.print_configuration()

    record = next(
        r for r in caplog.records if r.getMessage() == "Startup configuration"
    )
    for key in ("web_port", "image_path", "video_path", "thumbnail_path", "log_level"):
        assert key in record.__dict__
