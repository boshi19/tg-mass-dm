"""test_messages.py - 文案池和随机尾部模块的 pytest 测试"""
import pytest
from messages import (
    append_random_tail,
    compose_message,
    detect_risky_terms,
    load_messages,
    render_template,
)


class TestLoadMessages:
    """测试文案池加载"""

    def test_load_messages(self, tmp_messages_file):
        msgs = load_messages(tmp_messages_file)
        assert len(msgs) == 3
        assert msgs[0] == "你好，这是第一条消息"
        assert msgs[1] == "第二条消息模板"
        assert msgs[2] == "第三条消息"

    def test_skips_comments_and_blank(self, tmp_messages_file):
        msgs = load_messages(tmp_messages_file)
        assert any(line.startswith("#") for line in msgs) is False
        assert "" not in msgs

    def test_empty_file_raises(self, tmp_empty_file):
        with pytest.raises(ValueError, match="文案池为空"):
            load_messages(tmp_empty_file)

    def test_comments_only_file_raises(self, tmp_comments_only_file):
        with pytest.raises(ValueError, match="文案池为空"):
            load_messages(tmp_comments_only_file)

    def test_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_messages("/nonexistent/messages.txt")


class TestAppendRandomTail:
    """测试随机尾部追加"""

    def test_dots_style_appended(self):
        result = append_random_tail("Hello", style="dots")
        assert result != "Hello", "结果应与原文不同"

    def test_dots_style_multiple_calls_different(self):
        """多次调用 dots 风格可能产生不同结果"""
        results = {append_random_tail("Test", style="dots") for _ in range(20)}
        # 至少应该有多种不同结果（概率极高）
        assert len(results) > 1

    def test_ref_style_format(self):
        result = append_random_tail("Message", style="ref")
        assert "Message" in result
        assert "[ref:" in result
        assert result.endswith("]")

    def test_ref_style_unique(self):
        results = {append_random_tail("Msg", style="ref") for _ in range(10)}
        assert len(results) == 10  # ref ID 应该全部不同

    def test_empty_text_dots(self):
        result = append_random_tail("", style="dots")
        assert len(result) > 0  # 至少有 dots

    def test_default_style_is_dots(self):
        result = append_random_tail("Hello")
        assert "." in result or result == "Hello"
        assert result != "Hello"


class TestTemplateAndRisk:
    def test_render_template_known_placeholders(self):
        result = render_template("hi {username} from {account}", {"username": "alice", "account": "me"})
        assert result == "hi alice from me"

    def test_render_template_keeps_unknown_placeholders(self):
        result = render_template("hi {unknown}", {"username": "alice"})
        assert result == "hi {unknown}"

    def test_compose_message_can_disable_variation_and_tail(self):
        result = compose_message("hi {username}", {"username": "alice"}, tail_enabled=False, variation_enabled=False)
        assert result == "hi alice"

    def test_detect_risky_terms(self):
        result = detect_risky_terms("点击链接 https://example.com 加微信")
        assert "点击链接" in result
        assert "https://" in result
        assert "加微信" in result
