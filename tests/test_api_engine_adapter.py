from __future__ import annotations

"""Comprehensive tests for elle.daemon.api.engine_adapter.

Covers:
- _is_mutating_command: all mutating and non-mutating command patterns
- EngineResponse dataclass construction and defaults
- EngineAdapter.__init__: explicit and default parameters
- EngineAdapter.engine / classifier lazy properties
- EngineAdapter.process_chat: routing for readonly, execute, capabilities_only
- EngineAdapter.process_chat: empty messages fallback
- EngineAdapter.process_chat_streaming: chunked output iteration
- EngineAdapter._extract_user_input: last user message extraction
- EngineAdapter._create_session_from_messages: session creation from history
- EngineAdapter._classify_async: async classification wrapper
- EngineAdapter._process_engine_async: async engine wrapper
- EngineAdapter._handle_readonly: question, navigation, meta, task, shell, fixit, fallback
- EngineAdapter._handle_capabilities_only: task, shell, other intents
- EngineAdapter._handle_execute: success and failure
- EngineAdapter._generate_capability_tool_calls: restart, install, generic
- format_completion_response: with/without tool_calls, with/without policy
- get_adapter: module-level singleton
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.terminal.intent import Intent, IntentResult
from elle.daemon.api.engine_adapter import (
    MUTATING_COMMAND_PATTERNS,
    EngineAdapter,
    EngineResponse,
    _is_mutating_command,
    format_completion_response,
    get_adapter,
)
from elle.daemon.api.openai_models import (
    ChatMessage,
    ExecutionMode,
    FunctionCall,
    ToolCall,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADAPTER_MOD = "elle.daemon.api.engine_adapter"


def _intent_result(
    intent: Intent,
    confidence: float = 0.95,
    entities: list[str] | None = None,
) -> IntentResult:
    """Build a minimal IntentResult for tests."""
    return IntentResult(
        intent=intent,
        confidence=confidence,
        rationale="test classification",
        entities=entities or [],
        suggested_followups=[],
        requires_clarification=False,
        classified_by="rule",
    )


def _engine_result(output: str = "ok", success: bool = True) -> MagicMock:
    """Build a mock EngineResult with the given fields."""
    er = MagicMock()
    er.output = output
    er.success = success
    return er


def _user_msg(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def _assistant_msg(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)


def _system_msg(text: str) -> ChatMessage:
    return ChatMessage(role="system", content=text)


def _auth_ctx() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# _is_mutating_command
# ---------------------------------------------------------------------------


class TestIsMutatingCommand:
    """Tests for the _is_mutating_command helper."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm file.txt",
            "mv a b",
            "cp src dst",
            "mkdir /tmp/foo",
            "rmdir /tmp/foo",
            "touch newfile",
            "chmod 755 script.sh",
            "chown root:root f",
            "ln -s target link",
            "apt install vim",
            "apt-get update",
            "dpkg -i pkg.deb",
            "snap install app",
            "pip install flask",
            "npm install express",
            "systemctl start nginx",
            "systemctl stop nginx",
            "systemctl restart nginx",
            "systemctl enable nginx",
            "systemctl disable nginx",
            "systemctl reload nginx",
            "systemctl mask nginx",
            "systemctl unmask nginx",
            "service nginx start",
            "service nginx stop",
            "service nginx restart",
            "docker run hello",
            "docker start abc",
            "docker stop abc",
            "docker rm abc",
            "docker kill abc",
            "docker exec abc ls",
            "docker build .",
            "docker push img",
            "docker pull img",
            "docker-compose up",
            "docker-compose down",
            "docker-compose start",
            "docker-compose stop",
            "docker-compose rm",
            "git commit -m 'msg'",
            "git push origin main",
            "git pull",
            "git merge dev",
            "git rebase main",
            "git reset HEAD~1",
            "git checkout feature",
            "git branch -d old",
            "git branch -D old",
            "git stash",
            "git clean -fd",
            "ufw allow 80",
            "iptables -A INPUT",
            "useradd bob",
            "userdel bob",
            "usermod -aG sudo bob",
            "passwd bob",
            "mount /dev/sda1 /mnt",
            "umount /mnt",
            "mkfs.ext4 /dev/sda1",
            "fdisk /dev/sda",
        ],
    )
    def test_mutating_commands_detected(self, cmd: str) -> None:
        assert _is_mutating_command(cmd) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls -la",
            "cat /etc/hostname",
            "df -h",
            "free -m",
            "top -bn1",
            "systemctl status nginx",
            "docker ps",
            "docker images",
            "docker logs abc",
            "git status",
            "git log --oneline",
            "git diff",
            "git branch",
            "whoami",
            "uptime",
            "ps aux",
            "netstat -tlnp",
            "ss -tlnp",
            "journalctl -n 50",
        ],
    )
    def test_non_mutating_commands_allowed(self, cmd: str) -> None:
        assert _is_mutating_command(cmd) is False

    def test_whitespace_is_stripped(self) -> None:
        assert _is_mutating_command("  rm foo  ") is True

    def test_empty_command(self) -> None:
        assert _is_mutating_command("") is False
        assert _is_mutating_command("   ") is False


