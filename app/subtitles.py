import re
from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass(frozen=True)
class SubtitleLine:
    text: str
    start_time: str
    end_time: str


def is_study_worthy(line: SubtitleLine) -> bool:
    text = line.text.strip()
    if not text or len(text) < 2:
        return False
    if (text.startswith("*") and text.endswith("*")) or (
        text.startswith("[") and text.endswith("]")
    ):
        return False
    return any(character.isalpha() for character in text)


_TIMING = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)
_TAG = re.compile(r"<[^>]+>")
_PARENTHETICAL = re.compile(r"\([^()\n]*\)\s*")


def clean_subtitle_text(text: str) -> str:
    text = re.sub(_TAG, "", text)
    text = _PARENTHETICAL.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_srt(content: str) -> list[SubtitleLine]:
    lines: list[SubtitleLine] = []
    blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    for block in blocks:
        block_lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next(
            (index for index, line in enumerate(block_lines) if _TIMING.fullmatch(line)),
            None,
        )
        if timing_index is None:
            continue
        timing = _TIMING.fullmatch(block_lines[timing_index])
        if timing is None:
            continue
        text = " ".join(block_lines[timing_index + 1 :])
        text = clean_subtitle_text(text)
        if text:
            lines.append(
                SubtitleLine(
                    text=text,
                    start_time=timing.group("start"),
                    end_time=timing.group("end"),
                )
            )
    return lines


def _clock_to_seconds(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return float(value[:-1]) if value.endswith("s") else float(value)


def _seconds_to_clock(seconds: float) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"


def parse_ttml(content: str) -> list[SubtitleLine]:
    root = ElementTree.fromstring(content)
    parsed: list[SubtitleLine] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "p":
            continue
        begin = element.attrib.get("begin")
        end = element.attrib.get("end")
        text = clean_subtitle_text("".join(element.itertext()))
        if not begin or not end or not text:
            continue
        parsed.append(
            SubtitleLine(
                text=text,
                start_time=_seconds_to_clock(_clock_to_seconds(begin)),
                end_time=_seconds_to_clock(_clock_to_seconds(end)),
            )
        )
    return parsed


def parse_subtitles(content: str, suffix: str) -> list[SubtitleLine]:
    if suffix.lower() in {".xml", ".ttml", ".dfxp"}:
        return parse_ttml(content)
    return parse_srt(content)
