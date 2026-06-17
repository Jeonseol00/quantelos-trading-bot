import re

with open("python_node/kaggle_bridge.py", "r") as f:
    content = f.read()

# Fix 1: Step 4/5 order
old_steps = """- Step 4 (Red Teaming / Self-Correction): Conduct a mock debate. Let the Whale challenge the Retail trader's bias. Check lessons from Past Failures to see if we are walking into a similar trap. What counter-arguments exist?
- Step 5 (Risk Assessment): The Risk Assessor synthesizes the debate, reviews potential failure modes, calculates the final consensus, and assigns a confidence level."""
new_steps = """- Step 4 (Risk Assessment): The Risk Assessor synthesizes the debate, reviews potential failure modes, calculates the final consensus, and assigns a confidence level.
- Step 5 (Red Teaming / Self-Correction): Conduct a mock debate. Let the Whale challenge the Retail trader's bias. Check lessons from Past Failures to see if we are walking into a similar trap. Actively try to destroy the Risk Assessor's consensus. What counter-arguments exist?"""
if old_steps in content:
    content = content.replace(old_steps, new_steps)

# Fix 2: PIPELINE keywords
old_pipe = """        PIPELINE = [
            ("retail", ["retail trader", "retail swarm", "retail crowd", "fomo", "panic-buy", "panic-sell"]),
            ("whale", ["institutional whale", "whale maker", "liquidity", "stop-hunt", "accumulation"]),
            ("policy", ["policymaker", "central bank", "hawk", "dove", "inflation", "interest rate"]),
            ("risk_assessor", ["risk assess", "synthesis", "consensus", "final verdict", "risk assessment"]),
            ("self_correction", ["self-correction", "red team", "failure mode", "devil's advocate", "could fail"]),
        ]"""
new_pipe = """        PIPELINE = [
            ("retail", ["retail trader", "retail swarm", "retail crowd", "fomo", "panic-buy", "panic-sell", "retail_trader_sentiment", "retail"]),
            ("whale", ["institutional whale", "whale maker", "liquidity", "stop-hunt", "accumulation", "institutional_whale_outlook", "whale", "institutional"]),
            ("policy", ["policymaker", "central bank", "hawk", "dove", "inflation", "interest rate", "policymaker_fundamental_view", "policy", "macro"]),
            ("risk_assessor", ["risk assess", "synthesis", "consensus", "final verdict", "risk assessment", "risk_assessor"]),
            ("self_correction", ["self-correction", "red team", "failure mode", "devil's advocate", "could fail", "self_correction_red_team", "self correction"]),
        ]"""
if old_pipe in content:
    content = content.replace(old_pipe, new_pipe)

# Fix 3: query_llm broadcast logic
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

with open("python_node/kaggle_bridge.py", "w") as f:
    f.write(content)

with open("python_node/react_post_mortem.py", "r") as f:
    react_content = f.read()

if 'broadcast_ui=False' not in react_content:
    react_content = react_content.replace('self.ai.query_llm(conversation, temperature=0.6)', 'self.ai.query_llm(conversation, temperature=0.6, broadcast_ui=False)')
    with open("python_node/react_post_mortem.py", "w") as f:
        f.write(react_content)

print("Fixes applied successfully to recovered 777-line file.")
