import logging
import json
import re

logger = logging.getLogger("quantelos.react_agent")

REACT_SYSTEM_PROMPT = """You are an elite Quantitative Execution Analyst at a Tier-1 Hedge Fund.
Your mandate is to conduct a rigorous post-mortem analysis of a recently failed Forex trade (Stop Loss hit) to extract actionable, systemic lessons for the cognitive memory core.
You operate using the ReAct (Reasoning and Acting) protocol: Thought, Action, Action Input, and Observation.
At the end of your investigation, you must output a Final Conclusion containing a hyper-specific systemic adaptation.

You have access to the following data retrieval tools:
1. get_market_volatility: Retrieves the ATR (Average True Range) and Bollinger bandwidth to classify the market state (ranging, trending, or hyper-volatile).
   Action Input: "none"
2. get_past_mistakes: Retrieves the last 3 failures from the Vector Memory core to identify correlated algorithmic drift or repeating failure patterns.
   Action Input: "none"

Use the exact following format for your inner monologue and tool calls:
Thought: your internal quantitative reasoning about what data to query next
Action: the name of the tool to use (must be: get_market_volatility, or get_past_mistakes)
Action Input: the input to the tool (use "none" if no input required)

Once you have gathered sufficient telemetry, you must synthesize the data and output:
Final Conclusion: [Your deep quantitative breakdown of the execution failure, market microstructure anomalies, and a definitive, one-sentence LESSON_LEARNED].
"""

class ReActPostMortemAgent:
    """MiroFish-inspired ReAct Agent for Deep Trade Analysis."""
    
    def __init__(self, ai_bridge, db_manager, trade_context: dict):
        self.ai = ai_bridge
        self.db = db_manager
        self.trade_context = trade_context
        self.max_iterations = 3

    def _get_market_volatility(self) -> str:
        # Mocking tool output using context
        return f"Volatility Context - ATR: {self.trade_context.get('atr', 'Unknown')}, BB Bandwidth: {self.trade_context.get('bb_bandwidth', 'Unknown')}"

    def _get_past_mistakes(self) -> str:
        try:
            failures = self.db.get_recent_failures(limit=3)
            if not failures:
                return "No past mistakes found in DB."
            res = ""
            for i, f in enumerate(failures):
                res += f"Mistake {i+1}: {f.get('ai_lessons_learned')}\n"
            return res
        except Exception:
            return "Failed to fetch past mistakes."

    def execute_tool(self, action: str, action_input: str) -> str:
        if action == "get_market_volatility":
            return self._get_market_volatility()
        elif action == "get_past_mistakes":
            return self._get_past_mistakes()
        else:
            return f"Error: Tool '{action}' is not valid."

    def run_analysis(self) -> str:
        logger.info("Starting MiroFish-style ReAct Post-Mortem Analysis...")
        
        conversation = REACT_SYSTEM_PROMPT + f"\n\nTrade Context:\n{json.dumps(self.trade_context, indent=2)}\n\nBegin!"
        
        for i in range(self.max_iterations):
            logger.debug(f"ReAct Iteration {i+1}/{self.max_iterations}")
            response = self.ai.query_llm(conversation, temperature=0.6, broadcast_ui=False)
            
            if not response:
                break
                
            conversation += f"\n{response}\n"
            
            # Check for Final Conclusion
            final_match = re.search(r"Final Conclusion:\s*(.*)", response, re.DOTALL | re.IGNORECASE)
            if final_match:
                logger.info("ReAct Agent reached Final Conclusion.")
                return final_match.group(1).strip()
                
            # Check for Tool Call
            action_match = re.search(r"Action:\s*(.*?)\n", response, re.IGNORECASE)
            input_match = re.search(r"Action Input:\s*(.*?)(?:\n|$)", response, re.IGNORECASE)
            
            if action_match:
                action = action_match.group(1).strip()
                action_input = input_match.group(1).strip() if input_match else "none"
                logger.info(f"ReAct Agent calling tool: {action}")
                
                observation = self.execute_tool(action, action_input)
                conversation += f"Observation: {observation}\n"
            else:
                # If no tool is called and no conclusion, force termination
                logger.warning("ReAct Agent output format error. Forcing conclusion.")
                break
                
        # Fallback if loop ends without conclusion
        return "ReAct analysis truncated. Fallback Lesson: Ensure strict risk management and avoid trading during highly unpredictable conditions."
