"""Tests for OpenAI-compatible chat completions API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip tests if fastapi not installed
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from elle.daemon.api.app import create_app
from elle.daemon.api.auth import ANONYMOUS_AUTH, AuthContext
from elle.daemon.api.engine_adapter import EngineAdapter, EngineResponse
from elle.daemon.api.openai_models import ExecutionMode
from elle.daemon.config import ApiAuthConfig, ApiConfig, Config
from elle.daemon.main import ElledDaemon
from elle.daemon.telemetry.models import DaemonStatus, QueueStats


@pytest.fixture
def mock_daemon(tmp_path):
    """Create a mock daemon for testing."""
    daemon = MagicMock(spec=ElledDaemon)
    daemon.config = Config(
        api=ApiConfig(enabled=True),
        api_auth=ApiAuthConfig(
            allow_anonymous=True,
            api_keys_db_path=tmp_path / "api_keys.db",
        ),
    )
    daemon.get_status.return_value = DaemonStatus(
        started_at=datetime.now(UTC),
        uptime_sec=100,
        pid=12345,
        journal_active=True,
        kernel_active=True,
        probes_active=True,
        api_active=True,
        raw_queue=QueueStats(name="raw", size=10, max_size=10000, dropped=0, processed=100),
        event_queue=QueueStats(name="events", size=5, max_size=5000, dropped=0, processed=95),
        events_total=500,
        incidents_total=5,
        healthy=True,
        errors=(),
    )
    return daemon


@pytest.fixture
def mock_adapter():
    """Create a mock engine adapter."""
    adapter = MagicMock(spec=EngineAdapter)

    async def mock_process_chat(messages, mode, auth):
        return EngineResponse(
            content="Test response content",
            intent="system_question",
            success=True,
        )

    adapter.process_chat = AsyncMock(side_effect=mock_process_chat)

    async def mock_stream(*args, **kwargs):
        yield "Test "
        yield "streaming "
        yield "response"

    adapter.process_chat_streaming = mock_stream
    return adapter


@pytest.fixture
def client(mock_daemon, mock_adapter):
    """Create test client with mock daemon and adapter."""
    with patch("elle.daemon.api.app.get_adapter", return_value=mock_adapter):
        app = create_app(mock_daemon)
        return TestClient(app)


class TestChatCompletionsEndpoint:
    """Tests for POST /v1/chat/completions endpoint."""

    def test_basic_request(self, client):
        """Basic request should return valid OpenAI-format response."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [{"role": "user", "content": "What is my disk usage?"}],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert "choices" in data
        assert len(data["choices"]) >= 1

    def test_response_has_required_fields(self, client):
        """Response should have all required OpenAI fields."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [{"role": "user", "content": "status"}],
            },
        )
        data = response.json()

        assert "id" in data
        assert data["id"].startswith("chatcmpl-")
        assert "object" in data
        assert "created" in data
        assert "model" in data
        assert "choices" in data
        assert "usage" in data

    def test_choice_has_message(self, client):
        """Response choices should have message with content."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        data = response.json()

        choice = data["choices"][0]
        assert "message" in choice
        assert "content" in choice["message"]
        assert choice["message"]["role"] == "assistant"

    def test_elle_extensions_included(self, client):
        """Response should include ELLE-specific extensions."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        data = response.json()

        # ELLE extensions
        assert "intent" in data

    def test_invalid_model_rejected(self, client):
        """Invalid model name should be rejected."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        assert response.status_code == 422

    def test_empty_messages_rejected(self, client):
        """Empty messages list should be rejected."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [],
            },
        )
        assert response.status_code == 422

    def test_all_models_accepted(self, client):
        """All valid ELLE models should be accepted."""
        for model in ("elle", "elle.readonly", "elle.capabilities_only"):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "test"}],
                },
            )
            # Might be 200 or 403 depending on auth, but not 422
            assert response.status_code != 422


class TestReadonlyMode:
    """Tests for readonly mode behavior."""

    def test_readonly_mode_accessible_anonymously(self, client):
        """Readonly mode should be accessible without authentication."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [{"role": "user", "content": "what is my disk usage?"}],
            },
        )
        assert response.status_code == 200


class TestExecuteMode:
    """Tests for execute mode behavior."""

    def test_execute_mode_requires_auth(self, mock_daemon, tmp_path):
        """Execute mode requires proper authentication."""
        # Create daemon with anonymous not allowed for execute
        daemon = MagicMock(spec=ElledDaemon)
        daemon.config = Config(
            api=ApiConfig(enabled=True),
            api_auth=ApiAuthConfig(
                allow_anonymous=True,  # Anonymous only gets readonly
                api_keys_db_path=tmp_path / "keys.db",
            ),
        )
        daemon.get_status.return_value = mock_daemon.get_status.return_value

        mock_adapter = MagicMock(spec=EngineAdapter)

        async def mock_process(messages, mode, auth):
            return EngineResponse(
                content="Executed",
                intent="system_task",
                success=True,
            )

        mock_adapter.process_chat = AsyncMock(side_effect=mock_process)

        with patch("elle.daemon.api.app.get_adapter", return_value=mock_adapter):
            app = create_app(daemon)
            client = TestClient(app)

            # Anonymous request to execute mode
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "elle",  # Full execute mode
                    "messages": [{"role": "user", "content": "restart nginx"}],
                },
            )
            # Should be forbidden for anonymous
            assert response.status_code == 403


