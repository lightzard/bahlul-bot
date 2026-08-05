"""
Local test script for the DeepSeek chat integration.

Run mocked unit tests (no API key required):
    python -m pytest test_chat.py -v

Or run directly:
    python test_chat.py

Run a real integration test against the DeepSeek API (requires DEEPSEEK_API_KEY):
    python test_chat.py --live
"""

import asyncio
import os
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch

# Set dummy env vars so app.py module-level code works
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")


def test_message_building():
    """Test that conversation history is correctly converted to OpenAI-compatible messages."""
    from api.app import get_deepseek_response, DEEPSEEK_MODEL

    conversation = [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."},
        {"role": "user", "content": "What is its population?"},
    ]

    # Create a mock response object
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "The population is about 2.2 million."

    # Mock the AsyncOpenAI client
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("api.app.AsyncOpenAI", return_value=mock_client):
        result = asyncio.run(get_deepseek_response(conversation))

    assert result == "The population is about 2.2 million.", f"Unexpected response: {result}"

    # Verify the API was called with correct model and messages
    mock_client.chat.completions.create.assert_called_once()
    call_args = mock_client.chat.completions.create.call_args
    assert call_args.kwargs["model"] == DEEPSEEK_MODEL, f"Wrong model: {call_args.kwargs['model']}"

    messages = call_args.kwargs["messages"]
    # Should have 3 history messages + 1 system output-limit instruction
    assert len(messages) == 4, f"Expected 4 messages, got {len(messages)}: {messages}"
    assert messages[0] == {"role": "user", "content": "What is the capital of France?"}
    assert messages[1] == {"role": "assistant", "content": "The capital of France is Paris."}
    assert messages[2] == {"role": "user", "content": "What is its population?"}
    assert messages[3]["role"] == "system"
    assert "4096" in messages[3]["content"], "Output limit instruction missing"

    print("✓ test_message_building passed")


def test_system_message_list_format():
    """Test that legacy list-format system messages are handled correctly."""
    from api.app import get_deepseek_response

    conversation = [
        {"role": "system", "content": [{"type": "text", "text": "You are a helpful bot."}]},
        {"role": "user", "content": "Hello"},
    ]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi there!"

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("api.app.AsyncOpenAI", return_value=mock_client):
        result = asyncio.run(get_deepseek_response(conversation))

    assert result == "Hi there!"

    call_args = mock_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    # First message should be the converted system message
    assert messages[0] == {"role": "system", "content": "You are a helpful bot."}, \
        f"List-format system message not converted: {messages[0]}"
    assert messages[1] == {"role": "user", "content": "Hello"}

    print("✓ test_system_message_list_format passed")


def test_missing_api_key():
    """Test that a clear error is raised when DEEPSEEK_API_KEY is missing."""
    from api.app import get_deepseek_response

    with patch("api.app.DEEPSEEK_API_KEY", None):
        try:
            asyncio.run(get_deepseek_response([{"role": "user", "content": "test"}]))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "DEEPSEEK_API_KEY" in str(e), f"Unexpected error: {e}"
            print("✓ test_missing_api_key passed")


def test_model_name():
    """Test that the default model is deepseek-v4-flash."""
    from api.app import DEEPSEEK_MODEL
    assert DEEPSEEK_MODEL == "deepseek-v4-flash", f"Unexpected default model: {DEEPSEEK_MODEL}"
    print(f"✓ test_model_name passed (model={DEEPSEEK_MODEL})")


def test_base_url():
    """Test that the DeepSeek base URL is configured correctly."""
    from api.app import DEEPSEEK_BASE_URL
    assert "deepseek.com" in DEEPSEEK_BASE_URL, f"Unexpected base URL: {DEEPSEEK_BASE_URL}"
    print(f"✓ test_base_url passed (base_url={DEEPSEEK_BASE_URL})")


async def live_test():
    """Real integration test against the DeepSeek API (requires DEEPSEEK_API_KEY)."""
    from api.app import get_deepseek_response

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key or api_key == "test-deepseek-key":
        print("Skipping live test: set DEEPSEEK_API_KEY environment variable to run it")
        return False

    conversation = [
        {"role": "user", "content": "Reply with exactly: CONNECTION TEST OK"},
    ]
    try:
        result = await get_deepseek_response(conversation)
        print(f"✓ Live test succeeded! Response: {result[:200]}")
        return True
    except Exception as e:
        print(f"✗ Live test failed: {e}")
        return False


def run_mocked_tests():
    """Run all mocked tests."""
    print("=" * 60)
    print("Running mocked unit tests (no API key needed)")
    print("=" * 60)
    test_model_name()
    test_base_url()
    test_missing_api_key()
    test_message_building()
    test_system_message_list_format()
    print("=" * 60)
    print("All mocked tests passed! ✓")
    print("=" * 60)


if __name__ == "__main__":
    if "--live" in sys.argv:
        print("=" * 60)
        print("Running live integration test against DeepSeek API")
        print("=" * 60)
        success = asyncio.run(live_test())
        sys.exit(0 if success else 1)
    else:
        run_mocked_tests()