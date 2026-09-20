"""Reasoning redaction tests (master plan P3.3): cross-fragment, bounded hold."""

from morrow.runtime.reasoning_filter import HIDDEN_MARK, ReasoningRedactor


def test_safe_prose_and_code_pass_through():
    redactor = ReasoningRedactor()
    out = redactor.feed("先读取 src/morrow/server/replies.py，再检查 fetch_items() 的分页逻辑。")
    out += redactor.feed("normalize(path) => ok")
    out += redactor.finish()
    assert "fetch_items" in out
    assert "normalize(path)" in out


def test_credential_tokens_are_masked():
    redactor = ReasoningRedactor()
    out = redactor.feed("密钥是 sk-abcdefghijklmnopqrstuvwx 后继续正常推理")
    out += redactor.feed("补充说明。")
    out += redactor.finish()
    assert "sk-abcdefghijklmnopqrstuvwx" not in out
    assert HIDDEN_MARK in out
    assert "后继续正常推理" in out


def test_labeled_assignments_are_masked():
    redactor = ReasoningRedactor()
    out = redactor.feed("把 API_KEY = topsecret-value 写入配置后运行测试")
    out += redactor.feed("完成。")
    out += redactor.finish()
    assert "topsecret-value" not in out
    assert HIDDEN_MARK in out


def test_secret_split_across_fragments_never_leaks():
    redactor = ReasoningRedactor()
    parts = ["token", "_key", ": ", "sk-abc", "defghijklmnop", "qrstuvwxyz12", " 之后总结"]
    out = "".join(redactor.feed(part) for part in parts)
    out += redactor.finish()
    assert "sk-abcdefghijklmnopqrstuvwxyz12" not in out
    assert "sk-abc" not in out.replace(HIDDEN_MARK, "")


def test_unresolved_label_without_value_is_released_at_finish():
    """Stream end with no value: the label itself was never a secret."""
    redactor = ReasoningRedactor()
    redactor.feed("password: ")
    assert redactor.finish() == "password: "


def test_prose_flows_immediately_without_waiting_for_more_fragments():
    redactor = ReasoningRedactor()
    assert "最后一句正常结论。" in redactor.feed("最后一句正常结论。")


def test_partial_token_tail_is_dropped_when_stream_ends_mid_value():
    redactor = ReasoningRedactor()
    redactor.feed("密钥 sk-abcdefghijklmnopqrst")
    tail = redactor.finish()
    # The 20+ char token completed but its end sat at the input edge; the
    # conservative release masks the matched shape instead of trusting it.
    assert "sk-abcdefghijklmnopqrst" not in tail or HIDDEN_MARK in tail


def test_multibyte_and_control_sequences_do_not_break_the_filter():
    redactor = ReasoningRedactor()
    out = redactor.feed("中文段落\n第二行\x1b[31m红色\x1b[0m 结束")
    assert "中文段落" in out
    assert redactor.feed("后续") is not None