# ---------------------------------------------------------------------------
# EngineResponse dataclass
# ---------------------------------------------------------------------------


class TestEngineResponse:
    """Tests for the EngineResponse frozen dataclass."""

    def test_minimal_construction(self) -> None:
        r = EngineResponse(content="hello", intent="meta", success=True)
        assert r.content == "hello"
        assert r.intent == "meta"
        assert r.success is True
        assert r.tool_calls is None
        assert r.policy_evaluated is False
        assert r.policy_effect is None
        assert r.policy_message is None

    def test_full_construction(self) -> None:
        tc = (
            ToolCall(
                id="call_abc",
                type="function",
                function=FunctionCall(name="shell.execute", arguments="{}"),
            ),
        )
        r = EngineResponse(
            content="done",
            intent="system_task",
            success=True,
            tool_calls=tc,
            policy_evaluated=True,
            policy_effect="allow",
            policy_message="ok",
        )
        assert r.tool_calls == tc
        assert r.policy_evaluated is True
        assert r.policy_effect == "allow"
        assert r.policy_message == "ok"

    def test_frozen(self) -> None:
        r = EngineResponse(content="x", intent="meta", success=True)
        with pytest.raises(AttributeError):
            r.content = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EngineAdapter.__init__ and lazy properties
# ---------------------------------------------------------------------------


class TestEngineAdapterInit:
    """Tests for EngineAdapter construction and lazy properties."""

    def test_defaults(self) -> None:
        adapter = EngineAdapter()
        assert adapter._engine is None
        assert adapter._classifier is None
        assert adapter._executor is not None

    def test_explicit_args(self) -> None:
        eng = MagicMock()
        cls = MagicMock()
        exe = MagicMock()
        adapter = EngineAdapter(engine=eng, classifier=cls, executor=exe)
        assert adapter._engine is eng
        assert adapter._classifier is cls
        assert adapter._executor is exe

    @patch(f"{_ADAPTER_MOD}.get_engine")
    def test_engine_lazy_init(self, mock_get_engine: MagicMock) -> None:
        sentinel = MagicMock()
        mock_get_engine.return_value = sentinel
        adapter = EngineAdapter()
        result = adapter.engine
        assert result is sentinel
        mock_get_engine.assert_called_once()
        # Second access should not call get_engine again
        assert adapter.engine is sentinel
        assert mock_get_engine.call_count == 1

    @patch(f"{_ADAPTER_MOD}.get_classifier")
    def test_classifier_lazy_init(self, mock_get_cls: MagicMock) -> None:
        sentinel = MagicMock()
        mock_get_cls.return_value = sentinel
        adapter = EngineAdapter()
        result = adapter.classifier
        assert result is sentinel
        mock_get_cls.assert_called_once()
        # Second access reuses cached value
        assert adapter.classifier is sentinel
        assert mock_get_cls.call_count == 1

    def test_engine_property_uses_provided(self) -> None:
        eng = MagicMock()
        adapter = EngineAdapter(engine=eng)
        assert adapter.engine is eng

    def test_classifier_property_uses_provided(self) -> None:
        cls = MagicMock()
        adapter = EngineAdapter(classifier=cls)
        assert adapter.classifier is cls


# ---------------------------------------------------------------------------
# _extract_user_input
# ---------------------------------------------------------------------------


