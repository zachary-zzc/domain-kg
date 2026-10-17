"""Verify a complete retained package before sealing it or starting recovery."""

from collections.abc import AsyncIterator, Awaitable, Callable

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import SourceVersion
from domain_kg.operations.backup_models import AuthoritySection, BackupManifest
from domain_kg.operations.packages import PackageObject, PackagePage, PackagePart
from domain_kg.operations.storage import PackageStore
from domain_kg.revisions.models import PublishedView
from domain_kg.revisions.projections import CurationEmbeddedDocument, EmbeddedDocument


def inconsistent() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "The retained package is inconsistent.")


class PackageReader:
    def __init__(self, store: PackageStore, guard: Callable[[], Awaitable[object]]) -> None:
        self.store, self.guard = store, guard

    async def body(self, reference: PackageObject, maximum: int = 1024 * 1024) -> bytes:
        if reference.size_bytes > maximum:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Backup metadata exceeds its read budget.")
        chunks = []
        async for chunk in self.store.open(reference):
            await self.guard()
            chunks.append(chunk)
        return b"".join(chunks)

    async def pages(self, manifest: BackupManifest) -> AsyncIterator[PackagePage]:
        for index, reference in enumerate(manifest.pages):
            page = PackagePage.model_validate_json(await self.body(reference))
            if (page.org_id, page.kg_id, page.backup_id, page.attempt_id, page.index) != (
                manifest.org_id,
                manifest.kg_id,
                manifest.backup_id,
                manifest.attempt_id,
                index,
            ):
                raise inconsistent()
            yield page

    async def verify(self, manifest: BackupManifest) -> None:
        try:
            await self._verify(manifest)
        except (ValidationError, ValueError, KeyError, TypeError):
            raise inconsistent() from None

    async def _verify(self, manifest: BackupManifest) -> None:
        objects = set()
        originals = set()
        projections: list[PackagePart] = []
        root: PublishedView | None = None
        metadata = 0
        copied_bytes = 0
        async for page in self.pages(manifest):
            for part in page.parts:
                if part.object.id in objects:
                    raise inconsistent()
                objects.add(part.object.id)
                copied_bytes += part.object.size_bytes
                if len(objects) > 100000 or copied_bytes > 2 * 1024**3:
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Backup exceeds its read budget.")
                if part.kind == "authority":
                    section = AuthoritySection.model_validate_json(await self.body(part.object))
                    if (section.org_id, section.kg_id, section.backup_id) != (
                        manifest.org_id,
                        manifest.kg_id,
                        manifest.backup_id,
                    ):
                        raise inconsistent()
                    if section.table == "published_view":
                        if (
                            root is not None
                            or section.area != "control"
                            or len(section.records) != 1
                        ):
                            raise inconsistent()
                        root = PublishedView.model_validate(section.records[0])
                    elif section.table == "kg_metadata":
                        expected = {
                            "id": str(manifest.kg_id),
                            "published_revision": manifest.snapshot.revision,
                            "published_manifest_id": (
                                str(manifest.snapshot.manifest_id)
                                if manifest.snapshot.manifest_id
                                else None
                            ),
                            "generation": str(manifest.snapshot.generation),
                            "visibility_revision": manifest.snapshot.visibility_revision,
                            "revision": manifest.snapshot.metadata_revision,
                            "configuration_epoch": manifest.snapshot.configuration_epoch,
                        }
                        if (
                            section.area != "control"
                            or len(section.records) != 1
                            or any(
                                section.records[0].get(
                                    field, 0 if field == "configuration_epoch" else None
                                )
                                != value
                                for field, value in expected.items()
                            )
                        ):
                            raise inconsistent()
                        metadata += len(section.records)
                elif part.kind in ("source_projection", "curation_projection"):
                    projections.append(part)
                else:
                    async for _ in self.store.open(part.object):
                        await self.guard()
                    if part.kind == "original":
                        if part.source in originals:
                            raise inconsistent()
                        originals.add(part.source)
        if (
            root is None
            or metadata != 1
            or len(objects) != manifest.part_count
            or (
                root.org_id,
                root.kg_id,
                root.revision,
                root.generation,
                root.manifest_id,
                len(root.sources),
                len(root.curations),
            )
            != (
                manifest.org_id,
                manifest.kg_id,
                manifest.snapshot.revision,
                manifest.snapshot.generation,
                manifest.snapshot.manifest_id,
                manifest.source_count,
                manifest.curation_count,
            )
        ):
            raise inconsistent()
        if any(source not in originals for source in root.sources):
            raise inconsistent()
        bindings = {(binding.source, binding.curation): binding for binding in root.projections}
        seen = set()
        for part in projections:
            key = (part.source, part.curation)
            binding = bindings.get(key)
            if key in seen or binding is None or part.object.sha256 != binding.content_hash:
                raise inconsistent()
            seen.add(key)
            body = await self.body(part.object, 50 * 1024 * 1024)
            if part.source:
                document = EmbeddedDocument.model_validate_json(body)
                source = SourceVersion(
                    **{key: getattr(document, key) for key in SourceVersion.model_fields}
                )
                if source != part.source or document.import_origin != binding.import_origin:
                    raise inconsistent()
            elif CurationEmbeddedDocument.model_validate_json(body).curation != part.curation:
                raise inconsistent()
        if seen != set(bindings):
            raise inconsistent()
        await self.guard()
