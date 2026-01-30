from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from elle.rag.llm import (
    LLM,
    ContextWindowManager,
    LLMConfig,
    LLMError,
    LLMJSONError,
    LLMResponse,
    LLMSession,
    LLMTimeoutError,
    LLMUnavailableError,
    Message,
    ToolCall,
    ToolCallResponse,
    get_llm,
    reset_llm,
)

# ---------------------------------------------------------------------------
# LLMConfig
# ---------------------------------------------------------------------------

class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.host == "http://localhost:11434"
        assert cfg.model == "qwen2.5:7b-instruct-q8_0"
        assert cfg.timeout > 0
        assert cfg.max_tokens > 0
        assert 0.0 <= cfg.temperature <= 2.0

    def test_custom(self):
        cfg = LLMConfig(host="http://example:1234", model="foo", timeout=5.0)
        assert cfg.host == "http://example:1234"
        assert cfg.model == "foo"
        assert cfg.timeout == 5.0


# ---------------------------------------------------------------------------
# LLMResponse
# ---------------------------------------------------------------------------

class TestLLMResponse:
    def test_total_tokens_both(self):
        r = LLMResponse(content="hi", model="m", prompt_tokens=10, completion_tokens=5)
        assert r.total_tokens == 15

    def test_total_tokens_none(self):
        r = LLMResponse(content="hi", model="m")
        assert r.total_tokens is None

    def test_total_tokens_partial(self):
        r = LLMResponse(content="hi", model="m", prompt_tokens=10)
        assert r.total_tokens is None


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class TestMessage:
    def test_create(self):
        m = Message(role="system", content="hello")
        assert m.role == "system"
        assert m.content == "hello"


# ---------------------------------------------------------------------------
# ContextWindowManager
# ---------------------------------------------------------------------------

class TestContextWindowManager:
    def test_add_and_get(self):
        mgr = ContextWindowManager(max_tokens=1000)
        mgr.add_message("system", "sys")
        mgr.add_message("user", "hi")
        msgs = mgr.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "hi"

    def test_add_messages(self):
        mgr = ContextWindowManager(max_tokens=1000)
        mgr.add_messages([
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ])
        assert mgr.message_count == 2

    def test_token_count(self):
        mgr = ContextWindowManager(max_tokens=1000)
        mgr.add_message("user", "a" * 40)
        assert mgr.token_count == 10  # 40 chars / 4 chars per token

    def test_utilization(self):
        mgr = ContextWindowManager(max_tokens=100)
        mgr.add_message("user", "a" * 40)
        assert mgr.utilization == pytest.approx(0.1)

    def test_utilization_zero_max(self):
        mgr = ContextWindowManager(max_tokens=0)
        assert mgr.utilization == 0.0

    def test_needs_compaction(self):
        mgr = ContextWindowManager(max_tokens=100, compact_threshold=0.5)
        mgr.add_message("user", "a" * 250)
        assert mgr.needs_compaction()

    def test_no_compaction_needed(self):
        mgr = ContextWindowManager(max_tokens=100000, compact_threshold=0.8)
        mgr.add_message("user", "hello")
        assert not mgr.needs_compaction()

    def test_clear(self):
        mgr = ContextWindowManager(max_tokens=1000)
        mgr.add_message("user", "hi")
        mgr.clear()
        assert mgr.message_count == 0
        assert mgr.token_count == 0

    def test_compaction_count(self):
        mgr = ContextWindowManager(max_tokens=1000)
        assert mgr.compaction_count == 0

    @pytest.mark.asyncio
    async def test_compact_too_few_messages(self):
        mgr = ContextWindowManager(max_tokens=1000, min_messages=10)
        mgr.add_message("user", "short")
        llm_mock = MagicMock()
        result = await mgr.compact(llm_mock)
        assert result == ""

    @pytest.mark.asyncio
    async def test_compact_generates_summary(self):
        mgr = ContextWindowManager(
            max_tokens=100,
            compact_threshold=0.5,
            compact_target=0.3,
            min_messages=2,
        )
        mgr.add_message("system", "You are a helper.")
        for i in range(10):
            mgr.add_message("user", f"message {i} " * 10)
            mgr.add_message("assistant", f"reply {i} " * 10)

        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            content="Summary of conversation.", model="test"
        )

        result = await mgr.compact(mock_llm)
        assert result == "Summary of conversation."
        assert mgr.compaction_count == 1
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_compact_fallback_on_error(self):
        mgr = ContextWindowManager(
            max_tokens=100,
            compact_threshold=0.5,
            compact_target=0.3,
            min_messages=2,
        )
        mgr.add_message("system", "System prompt.")
        for i in range(10):
            mgr.add_message("user", f"message {i} content")
            mgr.add_message("assistant", f"reply {i} content")

        mock_llm = MagicMock()
        mock_llm.generate.side_effect = RuntimeError("LLM down")

        result = await mgr.compact(mock_llm)
        assert "Earlier conversation covered:" in result
        assert mgr.compaction_count == 1

    @pytest.mark.asyncio
    async def test_compact_no_messages_to_summarize(self):
        mgr = ContextWindowManager(
            max_tokens=10000,
            compact_threshold=0.01,
            compact_target=0.005,
            min_messages=100,
        )
        mgr.add_message("system", "sys")
        mgr.add_message("user", "a")
        mgr.add_message("assistant", "b")
        mock_llm = MagicMock()
        result = await mgr.compact(mock_llm)
        assert result == ""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_llm_error(self):
        e = LLMError("bad", status_code=500)
        assert e.message == "bad"
        assert e.status_code == 500

    def test_llm_unavailable(self):
        e = LLMUnavailableError("nope")
        assert isinstance(e, LLMError)

    def test_llm_timeout(self):
        e = LLMTimeoutError("slow")
        assert isinstance(e, LLMError)

    def test_llm_json_error(self):
        e = LLMJSONError("parse fail", raw_content="{bad}")
        assert e.raw_content == "{bad}"


