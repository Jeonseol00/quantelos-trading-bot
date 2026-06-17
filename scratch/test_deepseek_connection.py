import sys
import os
import requests

# Ensure python_node is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../python_node")))

from db_manager import DatabaseManager
from kaggle_bridge import KaggleBridge

import logging

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("Testing DeepSeek-R1:32b Cloudflare Tunnel Connection...")
    
    # Initialize DB Manager
    db_path = "./data/quantelos.db"
    db = DatabaseManager(db_path=db_path)
    
    # Load config to get target timeout
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with open("config/settings.toml", "rb") as f:
        cfg = tomllib.load(f)
    timeout = cfg.get("kaggle", {}).get("inference_timeout_s", 90)

    # Initialize KaggleBridge
    bridge = KaggleBridge(db_manager=db, timeout=timeout)
    
    url = bridge._get_kaggle_url()
    print(f"Loaded Kaggle URL from SQLite: {url}")
    print(f"Target Model: {bridge.cfg.get('kaggle', {}).get('target_model')}")
    print(f"Inference Timeout: {bridge.timeout}s")
    
    if not url:
        print("[ERROR] Kaggle URL not set in database!")
        return
        
    print("\n--- Sending request to /api/tags to detect Ollama models ---")
    try:
        resp = requests.get(f"{url}/api/tags", timeout=10)
        if resp.status_code == 200:
            print("[SUCCESS] Connected to Ollama backend!")
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            print(f"Available models: {models}")
        else:
            print(f"[ERROR] Ollama tags returned status code: {resp.status_code}")
            print(resp.text)
    except Exception as e:
        print(f"[WARNING] Could not connect to /api/tags or check Ollama backend: {e}")
        print("This is normal if the tunnel is just starting up or is a direct Flask proxy.")

    print("\n--- Running mock debate inference validation ---")
    prompt = bridge.build_debate_prompt(
        pair="XAU_USD",
        direction="BUY",
        entry=2034.50,
        catalyst="US Core CPI MoM prints 0.4% vs 0.2% forecast, showing persistent inflation pressure."
    )
    
    print("Sending request... (DeepSeek thinking loop might take a few seconds)")
    try:
        decision = bridge.get_decision(prompt)
        print("\n[SUCCESS] Received response from model!")
        print(f"Recommended Action: {decision.get('recommended_direction')}")
        print(f"Final Sentiment: {decision.get('final_sentiment')}")
        print(f"Confidence: {decision.get('confidence')}")
        print(f"Risk Assessment: {decision.get('risk_assessment')}")
        print(f"Decision Source: {decision.get('source', 'LLM Engine')}")
    except Exception as e:
        print(f"[ERROR] Inference query failed: {e}")

if __name__ == "__main__":
    main()
