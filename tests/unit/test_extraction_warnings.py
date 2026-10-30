def test_rejection_counts_add_across_chunks_and_other_warnings_remain_distinct():
    from domain_kg.ingestion.warnings import merge_warnings

    assert merge_warnings(
        [
            "OCR_REQUIRED",
            "extraction_rejected_entities:2",
            "extraction_rejected_assertions:3",
            "extraction_rejected_assertions:4",
            "OCR_REQUIRED",
        ]
    ) == (
        "OCR_REQUIRED",
        "extraction_rejected_entities:2",
        "extraction_rejected_assertions:7",
    )


def test_console_shows_chinese_rejection_counts_without_translating_source_text():
    from domain_kg.console.i18n import LANGUAGE, label

    token = LANGUAGE.set("zh-CN")
    try:
        message = label("extraction_rejected_assertions:7")
        assert "7" in message and "关系" in message and "原文完整保留" in message
        assert label("extraction_rejected_entities:2").startswith("已跳过 2")
        assert "OCR" in label("OCR_REQUIRED")
    finally:
        LANGUAGE.reset(token)
