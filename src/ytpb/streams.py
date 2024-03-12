from collections.abc import MutableSet
from operator import attrgetter
from typing import Any, Callable, Iterator, Self

from ytpb.conditional import FORMAT_SPEC_RE, make_filter_from_expression
from ytpb.exceptions import QueryError
from ytpb.types import AudioOrVideoStream, AudioStream, SetOfStreams, VideoStream


def stream_comparison_function(stream: AudioOrVideoStream):
    match stream:
        case AudioStream():
            return (stream.mime_type, stream.audio_sampling_rate)
        case VideoStream():
            return (stream.mime_type, stream.height, stream.frame_rate)


class Streams(MutableSet):
    """Represents a set of `ytpb.info.RepresentationInfo` objects."""

    def __init__(self, iterable: list[AudioOrVideoStream] | None = None) -> None:
        self._elements = set()
        for value in iterable or []:
            if not isinstance(value, AudioOrVideoStream):
                raise ValueError
            self._elements.add(value)

    @classmethod
    def from_dicts(cls, dicts: list[dict]):
        streams = cls()
        stream: AudioOrVideoStream
        for stream_dict in dicts:
            if "audio" in stream_dict["mime_type"]:
                stream = AudioStream(**stream_dict)
            else:
                stream = VideoStream(**stream_dict)
            streams.add(stream)
        return streams

    def __len__(self):
        return len(self._elements)

    def __ior__(self, other: Self) -> Self:
        self.update(other)
        return self

    def __iter__(self) -> Iterator[AudioOrVideoStream]:
        return iter(self._elements)

    def __contains__(self, item: Any) -> bool:
        for stream in self:
            if stream.itag == item.itag:
                return True
        return False

    def add(self, value: AudioOrVideoStream):
        self._elements.add(value)

    def discard(self, value: AudioOrVideoStream):
        try:
            self._elements.remove(value)
        except KeyError:
            pass

    def get_by_itag(self, itag: str) -> AudioOrVideoStream | None:
        for stream in self:
            if stream.itag == itag:
                return stream
        return None

    def filter(self, predicate: Callable[[AudioOrVideoStream], bool]) -> SetOfStreams:
        return self.__class__(list(filter(predicate, self._elements)))

    def query(
        self, format_spec: str, aliases: dict[str, str] | None = None
    ) -> list[AudioOrVideoStream]:
        if not format_spec:
            return []

        functions_map = {
            "best": lambda streams: sorted(streams, key=attrgetter("quality"))[-1],
        }

        if matched := FORMAT_SPEC_RE.search(format_spec):
            if function_name := matched.group("function"):
                expression = matched.group("expr")
            else:
                expression = matched.group("just_expr")
        else:
            raise QueryError(f"Format spec is invalid: {format_spec}")

        expression_filter = make_filter_from_expression(expression, aliases)
        queried: list[AudioOrVideoStream] = list(
            filter(expression_filter, self._elements)
        )
        if queried:
            if function_name:
                try:
                    queried = [functions_map[function_name](queried)]
                except KeyError:
                    raise QueryError(f"Unknown function: {function_name}")

        return queried
