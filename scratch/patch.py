import re

with open("python_node/kaggle_bridge.py", "r") as f:
    content = f.read()

# 1. Update query_llm signature and broadcast logic
content = content.replace('def query_llm(self, prompt: str, temperature: float = 0.5) -> str:', 'def query_llm(self, prompt: str, temperature: float = 0.5, broadcast_ui: bool = True) -> str:')

content = content.replace('''        with self._thinking_lock:
            self._thinking_state.update({''', '''        if broadcast_ui:
            with self._thinking_lock:
                self._thinking_state.update({''')

content = content.replace('''        with self._thinking_lock:
            self._thinking_state["phase"] = "analyzing"''', '''        if broadcast_ui:
            with self._thinking_lock:
                self._thinking_state["phase"] = "analyzing"''')

content = content.replace('''                            self._update_thinking(
                                tokens_generated=line_count,''', '''                            if broadcast_ui:
                                self._update_thinking(
                                    tokens_generated=line_count,''')

content = content.replace('''                        self._update_thinking(
                            tokens_generated=line_count,
                            thinking_text=thinking_buffer,
                            output_text=output_buffer,
                            current_agent="truncated",
                            agents_completed=completed,
                            elapsed_seconds=elapsed
                        )
                        
                        with self._thinking_lock:
                            self._thinking_state["active"] = False
                            self._thinking_state["phase"] = "truncated"
                            self._thinking_state["decision_json"] = decision_data''', '''                        if broadcast_ui:
                            self._update_thinking(
                                tokens_generated=line_count,
                                thinking_text=thinking_buffer,
                                output_text=output_buffer,
                                current_agent="truncated",
                                agents_completed=completed,
                                elapsed_seconds=elapsed
                            )
                            with self._thinking_lock:
                                self._thinking_state["active"] = False
                                self._thinking_state["phase"] = "truncated"
                                self._thinking_state["decision_json"] = decision_data''')

content = content.replace('''                    self._update_thinking(
                        tokens_generated=line_count,
                        thinking_text=thinking_buffer,
                        output_text=output_buffer,
                        current_agent=None,
                        agents_completed=completed,
                        elapsed_seconds=elapsed
                    )''', '''                    if broadcast_ui:
                        self._update_thinking(
                            tokens_generated=line_count,
                            thinking_text=thinking_buffer,
                            output_text=output_buffer,
                            current_agent=None,
                            agents_completed=completed,
                            elapsed_seconds=elapsed
                        )''')

content = content.replace('''        with self._thinking_lock:
            self._thinking_state["active"] = False
            if full_text:
                self._thinking_state["phase"] = "complete"
                self._thinking_state["decision_json"] = decision_data
            else:
                self._thinking_state["phase"] = "error"''', '''        if broadcast_ui:
            with self._thinking_lock:
                self._thinking_state["active"] = False
                if full_text:
                    self._thinking_state["phase"] = "complete"
                    self._thinking_state["decision_json"] = decision_data
                else:
                    self._thinking_state["phase"] = "error"''')

# 2. Fix PIPELINE
old_pipe = '''        PIPELINE = [
            ("retail", ["retail trader", "retail swarm", "retail crowd", "fomo", "panic-buy", "panic-sell"]),
            ("whale", ["institutional whale", "whale maker", "liquidity", "stop-hunt", "accumulation"]),
            ("policy", ["policymaker", "central bank", "hawk", "dove", "inflation", "interest rate"]),
            ("risk_assessor", ["risk assess", "synthesis", "consensus", "final verdict", "risk assessment"]),
            ("self_correction", ["self-correction", "red team", "failure mode", "devil's advocate", "could fail"]),
        ]'''

new_pipe = '''        PIPELINE = [
            ("retail", ["retail trader", "retail swarm", "retail crowd", "fomo", "panic-buy", "panic-sell", "retail_trader_sentiment", "retail"]),
            ("whale", ["institutional whale", "whale maker", "liquidity", "stop-hunt", "accumulation", "institutional_whale_outlook", "whale", "institutional"]),
            ("policy", ["policymaker", "central bank", "hawk", "dove", "inflation", "interest rate", "policymaker_fundamental_view", "policy", "macro"]),
            ("risk_assessor", ["risk assess", "synthesis", "consensus", "final verdict", "risk assessment", "risk_assessor"]),
            ("self_correction", ["self-correction", "red team", "failure mode", "devil's advocate", "could fail", "self_correction_red_team", "self correction"]),
        ]'''
content = content.replace(old_pipe, new_pipe)

# 3. Update react_post_mortem call
with open("python_node/react_post_mortem.py", "r") as f:
    react_content = f.read()
react_content = react_content.replace('self.ai.query_llm(conversation, temperature=0.6)', 'self.ai.query_llm(conversation, temperature=0.6, broadcast_ui=False)')

with open("python_node/kaggle_bridge.py", "w") as f:
    f.write(content)
with open("python_node/react_post_mortem.py", "w") as f:
    f.write(react_content)

print("Patch applied successfully.")
