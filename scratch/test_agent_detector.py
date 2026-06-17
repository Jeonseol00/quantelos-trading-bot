import sys
sys.path.append('python_node')
from kaggle_bridge import KaggleBridge

class DummyDB: pass
kb = KaggleBridge(DummyDB())

text = """
<think>
First, let's analyze the retail crowd sentiment.
Then, the institutional whales are hunting.
Next, the policymaker decides.
Then risk assessment is synthesized.
Finally, self correction red team.
</think>
{"retail_trader_sentiment": "panic", "institutional_whale_outlook": "trap"}
"""

# Let's simulate streaming chunks
buf = ""
for word in text.split(' '):
    buf += word + " "
    current_agent, completed = kb._detect_agent_phase(buf)

print(f"Current Agent: {current_agent}")
print(f"Completed: {completed}")
