"""Explicit bounded Unicode/Chinese decoding; no lossy replacement or encoding guessing."""

import codecs

from domain_kg.errors import DomainError, ErrorCode

VERSION = "text-unicode-gb18030-v1"


def codecs_for(prefix: bytes) -> tuple[str, ...]:
    if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return ("utf-32",)
    if prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return ("utf-16",)
    return ("utf-8-sig", "gb18030")


def check_text(text: str) -> None:
    if any(ord(c) < 32 and c not in "\t\r\n\f" for c in text):
        raise UnicodeError("Non-text control character")


class TextValidator:
    """Streaming upload validation uses the same encodings as the isolated decoder."""

    def __init__(self) -> None:
        self.header = b""
        self.decoders: list[codecs.IncrementalDecoder] | None = None

    def decode(self, part: bytes, *, final: bool = False) -> None:
        if self.decoders is None:
            self.header += part
            if len(self.header) < 4 and not final:
                return
            self.decoders = [
                codecs.getincrementaldecoder(c)("strict") for c in codecs_for(self.header)
            ]
            part, self.header = self.header, b""
        valid = []
        for decoder in self.decoders:
            try:
                check_text(decoder.decode(part, final=final))
            except UnicodeError:
                continue
            valid.append(decoder)
        self.decoders = valid
        if not valid:
            raise UnicodeError("Unsupported or malformed text encoding")


def decode_text(data: bytes) -> tuple[str, str | None]:
    for codec in codecs_for(data):
        try:
            text = data.decode(codec, errors="strict")
            check_text(text)
        except UnicodeError:
            continue
        return text, None if codec == "utf-8-sig" else "TEXT_ENCODING_" + codec.upper().replace(
            "-", ""
        )
    raise DomainError(
        ErrorCode.UNSUPPORTED_FORMAT, "Use UTF-8, BOM-marked Unicode, or GB18030 text."
    )
