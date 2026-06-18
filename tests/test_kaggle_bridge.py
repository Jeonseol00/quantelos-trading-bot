import time
import pytest
from unittest.mock import patch, MagicMock

# Attempt to import tomllib
try:
    import tomllib
except ImportError:
    import tomli as tomllib

from python_node.kaggle_bridge import KaggleBridge

def test_config_validation_missing_key():
    cfg = {
        "kaggle": {"sync_method": "manual"},
    }
    with pytest.raises(ValueError) as excinfo:
        KaggleBridge._validate_config(cfg)
    assert "FATAL: required config" in str(excinfo.value)

def test_config_validation_success():
    cfg = {
        "kaggle": {
            "sync_method": "manual",
            "router_api_key": "sk-123",
            "target_model": "model/x",
        },
        "oanda": {
            "instruments": ["EUR_USD"]
        }
    }
    # Should not raise
    KaggleBridge._validate_config(cfg)

@patch("builtins.open")
def test_duplicate_key_toml_decode_error(mock_open):
    try:
        import tomllib
        target_mock = "tomllib.load"
    except ImportError:
        import tomli as tomllib
        target_mock = "tomli.load"

    with patch(target_mock) as mock_load:
        mock_load.side_effect = tomllib.TOMLDecodeError("Cannot overwrite a value", "", 0)
        mock_open.return_value.__enter__.return_value = MagicMock()
        
        with pytest.raises(tomllib.TOMLDecodeError):
            KaggleBridge(db_manager=MagicMock())

@patch("python_node.kaggle_bridge.requests.post")
@patch("python_node.kaggle_bridge.KaggleBridge._get_kaggle_url")
def test_streaming_deadline(mock_get_url, mock_post):
    mock_get_url.return_value = "https://openrouter.ai"
    
    cfg = {
        "kaggle": {
            "sync_method": "manual",
            "router_api_key": "sk-123",
            "target_model": "test-model",
            "inference_timeout_s": 0.5  # VERY short timeout for test
        },
        "oanda": {"instruments": ["EUR_USD"]}
    }
    
    try:
        import tomllib
        target_mock = "tomllib.load"
    except ImportError:
        import tomli as tomllib
        target_mock = "tomli.load"
    
    with patch(target_mock, return_value=cfg), \
         patch("builtins.open"):
        bridge = KaggleBridge(db_manager=MagicMock())
        
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    
    def slow_iter_lines():
        start = time.time()
        yield b'data: {"choices": [{"delta": {"content": "start"}}]}'
        # Loop enough to breach the 0.5s timeout, keep yielding so iter_lines doesn't finish naturally
        while time.time() - start < 1.0:
            time.sleep(0.1)
            yield b'data: {"choices": [{"delta": {"content": "..."}}]}'
            
    mock_resp.iter_lines.side_effect = slow_iter_lines
    mock_post.return_value = mock_resp
    
    res = bridge.query_llm("Analyze this")
    
    # Should have broken out of loop and not hang
    assert "start" in res
    # Verify the socket was closed
    mock_resp.close.assert_called()
