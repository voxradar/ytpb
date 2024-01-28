from datetime import datetime, time, timedelta
from enum import auto, StrEnum
from typing import Literal, NamedTuple

import click
import structlog
from timedelta_isoformat import timedelta as isotimedelta

from ytpb import types
from ytpb.conditional import FORMAT_SPEC_RE
from ytpb.types import SegmentSequence
from ytpb.utils.date import ensure_date_aware

logger = structlog.get_logger(__name__)


class PointInStreamParamType(click.ParamType):
    def __init__(self, allowed_literals: list[str] = None):
        if allowed_literals:
            self.allowed_literals = allowed_literals
        else:
            self.allowed_literals = []

    def convert(self, value: str, param, ctx) -> types.AbsolutePointInStream | str:
        match value:
            # Allowed literals
            case literal if value in self.allowed_literals:
                output = literal
            # Time of today
            case x if x[0] == "T" or (":" in x and "-" not in x):
                parsed_time = time.fromisoformat(x)
                today = datetime.now()
                output = today.replace(
                    hour=parsed_time.hour,
                    minute=parsed_time.minute,
                    second=parsed_time.second,
                    microsecond=parsed_time.microsecond,
                )
                output = output.astimezone(parsed_time.tzinfo)
            # Segment sequence
            case sequence if value.isdecimal():
                output = SegmentSequence(sequence)
            # Seems like a date and time
            case value if value[:4].isdecimal():
                try:
                    output = ensure_date_aware(datetime.fromisoformat(value))
                except ValueError:
                    message = f"'{value}' does not match ISO 8601 date format."
                    self.fail(message, param, ctx)
            case _:
                self.fail("Option doesn't allow '{}' value", param, ctx)
        return output


class InputRewindInterval(NamedTuple):
    start: SegmentSequence | datetime | timedelta
    end: SegmentSequence | datetime | timedelta | Literal["now"]


class RewindIntervalParamType(click.ParamType):
    error_message_fmt = "Incorrectly formated interval: {}"

    def _replace_datetime_components(self, date: datetime, value: str):
        date_units = {"Y": "year", "M": "month", "D": "day"}
        time_units = {"H": "hour", "M": "minute", "S": "second"}

        components_to_replace = {}

        for part, units in zip(value.split("T"), (date_units, time_units)):
            if not part:
                continue
            previous = 0
            for i, c in enumerate(part):
                if c in units:
                    components_to_replace[units[c]] = int(part[previous:i])
                    previous = i + 1

        return date.replace(**components_to_replace)

    def _parse_interval_part(
        self,
        part: str,
        end: bool = False,
    ) -> int | str | Literal["now", ".."] | datetime | timedelta | isotimedelta:
        match part:
            # Sequence number
            case x if x.isdecimal():
                output = int(x)
            # Duration
            case x if x[0] == "P":
                output = isotimedelta.fromisoformat(x)
            # Replacing components
            case x if set(x) & set("DHMS"):
                output = x
            # Time of today
            case x if x[0] == "T" or (":" in x and "-" not in x):
                parsed_time = time.fromisoformat(x)
                today = datetime.now()
                output = today.replace(
                    hour=parsed_time.hour,
                    minute=parsed_time.minute,
                    second=parsed_time.second,
                    microsecond=parsed_time.microsecond,
                )
                output = output.astimezone(parsed_time.tzinfo)
            # Date and time
            case x if "T" in x:
                output = datetime.fromisoformat(x)
            case "now" | ".." as x:
                output = x
            case _:
                raise click.BadParameter(f"Incorrectly formatted part: {part}")

        return output

    def convert(
        self, value: str, param: click.Parameter, ctx: click.Context
    ) -> InputRewindInterval:
        parts = value.split("/")
        if len(parts) != 2:
            raise click.BadParameter("Interval must be formatted as '<start>/<end>'")

        start_part, end_part = parts

        try:
            parsed_start = self._parse_interval_part(start_part)
        except ValueError as exc:
            raise click.BadParameter(f"'{start_part}', {exc}.")

        try:
            parsed_end = self._parse_interval_part(end_part)
        except ValueError as exc:
            raise click.BadParameter(f"'{end_part}', {exc}.")

        match parsed_start, parsed_end:
            # Two durations
            case [timedelta(), timedelta()]:
                raise click.BadParameter("Two durations are ambiguous.")
            case ["now" | ".." as x, _]:
                raise click.BadParameter(
                    f"Keyword '{x}' is only allowed for the end part."
                )
            # Duration and '..'
            case [timedelta(), ".."]:
                raise click.BadParameter("Keyword '..' not compatible with duration.")
            # Anything compatible and 'now' or '..'
            case [int() | datetime() | timedelta(), "now" | ".."]:
                start = parsed_start
                end = parsed_end
            # Replacing components and date and time
            case (str(), datetime()) | (datetime(), str()):
                if isinstance(parsed_start, str):
                    end = parsed_end
                    start = self._replace_datetime_components(end, parsed_start)
                else:
                    start = parsed_start
                    end = self._replace_datetime_components(start, parsed_end)
            # Replacing components and anything remaining, non-compatible
            case (str(), _) | (_, str()):
                raise click.BadParameter(
                    "Replacement components is only compatible with date and time."
                )
            # Remaining and compatible
            case [_, _]:
                start = parsed_start
                end = parsed_end

        if type(start) == type(end) and start >= end:
            raise click.BadParameter(
                f"Start is ahead or equal to end: {start} >= {end}"
            )

        return start, end


class FormatSpecType(StrEnum):
    AUDIO = auto()
    VIDEO = auto()


class FormatSpecParamType(click.ParamType):
    def __init__(self, format_spec_type: FormatSpecType):
        self.format_spec_type = format_spec_type

    def convert(
        self, value: str, param: click.Parameter, ctx: click.Context
    ) -> str | None:
        if value in ("", "none"):
            output = None
        else:
            guard_condition = f"mime_type contains {self.format_spec_type.value}"
            if matched := FORMAT_SPEC_RE.match(value):
                if matched.group("function"):
                    replace_with = f"{guard_condition} and [{matched.group('expr')}]"
                    output = value.replace(matched.group("expr"), replace_with)
                else:
                    output = f"{guard_condition} and [{value}]"

        return output
