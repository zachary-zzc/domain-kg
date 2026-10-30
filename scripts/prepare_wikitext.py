"""Prepare approximately 200 MB of unique source-only WikiText articles, without API calls."""

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import quote

REVISION = "b08601e04326c79dfdd32d625aee71d232d685c3"
SHARD = "wikitext-103-raw-v1/train-00000-of-00002.parquet"
SHARD_SHA256 = "74da360f23826045b3e6ac6375411fdb15f003030aa74f2596ed08b857cb9212"
SOURCE = f"https://huggingface.co/datasets/Salesforce/wikitext/resolve/{REVISION}/{SHARD}"
CARD = f"https://huggingface.co/datasets/Salesforce/wikitext/blob/{REVISION}/README.md"
HEADER = re.compile(r"\s*= ([^=\n]+?) =\s*")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def articles(rows: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Keep exact source rows and whole articles; omit an incomplete shard tail."""
    title: str | None = None
    lines: list[str] = []
    for row in rows:
        match = HEADER.fullmatch(row)
        if match:
            if title is not None:
                yield title, "".join(lines)
            title, lines = match[1].strip(), []
        if title is not None:
            lines.append(row)
    # No next header means the final article may continue in another shard.


def prepare(rows: Iterable[str], output: Path, *, target_bytes: int) -> dict[str, object]:
    if target_bytes < 1:
        raise ValueError("target bytes must be positive")
    output.mkdir(parents=True, exist_ok=False)
    documents = output / "documents"
    documents.mkdir()
    total, count, duplicates = 0, 0, 0
    seen: set[str] = set()
    with (output / "materials.jsonl").open("x") as index:
        for title, text in articles(rows):
            raw = text.encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                duplicates += 1
                continue
            if len(raw) > 50 * 1024**2:
                raise ValueError("Article exceeds the server's upload limit")
            seen.add(digest)
            filename = f"documents/{count:05d}-{digest[:16]}.txt"
            (output / filename).write_bytes(raw)
            index.write(
                json.dumps(
                    {
                        "title": title,
                        "path": filename,
                        "bytes": len(raw),
                        "sha256": digest,
                        "source": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
                        "dataset_source": SOURCE,
                        "license": "CC-BY-SA-3.0 / GFDL (upstream dataset card)",
                    },
                    ensure_ascii=False,
                ) + "\n"
            )
            total += len(raw)
            count += 1
            if total >= target_bytes:
                break
    if total < target_bytes:
        raise ValueError(f"Insufficient complete source text: {total} < {target_bytes} bytes")
    manifest: dict[str, object] = {
        "dataset": "Salesforce/wikitext, wikitext-103-raw-v1, train",
        "revision": REVISION,
        "source_url": SOURCE,
        "dataset_card": CARD,
        "target_text_bytes": target_bytes,
        "source_text_bytes": total,
        "articles": count,
        "duplicate_articles_omitted": duplicates,
        "materials_sha256": file_hash(output / "materials.jsonl"),
        "selection": "First complete unique articles in source order through target UTF-8 bytes",
        "transformations": "Exact rows concatenated; incomplete final shard article omitted",
        "contains_questions_or_answers": False,
        "ingested": False,
    }
    (output / "NOTICE.txt").write_text(
        "WikiText by Stephen Merity, Caiming Xiong, James Bradbury and Richard Socher, "
        "Salesforce Research; source articles by Wikipedia contributors.\n"
        "Dataset card: " + CARD + "\n"
        "Licenses: https://creativecommons.org/licenses/by-sa/3.0/ and "
        "https://www.gnu.org/licenses/fdl-1.3.html\n"
        "Article attribution links and byte hashes are in materials.jsonl. "
        "Links identify article titles, not pinned historical Wikipedia revisions.\n"
        "The upstream dataset already tokenizes punctuation; this preparation preserves "
        "its text exactly. No question or answer annotations are included. "
        "Public pretraining overlap is unknown; this is a retrieval/load corpus, "
        "not an independent answer-quality benchmark.\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-bytes", type=int, default=200_000_000)
    args = parser.parse_args()
    digest = file_hash(args.parquet)
    if digest != SHARD_SHA256:
        raise ValueError("Parquet bytes differ from the pinned publisher SHA-256")
    # Offline preparation only. Reuse the already installed isolated Parquet reader.
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(args.parquet)

    def rows() -> Iterator[str]:
        for batch in parquet.iter_batches(batch_size=1024, columns=["text"]):
            for row in batch.column("text").to_pylist():
                if not isinstance(row, str):
                    raise ValueError("Unexpected non-text source row")
                yield row

    manifest = prepare(rows(), args.output, target_bytes=args.target_bytes)
    manifest["download_sha256"] = digest
    manifest["download_bytes"] = args.parquet.stat().st_size
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
