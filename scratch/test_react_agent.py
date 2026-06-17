import logging
import sys
import os

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

sys.path.append("/home/titiw/Downloads/Forex/quantelos/python_node")
from db_manager import DatabaseManager
from kaggle_bridge import KaggleBridge
from react_post_mortem import ReActPostMortemAgent

def run_mock_failure():
    print("🚀 Triggering Mock Trade Failure for ReAct Post-Mortem Testing...")
    
    db = DatabaseManager(db_path="/home/titiw/Downloads/Forex/quantelos/data/quantelos.db")
    ai = KaggleBridge(db)
    
    mock_context = {
        "instrument": "XAU_USD",
        "direction": "BUY",
        "outcome": "INCORRECT",
        "pips_gained": -150.5,
        "ai_reasoning": "Retail traders were buying the breakout, Whale was accumulating, Policymaker was Dovish.",
        "atr": 45.2,
        "bb_bandwidth": 0.003
    }
    
    agent = ReActPostMortemAgent(ai, db, mock_context)
    result = agent.run_analysis()
    
    print("\n" + "="*50)
    print("📊 REACT AGENT FINAL REPORT:")
    print("="*50)
    print(result)
    print("="*50)

if __name__ == "__main__":
    run_mock_failure()
