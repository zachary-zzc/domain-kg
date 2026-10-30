"""Validate the whole Office container before invoking native XML/library readers."""

import io
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.models import ParseOptions


@contextmanager
def office_archive(data: bytes, options: ParseOptions) -> Iterator[ZipFile]:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (
                len(members) > 2048
                or sum(m.file_size for m in members) > options.max_expanded_bytes
            ):
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Expanded archive exceeds the parser limit."
                )
            names: set[str] = set()
            for item in members:
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.filename in names
                    or item.flag_bits & 1
                ):
                    raise DomainError(
                        ErrorCode.UNSUPPORTED_FORMAT, "Unsafe or encrypted archive entry."
                    )
                names.add(item.filename)
                if item.filename.endswith((".xml", ".rels")):
                    if item.file_size > min(options.max_expanded_bytes, 32 * 1024 * 1024):
                        raise DomainError(
                            ErrorCode.QUOTA_EXCEEDED, "Office XML exceeds the parser limit."
                        )
                    fromstring(
                        archive.read(item),
                        forbid_dtd=True,
                        forbid_entities=True,
                        forbid_external=True,
                    )
            yield archive
    except (BadZipFile, ParseError, DefusedXmlException) as exc:
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Malformed Office archive.") from exc