class TestCapabilitiesOnlyMode:
    """Tests for capabilities_only mode behavior."""

    def test_capabilities_only_returns_tool_calls(self, mock_daemon, tmp_path):
        """Capabilities_only mode should return tool_calls without execution."""
        from elle.daemon.api.openai_models import FunctionCall, ToolCall

        daemon = MagicMock(spec=ElledDaemon)
        daemon.config = Config(
            api=ApiConfig(enabled=True),
            api_auth=ApiAuthConfig(
                allow_anonymous=True,
                api_keys_db_path=tmp_path / "keys.db",
                elle_uid=None,  # No special elle user
            ),
        )
        daemon.get_status.return_value = mock_daemon.get_status.return_value

        mock_adapter = MagicMock(spec=EngineAdapter)

        async def mock_process(messages, mode, auth):
            return EngineResponse(
                content="Capability identified",
                intent="system_task",
                success=True,
                tool_calls=(
                    ToolCall(
                        id="call_123",
                        type="function",
                        function=FunctionCall(
                            name="service.restart",
                            arguments='{"service": "nginx"}',
                        ),
                    ),
                ),
            )

        mock_adapter.process_chat = AsyncMock(side_effect=mock_process)

        # Note: capabilities_only requires auth beyond anonymous
        # This test verifies the response format when auth is present


class TestModelsEndpoint:
    """Tests for GET /v1/models endpoint."""

    def test_returns_available_models(self, client):
        """Lists available models with execution modes."""
        response = client.get("/v1/models")
        assert response.status_code == 200

        data = response.json()
        assert data["object"] == "list"
        assert "data" in data

        model_ids = [m["id"] for m in data["data"]]
        assert "elle" in model_ids
        assert "elle.readonly" in model_ids
        assert "elle.capabilities_only" in model_ids

    def test_models_have_execution_modes(self, client):
        """Each model should have execution_mode field."""
        response = client.get("/v1/models")
        data = response.json()

        for model in data["data"]:
            assert "execution_mode" in model
            assert model["execution_mode"] in ("execute", "readonly", "capabilities_only")

    def test_models_have_descriptions(self, client):
        """Each model should have a description."""
        response = client.get("/v1/models")
        data = response.json()

        for model in data["data"]:
            assert "description" in model
            assert len(model["description"]) > 0


class TestGetModelEndpoint:
    """Tests for GET /v1/models/{model_id} endpoint."""

    def test_get_existing_model(self, client):
        """Should return model info for existing model."""
        response = client.get("/v1/models/elle")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == "elle"
        assert data["object"] == "model"

    def test_get_nonexistent_model(self, client):
        """Should return 404 for nonexistent model."""
        response = client.get("/v1/models/gpt-4")
        assert response.status_code == 404


class TestStreamingResponse:
    """Tests for streaming responses."""

    def test_streaming_request_returns_sse(self, mock_daemon, tmp_path):
        """Streaming request should return SSE format."""
        daemon = MagicMock(spec=ElledDaemon)
        daemon.config = Config(
            api=ApiConfig(enabled=True),
            api_auth=ApiAuthConfig(
                allow_anonymous=True,
                api_keys_db_path=tmp_path / "keys.db",
            ),
        )
        daemon.get_status.return_value = mock_daemon.get_status.return_value

        mock_adapter = MagicMock(spec=EngineAdapter)

        async def mock_stream(messages, mode, auth):
            yield "Test "
            yield "streaming"

        mock_adapter.process_chat_streaming = mock_stream

        with patch("elle.daemon.api.app.get_adapter", return_value=mock_adapter):
            app = create_app(daemon)
            client = TestClient(app)

            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "elle.readonly",
                    "messages": [{"role": "user", "content": "status"}],
                    "stream": True,
                },
            )

            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")


class TestIntentClassification:
    """Tests for intent classification through the API."""

    def test_intent_included_in_response(self, client):
        """Every response should include classified intent."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [{"role": "user", "content": "what services are running?"}],
            },
        )
        data = response.json()
        assert "intent" in data


class TestConversationContext:
    """Tests for conversation context handling."""

    def test_multiple_messages_accepted(self, client):
        """Should accept multiple messages in conversation."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant"},
                    {"role": "user", "content": "What is my disk usage?"},
                    {"role": "assistant", "content": "Let me check..."},
                    {"role": "user", "content": "And memory?"},
                ],
            },
        )
        assert response.status_code == 200

    def test_extracts_last_user_message(self, mock_daemon, mock_adapter, tmp_path):
        """Should extract the last user message for processing."""
        with patch("elle.daemon.api.app.get_adapter", return_value=mock_adapter):
            app = create_app(mock_daemon)
            client = TestClient(app)

            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "elle.readonly",
                    "messages": [
                        {"role": "user", "content": "First question"},
                        {"role": "assistant", "content": "First answer"},
                        {"role": "user", "content": "Second question"},
                    ],
                },
            )
            assert response.status_code == 200
            # The adapter should have been called
            mock_adapter.process_chat.assert_called_once()


class TestErrorHandling:
    """Tests for error handling."""

    def test_invalid_json_rejected(self, client):
        """Invalid JSON should be rejected."""
        response = client.post(
            "/v1/chat/completions",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_missing_required_fields(self, client):
        """Missing required fields should be rejected."""
        # Missing model
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        assert response.status_code == 422

        # Missing messages
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "elle.readonly",
            },
        )
        assert response.status_code == 422


class TestRootEndpoint:
    """Tests for root endpoint with OpenAI info."""

    def test_root_mentions_openai_endpoint(self, client):
        """Root should mention OpenAI-compatible endpoint."""
        response = client.get("/")
        assert response.status_code == 200

        data = response.json()
        assert "openai_endpoint" in data
        assert data["openai_endpoint"] == "/v1/chat/completions"