# ---------------------------------------------------------------------------
# LLM (availability + model detection)
# ---------------------------------------------------------------------------

class TestLLMAvailability:
    def test_is_available_cached(self):
        llm = LLM()
        llm._available = True
        assert llm.is_available() is True

    def test_is_available_force_check(self):
        llm = LLM()
        llm._available = True
        with patch.object(llm._provider._client, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp
            assert llm.is_available(force_check=True) is True

    def test_is_available_request_error(self):
        llm = LLM()
        with patch.object(llm._provider._client, "get", side_effect=httpx.ConnectError("no")):
            assert llm.is_available(force_check=True) is False

    def test_list_models(self):
        llm = LLM()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "a"}, {"name": "b"}]}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "get", return_value=mock_resp):
            assert llm.list_models() == ["a", "b"]

    def test_list_models_error(self):
        llm = LLM()
        with patch.object(llm._provider._client, "get", side_effect=httpx.ConnectError("x")):
            # Provider catches connection error and returns empty list
            assert llm.list_models() == []

    def test_model_auto_detect_configured(self):
        cfg = LLMConfig(model="mymodel")
        llm = LLM(cfg)
        llm._available = True
        with patch.object(llm, "list_models", return_value=["mymodel"]):
            assert llm.model == "mymodel"

    def test_model_auto_detect_fallback(self):
        cfg = LLMConfig(model="not-available")
        llm = LLM(cfg)
        llm._available = True
        with patch.object(llm, "list_models", return_value=["qwen2.5:7b-instruct"]):
            m = llm.model
            assert m == "qwen2.5:7b-instruct"

    def test_model_no_matches(self):
        cfg = LLMConfig(model="mymodel")
        llm = LLM(cfg)
        llm._available = True
        with patch.object(llm, "list_models", return_value=["unrelated"]):
            assert llm.model == "mymodel"

    def test_model_unavailable(self):
        cfg = LLMConfig(model="mymodel")
        llm = LLM(cfg)
        llm._available = False
        assert llm.model == "mymodel"

    def test_has_model_true(self):
        llm = LLM()
        llm._available = True
        with patch.object(llm, "list_models", return_value=["qwen2.5:7b"]):
            assert llm.has_model("qwen2.5:7b") is True

    def test_has_model_unavailable(self):
        llm = LLM()
        llm._available = False
        assert llm.has_model("qwen2.5:7b") is False

    def test_context_manager(self):
        with LLM() as llm:
            assert llm is not None
        # _client should be closed, but llm still exists


# ---------------------------------------------------------------------------
# LLM generate
# ---------------------------------------------------------------------------

