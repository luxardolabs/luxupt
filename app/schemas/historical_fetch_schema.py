"""Result of a historical (Protect-recording) fetch run."""

from dataclasses import dataclass


@dataclass
class HistoricalFetchResult:
    """What one historical fetch run did — returned across the core/view boundary.

    Lives in schemas/ because it CROSSES a layer boundary. Defined inside the service it was
    invisible to the schema-scanning rules — fw.enum_dto_fields_enum_typed scans schemas/ —
    so any closed-set field on it would have gone unchecked while the enum family read green
    (fw.dto_in_schemas, LUXSIGNAL-18).
    """

    frames_attempted: int
    frames_succeeded: int
    no_recording: int  # HTTP 404 — gap in Protect's recordings, not our fault
    errors: int  # auth, network, malformed responses
    elapsed_seconds: float

    @property
    def frames_failed(self) -> int:
        return self.no_recording + self.errors
