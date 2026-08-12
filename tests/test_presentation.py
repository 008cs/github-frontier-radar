from __future__ import annotations

from radar.presentation import concise_brief_text


def test_concise_brief_text_keeps_only_first_two_sentences() -> None:
    value = "第一句解释工具做什么。第二句解释实际用途。第三句不应该进入周报。"

    assert concise_brief_text(value) == "第一句解释工具做什么。第二句解释实际用途。"


def test_concise_brief_text_caps_unpunctuated_provider_output() -> None:
    value = "很长的说明" * 40

    output = concise_brief_text(value)

    assert len(output) == 100
    assert output.endswith("…")
