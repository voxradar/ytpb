import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import av
import structlog

from ytpb.exceptions import YtpbError
from ytpb.types import SegmentSequence, Timestamp
from ytpb.utils.other import US_TO_S

logger = structlog.get_logger(__name__)


@dataclass
class SegmentMetadata:
    sequence_number: SegmentSequence
    ingestion_walltime: Timestamp
    ingestion_uncertainty: float
    stream_duration: float
    max_dvr_duration: float
    target_duration: float
    first_frame_time: Timestamp
    first_frame_uncertainty: float
    streamable: str | None = None
    encoding_alias: str | None = None


@dataclass
class Segment:
    def __init__(self) -> None:
        self.local_path: Path | None = None
        self.metadata: SegmentMetadata | None = None
        self.sequence: SegmentSequence | None = None
        self.is_partial: bool | None = None

    @classmethod
    def from_file(cls, path: Path) -> "Segment":
        segment = cls()

        with open(path, "rb") as f:
            content = f.read()
            segment.local_path = path

        segment.metadata = Segment.parse_youtube_metadata(content)
        segment.sequence = segment.metadata.sequence_number

        return segment

    @classmethod
    def from_bytes(cls, content: bytes) -> "Segment":
        segment = cls()
        segment.metadata = Segment.parse_youtube_metadata(content)
        segment.sequence = segment.metadata.sequence_number
        segment.is_partial = True

        return segment

    @property
    def ingestion_start_date(self):
        timestamp = self.metadata.ingestion_walltime
        return datetime.fromtimestamp(timestamp, timezone.utc)

    @property
    def ingestion_end_date(self):
        return self.ingestion_start_date + timedelta(seconds=self.get_actual_duration())

    @staticmethod
    def parse_youtube_metadata(content: bytes) -> SegmentMetadata:
        optional_fields = ("Streamable", "Encoding-Alias")

        def _search_for_metadata_field(
            name: str, content: bytes, optional: bool = False
        ) -> bytes | None:
            if matched := re.search(rf"{name}:\s(.+)\r\n".encode(), content):
                value = matched.group(1)
            else:
                if not optional:
                    raise YtpbError(f"Failed to parse metadata field: {name}")
                value = None
            return value

        def _convert_to_float_in_s(value: bytes) -> float:
            return float(value.decode()) / (1 / US_TO_S)

        def _convert_to_timestamp_in_s(value: bytes) -> Timestamp:
            return _convert_to_float_in_s(value)

        metadata_fields_map = (
            ("Sequence-Number", lambda x: int(x.decode())),
            ("Ingestion-Walltime-Us", _convert_to_timestamp_in_s),
            ("Ingestion-Uncertainty-Us", _convert_to_float_in_s),
            ("Stream-Duration-Us", _convert_to_float_in_s),
            ("Max-Dvr-Duration-Us", _convert_to_float_in_s),
            ("Target-Duration-Us", _convert_to_float_in_s),
            ("Streamable", lambda x: x.decode()),
            ("First-Frame-Time-Us", _convert_to_timestamp_in_s),
            ("First-Frame-Uncertainty-Us", _convert_to_float_in_s),
            ("Encoding-Alias", lambda x: x.decode()),
        )

        parsed_metadata_fields = {}
        for name, cast_func in metadata_fields_map:
            value_bytes = _search_for_metadata_field(
                name, content, optional=name in optional_fields
            )
            if value_bytes:
                value = cast_func(value_bytes)
                name_as_key = name.removesuffix("-Us").lower().replace("-", "_")
                parsed_metadata_fields[name_as_key] = value

        return SegmentMetadata(**parsed_metadata_fields)

    def get_actual_duration(self) -> float:
        with av.open(str(self.local_path)) as container:
            first_packet, *_, last_packet = list(container.demux())[:-1]
            end_timestamp = last_packet.pts + last_packet.duration
            duration = (end_timestamp - first_packet.pts) * float(
                first_packet.time_base
            )
            return duration