class TestLLMGenerate:
    def _make_llm(self):
        llm = LLM()
        llm._detected_model = "testmodel"
        return llm

    def test_generate_success(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "response": "hello world",
            "model": "testmodel",
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 10,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            resp = llm.generate("test prompt")
            assert resp.content == "hello world"
            assert resp.model == "testmodel"
            assert resp.done is True
            assert resp.prompt_tokens == 5
            assert resp.completion_tokens == 10

    def test_generate_with_system(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok", "model": "m", "done": True}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp) as mock_post:
            llm.generate("prompt", system="You are helpful.")
            payload = mock_post.call_args[1]["json"]
            assert payload["system"] == "You are helpful."

    def test_generate_connect_error(self):
        llm = self._make_llm()
        with patch.object(llm._provider._client, "post", side_effect=httpx.ConnectError("down")):
            with pytest.raises(LLMUnavailableError):
                llm.generate("test")
            assert llm._available is False

    def test_generate_timeout(self):
        llm = self._make_llm()
        with patch.object(llm._provider._client, "post", side_effect=httpx.ReadTimeout("slow")):
            with pytest.raises(LLMTimeoutError):
                llm.generate("test")

    def test_generate_http_error(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=mock_resp
        )
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            with pytest.raises(LLMError) as exc_info:
                llm.generate("test")
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# LLM generate_json
# ---------------------------------------------------------------------------

class TestLLMGenerateJSON:
    def _make_llm(self):
        llm = LLM()
        llm._detected_model = "testmodel"
        return llm

    def test_generate_json_success(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "response": '{"key": "value"}',
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            result = llm.generate_json("give json")
            assert result == {"key": "value"}

    def test_generate_json_with_schema(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "response": '{"items": []}',
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp) as mock_post:
            result = llm.generate_json("list items", schema={"items": ["str"]})
            assert result == {"items": []}
            payload = mock_post.call_args[1]["json"]
            assert "format" in payload
            assert payload["format"] == "json"

    def test_generate_json_retry_on_parse_error(self):
        llm = self._make_llm()

        call_count = [0]
        def mock_post(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count[0] == 1:
                resp.json.return_value = {"response": "not json{", "model": "m", "done": True}
            else:
                resp.json.return_value = {"response": '{"fixed": true}', "model": "m", "done": True}
            return resp

        with patch.object(llm._provider._client, "post", side_effect=mock_post):
            result = llm.generate_json("prompt")
            assert result == {"fixed": True}
            assert call_count[0] == 2

    def test_generate_json_no_retry(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "not json at all", "model": "m", "done": True}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            with pytest.raises(LLMJSONError):
                llm.generate_json("prompt", retry_on_error=False)

    def test_generate_json_retry_also_fails(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "bad data", "model": "m", "done": True}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            with pytest.raises(LLMJSONError):
                llm.generate_json("prompt")

    def test_parse_json_markdown_code_blocks(self):
        llm = self._make_llm()
        result = llm._parse_json('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_parse_json_plain_code_block(self):
        llm = self._make_llm()
        result = llm._parse_json('```\n{"a": 1}\n```')
        assert result == {"a": 1}


# ---------------------------------------------------------------------------
# LLM chat
# ---------------------------------------------------------------------------

class TestLLMChat:
    def _make_llm(self):
        llm = LLM()
        llm._detected_model = "testmodel"
        return llm

    def test_chat_dict_messages(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": "reply"},
            "model": "testmodel",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            result = llm.chat([
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello"},
            ])
            assert result.content == "reply"

    def test_chat_message_objects(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": "ok"},
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            result = llm.chat([
                Message(role="user", content="hi"),
            ])
            assert result.content == "ok"

    def test_chat_connect_error(self):
        llm = self._make_llm()
        with patch.object(llm._provider._client, "post", side_effect=httpx.ConnectError("x")):
            with pytest.raises(LLMUnavailableError):
                llm.chat([{"role": "user", "content": "hi"}])

    def test_chat_timeout(self):
        llm = self._make_llm()
        with patch.object(llm._provider._client, "post", side_effect=httpx.ReadTimeout("slow")):
            with pytest.raises(LLMTimeoutError):
                llm.chat([{"role": "user", "content": "hi"}])

    def test_chat_http_error(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=mock_resp
        )
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            with pytest.raises(LLMError):
                llm.chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# LLM chat_json
# ---------------------------------------------------------------------------

class TestLLMChatJSON:
    def _make_llm(self):
        llm = LLM()
        llm._detected_model = "testmodel"
        return llm

    def test_chat_json_success(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": '{"status": "ok"}'},
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            result = llm.chat_json([
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello"},
            ])
            assert result == {"status": "ok"}

    def test_chat_json_no_system(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": '{"a": 1}'},
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            result = llm.chat_json([
                {"role": "user", "content": "hello"},
            ])
            assert result == {"a": 1}

    def test_chat_json_with_schema(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": '{"items": []}'},
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            result = llm.chat_json(
                [Message(role="user", content="give items")],
                schema={"items": ["str"]},
            )
            assert result == {"items": []}

    def test_chat_json_retry_on_failure(self):
        llm = self._make_llm()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count[0] == 1:
                resp.json.return_value = {"message": {"content": "not json"}, "model": "m", "done": True}
            else:
                resp.json.return_value = {"message": {"content": '{"ok": true}'}, "model": "m", "done": True}
            return resp

        with patch.object(llm._provider._client, "post", side_effect=side_effect):
            result = llm.chat_json([{"role": "user", "content": "hi"}])
            assert result == {"ok": True}

    def test_chat_json_no_retry(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "bad"}, "model": "m", "done": True}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            with pytest.raises(LLMJSONError):
                llm.chat_json([{"role": "user", "content": "hi"}], retry_on_error=False)

    def test_chat_json_retry_also_fails(self):
        llm = self._make_llm()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "never json"}, "model": "m", "done": True}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(llm._provider._client, "post", return_value=mock_resp):
            with pytest.raises(LLMJSONError):
                llm.chat_json([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# ToolCall / ToolCallResponse
# ---------------------------------------------------------------------------

class TestToolModels:
    def test_tool_call(self):
        tc = ToolCall(name="search", arguments={"q": "test"})
        assert tc.name == "search"
        assert tc.id == ""

    def test_tool_call_response_has_calls(self):
        tcr = ToolCallResponse(
            tool_calls=(ToolCall(name="a", arguments={}),),
        )
        assert tcr.has_tool_calls is True

    def test_tool_call_response_no_calls(self):
        tcr = ToolCallResponse()
        assert tcr.has_tool_calls is False


# ---------------------------------------------------------------------------
# LLMSession
# ---------------------------------------------------------------------------

class TestLLMSession:
    def _make_session(self, system=None, tools=None):
        llm = LLM()
        llm._detected_model = "testmodel"
        return LLMSession(llm, system_prompt=system, tools=tools)

    def test_init_with_system(self):
        s = self._make_session(system="Be helpful.")
        assert s.message_count == 1
        assert s.messages[0]["role"] == "system"

    def test_init_no_system(self):
        s = self._make_session()
        assert s.message_count == 0

    def test_add_user_message(self):
        s = self._make_session()
        s.add_user_message("Hello")
        assert s.message_count == 1

    def test_add_assistant_message(self):
        s = self._make_session()
        s.add_assistant_message("Hi", tool_calls=[ToolCall(name="f", arguments={})])
        msg = s.messages[0]
        assert msg["role"] == "assistant"
        assert "tool_calls" in msg

    def test_add_tool_result(self):
        s = self._make_session()
        s.add_tool_result("call_0", "result text")
        msg = s.messages[0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_0"

    def test_close(self):
        s = self._make_session()
        s.close()
        assert s._closed is True

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        llm = LLM()
        llm._detected_model = "testmodel"
        async with LLMSession(llm) as s:
            assert not s._closed
        assert s._closed

    @pytest.mark.asyncio
    async def test_chat_closed_raises(self):
        s = self._make_session()
        s.close()
        with pytest.raises(RuntimeError, match="closed"):
            await s.chat("hi")

    @pytest.mark.asyncio
    async def test_sync_chat(self):
        s = self._make_session()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": "response text"},
            "model": "testmodel",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(s.llm._provider._client, "post", return_value=mock_resp):
            result = await s.chat("hello")
            assert result.content == "response text"
            assert s.message_count == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_sync_chat_with_tool_calls(self):
        s = self._make_session(tools=[{"type": "function", "function": {"name": "search"}}])
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "search",
                            "arguments": '{"q": "test"}',
                        },
                    }
                ],
            },
            "model": "testmodel",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(s.llm._provider._client, "post", return_value=mock_resp):
            result = await s.chat("find something")
            assert result.has_tool_calls
            assert result.tool_calls[0].name == "search"

    @pytest.mark.asyncio
    async def test_sync_chat_tool_calls_bad_args(self):
        s = self._make_session()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "bad",
                            "arguments": "not json{",
                        },
                    }
                ],
            },
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(s.llm._provider._client, "post", return_value=mock_resp):
            result = await s.chat("test")
            assert result.tool_calls[0].arguments == {}

    @pytest.mark.asyncio
    async def test_sync_chat_tool_calls_dict_args(self):
        s = self._make_session()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "fn",
                            "arguments": {"x": 1},
                        },
                    }
                ],
            },
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(s.llm._provider._client, "post", return_value=mock_resp):
            result = await s.chat("test")
            assert result.tool_calls[0].arguments == {"x": 1}

    @pytest.mark.asyncio
    async def test_chat_connect_error(self):
        s = self._make_session()
        with patch.object(s.llm._provider._client, "post", side_effect=httpx.ConnectError("x")):
            with pytest.raises(LLMUnavailableError):
                await s.chat("test")

    @pytest.mark.asyncio
    async def test_chat_timeout_error(self):
        s = self._make_session()
        with patch.object(s.llm._provider._client, "post", side_effect=httpx.ReadTimeout("slow")):
            with pytest.raises(LLMTimeoutError):
                await s.chat("test")

    @pytest.mark.asyncio
    async def test_chat_http_error(self):
        s = self._make_session()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=mock_resp
        )
        with patch.object(s.llm._provider._client, "post", return_value=mock_resp):
            with pytest.raises(LLMError):
                await s.chat("test")

    @pytest.mark.asyncio
    async def test_continue_after_tool(self):
        s = self._make_session()
        s.add_user_message("q")
        s.add_assistant_message("", tool_calls=[ToolCall(name="f", arguments={})])
        s.add_tool_result("call_0", "result")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"content": "follow up"},
            "model": "m",
            "done": True,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(s.llm._provider._client, "post", return_value=mock_resp):
            result = await s.continue_after_tool()
            assert result.content == "follow up"

    @pytest.mark.asyncio
    async def test_stream_chat(self):
        s = self._make_session()
        chunks_json = [
            json.dumps({"message": {"content": "hel"}, "done": False}),
            json.dumps({"message": {"content": "lo"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True, "model": "testmodel", "prompt_eval_count": 5, "eval_count": 2}),
        ]

        collected = []
        def callback(token):
            collected.append(token)

        mock_stream_resp = MagicMock()
        mock_stream_resp.__enter__ = MagicMock(return_value=mock_stream_resp)
        mock_stream_resp.__exit__ = MagicMock(return_value=False)
        mock_stream_resp.raise_for_status = MagicMock()
        mock_stream_resp.iter_lines.return_value = iter(chunks_json)

        with patch.object(s.llm._provider._client, "stream", return_value=mock_stream_resp):
            result = await s.chat("hello", stream_callback=callback)
            assert result.content == "hello"
            assert result.done is True
            assert collected == ["hel", "lo"]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestModuleSingleton:
    def test_get_llm_singleton(self):
        import elle.rag.llm as llm_mod
        old = llm_mod._llm
        llm_mod._llm = None
        try:
            a = get_llm()
            b = get_llm()
            assert a is b
        finally:
            if llm_mod._llm:
                llm_mod._llm.close()
            llm_mod._llm = old

    def test_reset_llm(self):
        import elle.rag.llm as llm_mod
        old = llm_mod._llm
        llm_mod._llm = None
        try:
            _ = get_llm()
            assert llm_mod._llm is not None
            reset_llm()
            assert llm_mod._llm is None
        finally:
            llm_mod._llm = old

    def test_reset_llm_when_none(self):
        import elle.rag.llm as llm_mod
        old = llm_mod._llm
        llm_mod._llm = None
        try:
            reset_llm()  # should not raise
            assert llm_mod._llm is None
        finally:
            llm_mod._llm = old
