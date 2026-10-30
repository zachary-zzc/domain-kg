import json

import pytest

from scripts.prepare_wikitext import articles, file_hash, prepare


def test_whole_articles_keep_exact_text_subheadings_and_drop_incomplete_tail(tmp_path):
    first = " = Alpha = \nAlpha text.\n == History == \nMore text.\n"
    second = " = 中文 = \n第二篇。\n"
    rows = (first + second + " = Incomplete = \nTail\n").splitlines(keepends=True)
    assert list(articles(rows)) == [("Alpha", first), ("中文", second)]
    out = tmp_path / "corpus"
    manifest = prepare(rows, out, target_bytes=len(first.encode()) + 1)
    assert manifest["source_text_bytes"] == len((first + second).encode())
    index = [json.loads(line) for line in (out / "materials.jsonl").read_text().splitlines()]
    assert len(index) == 2
    assert [file_hash(out / item["path"]) for item in index] == [item["sha256"] for item in index]
    assert (out / index[1]["path"]).read_text() == second
    with pytest.raises(FileExistsError):
        prepare(rows, out, target_bytes=1)


def test_repeated_articles_do_not_pad_size_and_insufficient_corpus_fails(tmp_path):
    article = " = Alpha = \nOnly source.\n"
    rows = (article * 3).splitlines(keepends=True)
    with pytest.raises(ValueError, match="Insufficient complete source text"):
        prepare(rows, tmp_path / "corpus", target_bytes=len(article.encode()) + 1)