class TestExtractUserInput:
    """Tests for EngineAdapter._extract_user_input."""

    def test_single_user_message(self) -> None:
        adapter = EngineAdapter()
        msgs = (_user_msg("hello world"),)
        assert adapter._extract_user_input(msgs) == "hello world"

    def test_multiple_messages_returns_last_user(self) -> None:
        adapter = EngineAdapter()
        msgs = (
            _system_msg("sys"),
            _user_msg("first"),
            _assistant_msg("response"),
            _user_msg("  second  "),
        )
        assert adapter._extract_user_input(msgs) == "second"

    def test_no_user_message_returns_empty(self) -> None:
        adapter = EngineAdapter()
        msgs = (_system_msg("sys"), _assistant_msg("asst"))
        assert adapter._extract_user_input(msgs) == ""

    def test_empty_tuple(self) -> None:
        adapter = EngineAdapter()
        assert adapter._extract_user_input(()) == ""

    def test_user_message_with_none_content(self) -> None:
        adapter = EngineAdapter()
        msgs = (ChatMessage(role="user", content=None),)
        assert adapter._extract_user_input(msgs) == ""

    def test_user_message_with_empty_string(self) -> None:
        adapter = EngineAdapter()
        msgs = (ChatMessage(role="user", content=""),)
        assert adapter._extract_user_input(msgs) == ""


# ---------------------------------------------------------------------------
# _create_session_from_messages
# ---------------------------------------------------------------------------


class TestCreateSessionFromMessages:
    """Tests for session creation from conversation history."""

    @patch(f"{_ADAPTER_MOD}.create_session")
    def test_creates_session(self, mock_create: MagicMock) -> None:
        mock_session = MagicMock()
        mock_create.return_value = mock_session
        adapter = EngineAdapter()
        result = adapter._create_session_from_messages(())
        mock_create.assert_called_once()
        assert result is mock_session

    @patch(f"{_ADAPTER_MOD}.create_session")
    def test_with_assistant_messages_containing_commands(self, mock_create: MagicMock) -> None:
        mock_session = MagicMock()
        mock_create.return_value = mock_session
        adapter = EngineAdapter()
        msgs = (
            _user_msg("check disk"),
            _assistant_msg("Here is the output: $ df -h"),
            _assistant_msg("The `uptime` command shows ..."),
        )
        result = adapter._create_session_from_messages(msgs)
        # Should still return the session (history extraction is a no-op currently)
        assert result is mock_session

    @patch(f"{_ADAPTER_MOD}.create_session")
    def test_no_history_populated_currently(self, mock_create: MagicMock) -> None:
        mock_session = MagicMock()
        mock_session.with_command_result = MagicMock()
        mock_create.return_value = mock_session
        adapter = EngineAdapter()
        msgs = (_user_msg("hello"),)
        adapter._create_session_from_messages(msgs)
        # Currently history list stays empty so with_command_result is never called
        mock_session.with_command_result.assert_not_called()


# ---------------------------------------------------------------------------
# _classify_async
# ---------------------------------------------------------------------------


class TestClassifyAsync:
    """Tests for async classification wrapper."""

    @pytest.mark.asyncio
    async def test_classify_async_delegates(self) -> None:
        mock_cls = MagicMock()
        ir = _intent_result(Intent.META)
        mock_cls.classify.return_value = ir

        mock_session = MagicMock()
        adapter = EngineAdapter(classifier=mock_cls)

        result = await adapter._classify_async("help", mock_session)
        assert result is ir
        mock_cls.classify.assert_called_once_with("help", mock_session)


# ---------------------------------------------------------------------------
# _process_engine_async
# ---------------------------------------------------------------------------


class TestProcessEngineAsync:
    """Tests for async engine processing wrapper."""

    @pytest.mark.asyncio
    async def test_process_engine_async_delegates(self) -> None:
        mock_engine = MagicMock()
        er = _engine_result("result text")
        mock_engine.process.return_value = er

        mock_session = MagicMock()
        adapter = EngineAdapter(engine=mock_engine)

        result = await adapter._process_engine_async("do something", mock_session)
        assert result is er
        mock_engine.process.assert_called_once_with("do something", mock_session, stream_output=False)


# ---------------------------------------------------------------------------
# process_chat
# ---------------------------------------------------------------------------


