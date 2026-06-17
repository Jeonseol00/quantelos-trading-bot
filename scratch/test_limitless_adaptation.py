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
    print("Testing Quantelos Limitless Adaptation Cognitive Recall & Debate...")
    
    # Initialize DB Manager
    db_path = "./data/quantelos.db"
    db = DatabaseManager(db_path=db_path)
    
    # Check if there are failures in db
    failures = db.get_recent_failures(limit=3)
    print(f"\nFetched {len(failures)} recent failures from SQLite:")
    for idx, f in enumerate(failures, 1):
        print(f"[{f.get('type', 'UNKNOWN')}] {f.get('pair')}: Pips: {f.get('pips_gained')}, Lesson: {f.get('ai_lessons_learned')}")
        
    # If no failures, write a mock failure for testing
    if not failures:
        print("\n[INFO] No failures found in DB, inserting mock failure for validation...")
        with db._connect() as conn:
            conn.execute("""
                INSERT INTO weekend_training_logs 
                (pair, predicted_direction, entry_price, exit_price, pips_gained, evaluation_result, reward_penalty, ai_reasoning, ai_lessons_learned)
                VALUES 
                ('XAU_USD', 'BUY', 2340.00, 2335.00, -50.0, 'INCORRECT', -5.0, 'Fakeout on resistance', 'Avoid BUY entry when price is right under major daily resistance. Whale will stop-hunt long liquidations.')
            """)
        failures = db.get_recent_failures(limit=3)
        print(f"Re-fetched {len(failures)} recent failures:")
        for idx, f in enumerate(failures, 1):
            print(f"[{f.get('type', 'UNKNOWN')}] {f.get('pair')}: Pips: {f.get('pips_gained')}, Lesson: {f.get('ai_lessons_learned')}")
            
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
    print(f"\nLoaded Kaggle URL from SQLite: {url}")
    
    # Build prompt
    prompt = bridge.build_debate_prompt(
        pair="XAU_USD",
        direction="BUY",
        entry=2342.50,
        catalyst="USD CPI MoM prints 0.4% vs 0.2% forecast, hinting at hawkish Fed rate path.",
        past_failures=failures
    )
    
    print("\n--- Generated Prompt Preview (First 500 & Last 500 chars) ---")
    print(prompt[:500])
    print("...\n[PROMPT MIDDLE SECTIONS]\n...")
    print(prompt[-500:])
    
    if not url:
        print("\n[ERROR] Kaggle URL not set in database, skipping inference test.")
        return
        
    print("\nSending request to remote DeepSeek GPU Swarm on Kaggle... (Please wait)")
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