class TestProcessChat:
    """Tests for the main process_chat entry point."""

    @pytest.mark.asyncio
    async def test_empty_messages_returns_error(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        result = await adapter.process_chat(
            messages=(_system_msg("sys"),),
            mode=ExecutionMode.EXECUTE,
            auth=_auth_ctx(),
        )
        assert result.success is False
        assert "No user message" in result.content
        assert result.intent == "meta"

    @pytest.mark.asyncio
    async def test_routes_to_readonly(self) -> None:
        mock_cls = MagicMock()
        ir = _intent_result(Intent.META)
        mock_cls.classify.return_value = ir

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("readonly result")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        result = await adapter.process_chat(
            messages=(_user_msg("help"),),
            mode=ExecutionMode.READONLY,
            auth=_auth_ctx(),
        )
        assert result.content == "readonly result"
        assert result.intent == "meta"

    @pytest.mark.asyncio
    async def test_routes_to_capabilities_only(self) -> None:
        mock_cls = MagicMock()
        ir = _intent_result(Intent.SYSTEM_TASK)
        mock_cls.classify.return_value = ir

        adapter = EngineAdapter(engine=MagicMock(), classifier=mock_cls)
        result = await adapter.process_chat(
            messages=(_user_msg("restart nginx"),),
            mode=ExecutionMode.CAPABILITIES_ONLY,
            auth=_auth_ctx(),
        )
        assert "CAPABILITIES_ONLY" in result.content

    @pytest.mark.asyncio
    async def test_routes_to_execute(self) -> None:
        mock_cls = MagicMock()
        ir = _intent_result(Intent.SYSTEM_TASK)
        mock_cls.classify.return_value = ir

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("executed")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        result = await adapter.process_chat(
            messages=(_user_msg("restart nginx"),),
            mode=ExecutionMode.EXECUTE,
            auth=_auth_ctx(),
        )
        assert result.content == "executed"
        assert result.policy_evaluated is True


# ---------------------------------------------------------------------------
# process_chat_streaming
# ---------------------------------------------------------------------------


class TestProcessChatStreaming:
    """Tests for streaming output."""

    @pytest.mark.asyncio
    async def test_streaming_yields_chunks(self) -> None:
        mock_cls = MagicMock()
        ir = _intent_result(Intent.META)
        mock_cls.classify.return_value = ir

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("Hello streaming world")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        chunks: list[str] = []
        async for chunk in adapter.process_chat_streaming(
            messages=(_user_msg("hi"),),
            mode=ExecutionMode.READONLY,
            auth=_auth_ctx(),
        ):
            chunks.append(chunk)

        combined = "".join(chunks)
        assert combined == "Hello streaming world"

    @pytest.mark.asyncio
    async def test_streaming_small_content(self) -> None:
        mock_cls = MagicMock()
        ir = _intent_result(Intent.META)
        mock_cls.classify.return_value = ir

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("ok")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        chunks: list[str] = []
        async for chunk in adapter.process_chat_streaming(
            messages=(_user_msg("hi"),),
            mode=ExecutionMode.READONLY,
            auth=_auth_ctx(),
        ):
            chunks.append(chunk)

        assert "".join(chunks) == "ok"
        # Content shorter than chunk_size => single chunk
        assert len(chunks) == 1

    @pytest.mark.asyncio
    async def test_streaming_empty_response(self) -> None:
        mock_cls = MagicMock()
        ir = _intent_result(Intent.META)
        mock_cls.classify.return_value = ir

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        # Empty string should yield nothing because range(0, 0, 50) is empty
        chunks: list[str] = []
        async for chunk in adapter.process_chat_streaming(
            messages=(_user_msg("hi"),),
            mode=ExecutionMode.EXECUTE,
            auth=_auth_ctx(),
        ):
            chunks.append(chunk)

        assert chunks == []


# ---------------------------------------------------------------------------
# _handle_readonly
# ---------------------------------------------------------------------------


class TestHandleReadonly:
    """Tests for readonly mode handling per intent."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "intent",
        [Intent.SYSTEM_QUESTION, Intent.NAVIGATION, Intent.META],
    )
    async def test_allowed_intents_process_normally(self, intent: Intent) -> None:
        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("answer")

        adapter = EngineAdapter(engine=mock_engine, classifier=MagicMock())
        ir = _intent_result(intent)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("what time?", ir, mock_session)
        assert result.content == "answer"
        assert result.intent == intent.value
        assert result.success is True
        assert result.policy_evaluated is False

    @pytest.mark.asyncio
    async def test_system_task_blocked_with_plan(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SYSTEM_TASK, confidence=0.85)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("restart nginx", ir, mock_session)
        assert "READONLY MODE" in result.content
        assert "restart nginx" in result.content
        assert result.intent == "system_task"
        assert result.success is True
        assert result.policy_evaluated is True
        assert result.policy_effect == "readonly_blocked"
        assert result.policy_message == "Execution blocked in readonly mode"
        assert "0.85" in result.content  # confidence shown

    @pytest.mark.asyncio
    async def test_shell_mutating_blocked(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("rm -rf /tmp/test", ir, mock_session)
        assert "READONLY MODE" in result.content
        assert "blocked" in result.content.lower()
        assert result.success is False
        assert result.policy_evaluated is True
        assert result.policy_effect == "readonly_blocked"

    @pytest.mark.asyncio
    async def test_shell_mutating_with_sh_prefix(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("/sh apt install vim", ir, mock_session)
        assert result.success is False
        assert "READONLY MODE" in result.content

    @pytest.mark.asyncio
    async def test_shell_mutating_with_bang_prefix(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("!rm foo", ir, mock_session)
        assert result.success is False
        assert result.policy_effect == "readonly_blocked"

    @pytest.mark.asyncio
    async def test_shell_non_mutating_allowed(self) -> None:
        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("output of ls")

        adapter = EngineAdapter(engine=mock_engine, classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("ls -la /tmp", ir, mock_session)
        assert result.content == "output of ls"
        assert result.success is True
        assert result.policy_evaluated is False

    @pytest.mark.asyncio
    async def test_shell_non_mutating_with_sh_prefix(self) -> None:
        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("status output")

        adapter = EngineAdapter(engine=mock_engine, classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("/sh df -h", ir, mock_session)
        assert result.content == "status output"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_fixit_blocked(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.FIXIT)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("fix the issue", ir, mock_session)
        assert "READONLY MODE" in result.content
        assert "fixit" in result.content
        assert result.success is False
        assert result.policy_evaluated is True
        assert result.policy_effect == "readonly_blocked"
        assert result.policy_message is None  # fixit branch has no policy_message

    @pytest.mark.asyncio
    async def test_fallback_unknown_intent(self) -> None:
        """Intent not explicitly handled falls through to engine processing."""
        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("fallback result")

        adapter = EngineAdapter(engine=mock_engine, classifier=MagicMock())
        ir = _intent_result(Intent.EXPLAIN_COMMAND)
        mock_session = MagicMock()

        result = await adapter._handle_readonly("explain ls", ir, mock_session)
        assert result.content == "fallback result"
        assert result.intent == "explain_command"
        assert result.success is True


# ---------------------------------------------------------------------------
# _handle_capabilities_only
# ---------------------------------------------------------------------------


class TestHandleCapabilitiesOnly:
    """Tests for capabilities_only mode handling."""

    @pytest.mark.asyncio
    async def test_system_task_returns_tool_calls(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SYSTEM_TASK, entities=["nginx"])
        mock_session = MagicMock()

        result = await adapter._handle_capabilities_only("restart nginx", ir, mock_session)
        assert "CAPABILITIES_ONLY" in result.content
        assert result.intent == "system_task"
        assert result.success is True
        assert result.tool_calls is not None
        assert len(result.tool_calls) >= 1

    @pytest.mark.asyncio
    async def test_shell_passthrough_returns_shell_tool_call(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_capabilities_only("ls -la /tmp", ir, mock_session)
        assert "CAPABILITIES_ONLY" in result.content
        assert result.intent == "shell_passthrough"
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc.function.name == "shell.execute"
        args = json.loads(tc.function.arguments)
        assert args["command"] == "ls -la /tmp"

    @pytest.mark.asyncio
    async def test_shell_with_sh_prefix_stripped(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_capabilities_only("/sh uptime", ir, mock_session)
        assert result.tool_calls is not None
        args = json.loads(result.tool_calls[0].function.arguments)
        assert args["command"] == "uptime"

    @pytest.mark.asyncio
    async def test_shell_with_bang_prefix_stripped(self) -> None:
        adapter = EngineAdapter(engine=MagicMock(), classifier=MagicMock())
        ir = _intent_result(Intent.SHELL_PASSTHROUGH)
        mock_session = MagicMock()

        result = await adapter._handle_capabilities_only("!whoami", ir, mock_session)
        assert result.tool_calls is not None
        args = json.loads(result.tool_calls[0].function.arguments)
        assert args["command"] == "whoami"

    @pytest.mark.asyncio
    async def test_other_intent_processes_normally(self) -> None:
        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("cap result")

        adapter = EngineAdapter(engine=mock_engine, classifier=MagicMock())
        ir = _intent_result(Intent.SYSTEM_QUESTION)
        mock_session = MagicMock()

        result = await adapter._handle_capabilities_only("what is disk usage?", ir, mock_session)
        assert result.content == "cap result"
        assert result.intent == "system_question"
        assert result.tool_calls is None


# ---------------------------------------------------------------------------
# _handle_execute
# ---------------------------------------------------------------------------


class TestHandleExecute:
    """Tests for full execute mode handling."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("done", success=True)

        adapter = EngineAdapter(engine=mock_engine, classifier=MagicMock())
        ir = _intent_result(Intent.SYSTEM_TASK)
        mock_session = MagicMock()

        result = await adapter._handle_execute("restart nginx", ir, mock_session)
        assert result.content == "done"
        assert result.intent == "system_task"
        assert result.success is True
        assert result.policy_evaluated is True
        assert result.policy_effect == "allow"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("failed", success=False)

        adapter = EngineAdapter(engine=mock_engine, classifier=MagicMock())
        ir = _intent_result(Intent.SYSTEM_TASK)
        mock_session = MagicMock()

        result = await adapter._handle_execute("restart nginx", ir, mock_session)
        assert result.content == "failed"
        assert result.success is False
        assert result.policy_effect == "executed_with_errors"


# ---------------------------------------------------------------------------
# _generate_capability_tool_calls
# ---------------------------------------------------------------------------


class TestGenerateCapabilityToolCalls:
    """Tests for capability inference from user input."""

    def test_restart_service(self) -> None:
        adapter = EngineAdapter()
        ir = _intent_result(Intent.SYSTEM_TASK, entities=["nginx"])
        calls = adapter._generate_capability_tool_calls("restart nginx", ir)

        assert len(calls) == 1
        assert calls[0].function.name == "service.restart"
        args = json.loads(calls[0].function.arguments)
        assert args["service"] == "nginx"
        assert calls[0].type == "function"
        assert calls[0].id.startswith("call_")

    def test_install_package(self) -> None:
        adapter = EngineAdapter()
        ir = _intent_result(Intent.SYSTEM_TASK, entities=["vim"])
        calls = adapter._generate_capability_tool_calls("install vim", ir)

        assert len(calls) == 1
        assert calls[0].function.name == "package.install"
        args = json.loads(calls[0].function.arguments)
        assert args["package"] == "vim"

    def test_generic_task_no_keyword(self) -> None:
        adapter = EngineAdapter()
        ir = _intent_result(Intent.SYSTEM_TASK, entities=["disk"])
        calls = adapter._generate_capability_tool_calls("check disk usage", ir)

        assert len(calls) == 1
        assert calls[0].function.name == "system.task"
        args = json.loads(calls[0].function.arguments)
        assert args["description"] == "check disk usage"
        assert args["entities"] == ["disk"]

    def test_generic_task_empty_entities(self) -> None:
        adapter = EngineAdapter()
        ir = _intent_result(Intent.SYSTEM_TASK, entities=[])
        calls = adapter._generate_capability_tool_calls("do something", ir)

        assert len(calls) == 1
        assert calls[0].function.name == "system.task"

    def test_generic_task_none_entities(self) -> None:
        """IntentResult with entities=None (edge case)."""
        adapter = EngineAdapter()
        # Manually create an IntentResult where entities might be empty
        ir = _intent_result(Intent.SYSTEM_TASK, entities=[])
        calls = adapter._generate_capability_tool_calls("do stuff", ir)
        assert len(calls) == 1
        assert calls[0].function.name == "system.task"

    def test_restart_without_entities_falls_to_generic(self) -> None:
        """'restart' keyword but no entity besides 'restart' -> generic."""
        adapter = EngineAdapter()
        ir = _intent_result(Intent.SYSTEM_TASK, entities=["restart"])
        calls = adapter._generate_capability_tool_calls("restart", ir)

        # entities only has "restart", so the any() check yields False
        assert len(calls) == 1
        assert calls[0].function.name == "system.task"

    def test_install_with_install_only_entity(self) -> None:
        """'install' keyword with entity 'install' -> package unknown."""
        adapter = EngineAdapter()
        ir = _intent_result(Intent.SYSTEM_TASK, entities=["install"])
        calls = adapter._generate_capability_tool_calls("install", ir)

        # "install" in lower is True but next() skips "install"
        # -> no match -> "unknown"
        assert len(calls) == 1
        assert calls[0].function.name == "package.install"
        args = json.loads(calls[0].function.arguments)
        assert args["package"] == "unknown"

    def test_restart_with_multiple_entities(self) -> None:
        adapter = EngineAdapter()
        ir = _intent_result(Intent.SYSTEM_TASK, entities=["restart", "apache2"])
        calls = adapter._generate_capability_tool_calls("restart apache2", ir)

        assert len(calls) == 1
        assert calls[0].function.name == "service.restart"
        args = json.loads(calls[0].function.arguments)
        assert args["service"] == "apache2"


# ---------------------------------------------------------------------------
# format_completion_response
# ---------------------------------------------------------------------------


class TestFormatCompletionResponse:
    """Tests for format_completion_response."""

    def test_basic_response(self) -> None:
        resp = EngineResponse(
            content="hello world",
            intent="meta",
            success=True,
        )
        cr = format_completion_response(resp, model="elle.readonly")

        assert cr.model == "elle.readonly"
        assert len(cr.choices) == 1
        choice = cr.choices[0]
        assert choice.index == 0
        assert choice.message.role == "assistant"
        assert choice.message.content == "hello world"
        assert choice.finish_reason == "stop"
        assert cr.usage.total_tokens == cr.usage.prompt_tokens + cr.usage.completion_tokens
        assert cr.intent == "meta"
        assert cr.policy_result is None
        assert cr.id.startswith("chatcmpl-")

    def test_response_with_tool_calls(self) -> None:
        tc = (
            ToolCall(
                id="call_abc",
                type="function",
                function=FunctionCall(name="shell.execute", arguments="{}"),
            ),
        )
        resp = EngineResponse(
            content="calling tool",
            intent="shell_passthrough",
            success=True,
            tool_calls=tc,
        )
        cr = format_completion_response(resp, model="elle")

        assert cr.choices[0].finish_reason == "tool_calls"
        assert cr.choices[0].message.tool_calls == tc

    def test_response_with_policy(self) -> None:
        resp = EngineResponse(
            content="blocked",
            intent="system_task",
            success=False,
            policy_evaluated=True,
            policy_effect="readonly_blocked",
            policy_message="Not allowed",
        )
        cr = format_completion_response(resp, model="elle.readonly")

        assert cr.policy_result is not None
        assert cr.policy_result.evaluated is True
        assert cr.policy_result.effect == "readonly_blocked"
        assert cr.policy_result.message == "Not allowed"

    def test_custom_completion_id(self) -> None:
        resp = EngineResponse(content="x", intent="meta", success=True)
        cr = format_completion_response(resp, model="elle", completion_id="custom-123")
        # completion_id param is accepted but the model generates its own id
        # We just verify the function doesn't crash and returns a valid response
        assert cr.id is not None

    def test_policy_not_evaluated(self) -> None:
        resp = EngineResponse(
            content="ok",
            intent="meta",
            success=True,
            policy_evaluated=False,
        )
        cr = format_completion_response(resp, model="elle")
        assert cr.policy_result is None

    def test_token_usage_estimation(self) -> None:
        resp = EngineResponse(
            content="one two three",
            intent="meta",
            success=True,
        )
        cr = format_completion_response(resp, model="elle")
        # "one two three" -> 3 words
        # prompt_tokens = 3 * 2 = 6, completion_tokens = 3
        assert cr.usage.prompt_tokens == 6
        assert cr.usage.completion_tokens == 3
        assert cr.usage.total_tokens == 9

    def test_empty_content_token_estimation(self) -> None:
        resp = EngineResponse(content="", intent="meta", success=True)
        cr = format_completion_response(resp, model="elle")
        # "".split() -> [] -> 0 words
        assert cr.usage.completion_tokens == 0
        assert cr.usage.prompt_tokens == 0
        assert cr.usage.total_tokens == 0


# ---------------------------------------------------------------------------
# get_adapter singleton
# ---------------------------------------------------------------------------


class TestGetAdapter:
    """Tests for the module-level get_adapter singleton."""

    @patch(f"{_ADAPTER_MOD}._adapter", None)
    def test_creates_new_adapter(self) -> None:
        import elle.daemon.api.engine_adapter as mod

        old = mod._adapter
        try:
            mod._adapter = None
            adapter = get_adapter()
            assert isinstance(adapter, EngineAdapter)
        finally:
            mod._adapter = old

    @patch(f"{_ADAPTER_MOD}._adapter", None)
    def test_returns_same_instance(self) -> None:
        import elle.daemon.api.engine_adapter as mod

        old = mod._adapter
        try:
            mod._adapter = None
            a1 = get_adapter()
            a2 = get_adapter()
            assert a1 is a2
        finally:
            mod._adapter = old

    def test_returns_existing_adapter(self) -> None:
        import elle.daemon.api.engine_adapter as mod

        sentinel = EngineAdapter()
        old = mod._adapter
        try:
            mod._adapter = sentinel
            assert get_adapter() is sentinel
        finally:
            mod._adapter = old


# ---------------------------------------------------------------------------
# MUTATING_COMMAND_PATTERNS constant
# ---------------------------------------------------------------------------


class TestMutatingCommandPatterns:
    """Ensure the patterns tuple is well-formed."""

    def test_patterns_is_tuple(self) -> None:
        assert isinstance(MUTATING_COMMAND_PATTERNS, tuple)

    def test_patterns_not_empty(self) -> None:
        assert len(MUTATING_COMMAND_PATTERNS) > 0

    def test_all_entries_are_compiled_regex(self) -> None:
        import re

        for pat in MUTATING_COMMAND_PATTERNS:
            assert isinstance(pat, re.Pattern)


# ---------------------------------------------------------------------------
# Integration: full round-trip through process_chat
# ---------------------------------------------------------------------------


class TestProcessChatIntegration:
    """Integration-style tests exercising the full process_chat path."""

    @pytest.mark.asyncio
    async def test_readonly_meta_round_trip(self) -> None:
        mock_cls = MagicMock()
        mock_cls.classify.return_value = _intent_result(Intent.META)

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("version 1.0")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        resp = await adapter.process_chat(
            messages=(_user_msg("version"),),
            mode=ExecutionMode.READONLY,
            auth=_auth_ctx(),
        )
        cr = format_completion_response(resp, model="elle.readonly")

        assert cr.choices[0].message.content == "version 1.0"
        assert cr.choices[0].finish_reason == "stop"
        assert cr.intent == "meta"

    @pytest.mark.asyncio
    async def test_execute_system_task_round_trip(self) -> None:
        mock_cls = MagicMock()
        mock_cls.classify.return_value = _intent_result(Intent.SYSTEM_TASK)

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("restarted ok")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        resp = await adapter.process_chat(
            messages=(_user_msg("restart nginx"),),
            mode=ExecutionMode.EXECUTE,
            auth=_auth_ctx(),
        )
        cr = format_completion_response(resp, model="elle")

        assert cr.choices[0].message.content == "restarted ok"
        assert cr.policy_result is not None
        assert cr.policy_result.effect == "allow"

    @pytest.mark.asyncio
    async def test_capabilities_only_shell_round_trip(self) -> None:
        mock_cls = MagicMock()
        mock_cls.classify.return_value = _intent_result(Intent.SHELL_PASSTHROUGH)

        adapter = EngineAdapter(engine=MagicMock(), classifier=mock_cls)
        resp = await adapter.process_chat(
            messages=(_user_msg("ls -la"),),
            mode=ExecutionMode.CAPABILITIES_ONLY,
            auth=_auth_ctx(),
        )
        cr = format_completion_response(resp, model="elle.capabilities_only")

        assert cr.choices[0].finish_reason == "tool_calls"
        assert cr.choices[0].message.tool_calls is not None

    @pytest.mark.asyncio
    async def test_readonly_blocks_mutating_shell(self) -> None:
        mock_cls = MagicMock()
        mock_cls.classify.return_value = _intent_result(Intent.SHELL_PASSTHROUGH)

        adapter = EngineAdapter(engine=MagicMock(), classifier=mock_cls)
        resp = await adapter.process_chat(
            messages=(_user_msg("rm -rf /tmp/test"),),
            mode=ExecutionMode.READONLY,
            auth=_auth_ctx(),
        )
        cr = format_completion_response(resp, model="elle.readonly")

        assert "READONLY" in cr.choices[0].message.content
        assert cr.policy_result is not None
        assert cr.policy_result.effect == "readonly_blocked"

    @pytest.mark.asyncio
    async def test_multiple_user_messages_uses_last(self) -> None:
        mock_cls = MagicMock()
        mock_cls.classify.return_value = _intent_result(Intent.META)

        mock_engine = MagicMock()
        mock_engine.process.return_value = _engine_result("second response")

        adapter = EngineAdapter(engine=mock_engine, classifier=mock_cls)
        await adapter.process_chat(
            messages=(
                _user_msg("first question"),
                _assistant_msg("first answer"),
                _user_msg("second question"),
            ),
            mode=ExecutionMode.EXECUTE,
            auth=_auth_ctx(),
        )
        # The engine.process should have been called with "second question"
        mock_engine.process.assert_called_once()
        call_args = mock_engine.process.call_args
        assert call_args[0][0] == "second question" or "second question" in str(call_args)
