# =============================================================================
# Quantelos AI Trader — Kaggle AI Bridge (Mini-Debate Inference)
# =============================================================================
# Communicates with the "Otak AI" LLM running on Kaggle Notebooks.
# Implements the Mini-Debate architecture (Bull vs Bear vs Risk Assessor)
# for superior sentiment accuracy over single-prompt models.
# =============================================================================
import json
import logging
import time
import threading
import re
from datetime import datetime, timezone

logger = logging.getLogger("quantelos.kaggle")

try:
    import requests
except ImportError:
    logger.error("requests not installed. Run: pip install requests")
    raise

# ─── Local Fallback (VADER Sentiment) ─────────────────────────────────────────
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False
    logger.warning("VADER not available. Local fallback disabled.")


# ─── MiroFish Swarm System Prompt ──────────────────────────────────────────────
MIROFISH_SWARM_PROMPT = """You are orchestrating a MiroFish-style multi-agent Forex market crowd simulation to evaluate EUR/USD. You must simulate the conversations, reactions, and order placements of the following three simulated market participants:

1. **RETAIL TRADER SWARM (FOMO & Technical Momentum Driven)**:
   - Behavior: High emotional reactivity, follows technical indicator squeezes/breakouts, prone to chasing high-impact news spikes.

2. **INSTITUTIONAL WHALE (Liquidity-Seeking Market Maker)**:
   - Behavior: Counter-trend execution, seeks pools of stop-loss liquidity (often above resistance or below support) to fill large orders.

3. **CENTRAL BANK POLICYMAKER (Macro & Fundamental Strategist)**:
   - Behavior: Highly analytical, hawk vs. dove positioning, evaluates core macroeconomic drivers.

4. **RISK ASSESSOR (Consensus Synthesis)**:
   - Behavior: Weighs the conflicting views of the first three agents. Focuses on position sizing, risk/reward, and probability.

5. **RED TEAM / DEVIL'S ADVOCATE (Self-Correction)**:
   - Behavior: Actively tries to destroy the Risk Assessor's consensus. Identifies failure modes, black swan risks, and "what if we are completely wrong" scenarios.

## News Data Catalyst:
{news_payload}

## Current Technical Context:
- ATR(14): {atr_value} pips (Squeeze: {squeeze_status})
- Bollinger Bandwidth: {bb_bandwidth} (Compressed: {bb_compressed})
- RSI(14): {rsi_value}
- Support: {support} | Resistance: {resistance}

## Required Output Format (STRICT JSON):
```json
{{
  "retail_trader_sentiment": "<Retail crowd reaction: fear, FOMO, or indifference>",
  "institutional_whale_outlook": "<Whale positioning: accumulation, stop-hunting, or distribution>",
  "policymaker_fundamental_view": "<Central bank outlook: hawk, dove, or neutral impact>",
  "risk_assessment": "<Risk Assessor's synthesis of the simulated crowd reactions>",
  "self_correction_red_team": "<Devil's advocate highlighting failure modes and hidden risks>",
  "final_sentiment": "BULLISH_USD" | "BEARISH_USD" | "NEUTRAL",
  "confidence": <float 0.0 to 1.0 representing confirmation strength>,
  "recommended_direction": "BUY" | "SELL" | "HOLD"
}}
```"""


class KaggleBridge:
    """Bridge to Kaggle-hosted LLM inference with local VADER fallback."""

    def __init__(self, db_manager, timeout: int = 15):
        self.db = db_manager
        self.timeout = timeout
        self._kaggle_url = None
        self._last_url_check = 0
        self._url_cache_ttl = 30  # Re-check Kaggle URL every 30s
        self._last_catalyst = "Technical Breakout"
        self._last_direction = "HOLD"
        self._last_pair = ""

        # Live AI Thinking State (thread-safe, polled by dashboard)
        self._thinking_lock = threading.Lock()
        self._thinking_state = {
            "active": False,
            "phase": "idle",           # idle | connecting | thinking | analyzing | complete | truncated | error
            "model": "",
            "started_at": None,
            "tokens_generated": 0,
            "thinking_text": "",       # DeepSeek <think> content (internal reasoning)
            "output_text": "",         # Final visible output
            "current_agent": None,     # retail | whale | policy | risk_assessor | self_correction
            "agents_completed": [],
            "pair": "",
            "direction": "",
            "elapsed_seconds": 0,
            "error": None,
            "decision_json": None        # Gold v2.0: Parsed AI decision for conclusion panel
        }
        
        # Load configuration for Supabase Sync
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        # Fail-fast: a trading daemon must NEVER silently run on an empty/default
        # config. A malformed TOML (e.g. duplicate keys -> TOMLDecodeError) or a
        # missing file is a fatal startup condition, not something to swallow.
        try:
            with open("config/settings.toml", "rb") as f:
                self.cfg = tomllib.load(f)
        except FileNotFoundError as e:
            logger.critical("FATAL: config/settings.toml not found: %s", e)
            raise
        except tomllib.TOMLDecodeError as e:
            logger.critical(
                "FATAL: config/settings.toml is invalid (likely a duplicate key): %s", e
            )
            raise
        # Validate required keys/types so a typo cannot start the engine with
        # default risk parameters.
        self._validate_config(self.cfg)
        
        # Absolute wall-clock cap for a single streaming inference. The per-socket
        # read timeout is NOT sufficient: SSE keep-alive bytes reset it, so a
        # stalled stream could otherwise block the orchestrator indefinitely.
        self.max_inference_seconds = self.cfg.get("kaggle", {}).get("inference_timeout_s", 180)

        self.fallback_model = self.cfg.get("kaggle", {}).get("fallback_model", "none")
        self.num_predict = self.cfg.get("kaggle", {}).get("num_predict", 4096)
        logger.info("KaggleBridge initialized — num_predict: %d | fallback_model: %s", self.num_predict, self.fallback_model)

    @staticmethod
    def _validate_config(cfg: dict) -> None:
        """Validate required config sections/keys and basic types at startup.

        Raises ValueError on the first problem so a misconfiguration fails the
        daemon immediately instead of degrading to unsafe defaults.
        """
        required = {
            ("kaggle", "sync_method"): str,
            ("kaggle", "router_api_key"): str,
            ("kaggle", "target_model"): str,
            ("oanda", "instruments"): list,
        }
        for (section, key), expected_type in required.items():
            value = cfg.get(section, {}).get(key)
            if value is None:
                raise ValueError(
                    f"FATAL: required config '{section}.{key}' is missing in settings.toml"
                )
            if not isinstance(value, expected_type):
                raise ValueError(
                    f"FATAL: config '{section}.{key}' must be of type "
                    f"{expected_type.__name__}, got {type(value).__name__}"
                )
        instruments = cfg.get("oanda", {}).get("instruments", [])
        if not instruments:
            raise ValueError("FATAL: 'oanda.instruments' must contain at least one instrument")

    def sync_kaggle_url(self) -> str | None:
        """Fetch latest Kaggle URL from Supabase and update local database."""
        sync_method = self.cfg.get("kaggle", {}).get("sync_method", "supabase")
        if sync_method == "supabase":
            url = self.cfg.get("kaggle", {}).get("supabase_url")
            key = self.cfg.get("kaggle", {}).get("supabase_key")
            if not url or not key:
                logger.warning("Supabase URL or Key not configured in settings.toml")
                return None
            
            headers = {
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            try:
                # Query the configuration table for the ngrok/cloudflare URL key
                resp = requests.get(
                    f"{url}/rest/v1/quantelos_config?key=eq.kaggle_ngrok_url",
                    headers=headers,
                    timeout=5
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data and len(data) > 0:
                        val = data[0].get("value")
                        if val:
                            self.db.set_config("kaggle_ngrok_url", val)
                            logger.info("Synced Kaggle URL from Supabase: %s", val)
                            return val
                else:
                    logger.error("Supabase returned status code %d: %s", resp.status_code, resp.text)
            except Exception as e:
                logger.error("Failed to sync Kaggle URL from Supabase: %s", e)
        return None

    def _get_kaggle_url(self) -> str | None:
        """Fetch current Kaggle ngrok/cloudflare URL from database (cached)."""
        now = time.time()
        # If cache is valid and we have the URL in memory, return it
        if now - self._last_url_check < self._url_cache_ttl and self._kaggle_url:
            return self._kaggle_url

        sync_method = self.cfg.get("kaggle", {}).get("sync_method", "supabase")
        
        if sync_method == "manual":
            url = self.cfg.get("kaggle", {}).get("supabase_url")
            if url:
                self.db.set_config("kaggle_ngrok_url", url)
        else:
            # Check local DB first
            url = self.db.get_config("kaggle_ngrok_url")
            
            # If local DB is empty or cache expired, sync URL
            if not url or (now - self._last_url_check >= self._url_cache_ttl):
                logger.info("Syncing Kaggle URL from Supabase...")
                synced_url = self.sync_kaggle_url()
                if synced_url:
                    url = synced_url

        self._kaggle_url = url if url else None
        self._last_url_check = now
        return self._kaggle_url

    def build_debate_prompt(self, pair: str, direction: str, entry: float, catalyst: str, past_failures: list[dict] = None) -> str:
        """Construct the MiroFish-inspired multi-agent Forex market crowd simulation prompt with past memory and multi-turn reasoning."""
        self._last_catalyst = catalyst
        self._last_direction = direction
        self._last_pair = pair
        pair_display = pair.replace("_", "/")
        
        # Format past failures if they exist
        failures_context = ""
        if past_failures:
            failures_context = "\n## COGNITIVE MEMORY: PAST FAILURES & LESSONS LEARNED (DO NOT REPEAT):\n"
            for idx, fail in enumerate(past_failures, 1):
                failures_context += f"Mistake #{idx}:\n"
                failures_context += f"  - Context/Pair: {fail.get('pair')}\n"
                failures_context += f"  - Pips Lost: {fail.get('pips_gained'):.1f} pips\n"
                failures_context += f"  - Lesson Learned: {fail.get('ai_lessons_learned')}\n\n"
        
        prompt = f"""You are a Principal Quantitative Strategist orchestrating an elite multi-agent Forex market simulation to evaluate a high-probability technical setup and breaking catalyst for {pair_display}. 
You must simulate the concurrent reasoning, order-flow positioning, and tactical execution of three distinct market entities:

1. **RETAIL TRADER SWARM (Momentum & Emotion Driven)**:
   - Behavior: High emotional reactivity, follows late-stage technical indicator breakouts, prone to FOMO (Fear Of Missing Out).
   - Analysis Focus: Evaluate if retail traders are providing liquidity (chasing the breakout too late) or if they are trapped on the wrong side of the market.

2. **INSTITUTIONAL WHALE (Liquidity-Seeking Tier-1 Bank)**:
   - Behavior: Massive capital deployment, operates on algorithmic liquidity mapping, seeks deep pools of stop-losses.
   - Analysis Focus: Determine if the current technical momentum is a genuine institutional markup/markdown, or a engineered stop-hunt (fakeout) to trap retail traders. Do not assume all breakouts are traps; validate the momentum.

3. **CENTRAL BANK POLICYMAKER (Macro & Fundamental Strategist)**:
   - Behavior: Highly analytical, evaluates long-term sovereign bond yields, inflation, and core macroeconomic drivers.
   - Analysis Focus: Assess the fundamental significance of the catalyst. Does it support the technical direction?

{failures_context}
## Target Trade Context (Under Evaluation):
- Pair: {pair} (Base Asset vs Quote Asset)
- Proposed Direction: {direction}
- Entry Price: {entry}

## Execution Protocol (Institutional Tree of Thought):
IMPORTANT: Think step-by-step through each entity's perspective. 
You MUST wrap your internal reasoning inside <think> and </think> tags. Provide the final JSON output AFTER the closing </think> tag.

- Step 1: Simulate the Retail Swarm's positioning. Are they the trap, or are they following a genuine trend?
- Step 2: Simulate the Institutional Whale's liquidity sweep. Is the Whale accumulating in the Proposed Direction, or setting up a counter-trend sweep?
- Step 3: Simulate the Policymaker's fundamental alignment. Does the macro narrative support the {pair} Proposed Direction?
- Step 4 (Risk Synthesis & Conclusion): The Chief Risk Officer synthesizes the data. If the institutional and macro data validate the Proposed Direction ({direction}), you MUST explicitly output "{direction}" in your recommended_direction. If the data strictly contradicts it, output the opposite or "HOLD". Do not default to "HOLD" unless the environment is purely toxic/random.

## Required Output Format (STRICT JSON at the very end of your response):
```json
{{
  "retail_trader_sentiment": "<Retail crowd reaction synthesis>",
  "institutional_whale_outlook": "<Whale order-flow and accumulation/distribution intent>",
  "policymaker_fundamental_view": "<Macro alignment with the technical direction>",
  "risk_assessment": "<Chief Risk Officer's final synthesis and risk/reward evaluation>",
  "final_sentiment": "BULLISH_BASE_ASSET" | "BEARISH_BASE_ASSET" | "NEUTRAL",
  "confidence": <float 0.0 to 1.0 representing conviction strength>,
  "recommended_direction": "BUY" | "SELL" | "HOLD"
}}
```"""
        return prompt

    def query_llm(self, prompt: str, temperature: float = 0.5, broadcast_ui: bool = True) -> str:
        """Send a generic text prompt to the remote LLM and return the raw text response."""
        url = self._get_kaggle_url()
        if not url:
            logger.warning("Kaggle URL is empty/not configured.")
            return ""

        # 1. Detect API type
        api_type = "flask"
        config_model = self.cfg.get("kaggle", {}).get("target_model", "deepseek-r1:32b")
        target_model = config_model
        
        if "openrouter" in url.lower() or "20128" in url or "/v1" in url:
            api_type = "openai"
        else:
            try:
                tags_resp = requests.get(f"{url}/api/tags", timeout=3)
                if tags_resp.status_code == 200:
                    api_type = "ollama"
                    tags_data = tags_resp.json()
                    models = [m["name"] for m in tags_data.get("models", [])]
                    if models:
                        matched_model = next((m for m in models if config_model in m or m in config_model), None)
                        if matched_model:
                            target_model = matched_model
                        elif "deepseek-r1:32b" in models:
                            target_model = "deepseek-r1:32b"
                        elif "deepseek-r1:8b" in models:
                            target_model = "deepseek-r1:8b"
                        elif "qwen2.5-coder:14b" in models:
                            target_model = "qwen2.5-coder:14b"
                        else:
                            target_model = models[0]
            except Exception:
                pass

        # 2. Query appropriate endpoint with retries
        max_retries = 3
        retry_delay = 15  # Gold v2.0: increased from 2s — Cloudflare tunnels need recovery time
        
        # Gold v2.0: Reset agent pipeline state at start of each LLM query
        self._agent_pipeline_idx = 0
        self._agent_completed = []
        
        for attempt in range(1, max_retries + 1):
            try:
                # If retrying, we might need a fresh URL (in case it got updated on Kaggle)
                if attempt > 1:
                    logger.info("Retrying LLM query (attempt %d/%d)...", attempt, max_retries)
                    url = self._get_kaggle_url()
                    if not url:
                        raise ValueError("Kaggle URL is empty/not configured.")

                if api_type in ["ollama", "openai"]:
                    logger.info("Connecting to %s GPU Swarm backend (%s) with streaming...", api_type.upper(), target_model)
                    self._preserve_last_thinking()
                    self._update_thinking(active=True, phase="connecting", model=target_model,
                                          pair=self._last_pair, direction=self._last_direction,
                                          started_at=time.time(), tokens_generated=0,
                                          thinking_text="", output_text="", error=None,
                                          current_agent=None, agents_completed=[])
                    
                    if api_type == "openai":
                        api_key = self.cfg.get("kaggle", {}).get("router_api_key", "sk-xxx")
                        headers = {
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}"
                        }
                        if "openrouter" in url.lower():
                            headers["HTTP-Referer"] = "http://localhost:3000"
                            headers["X-Title"] = "Quantelos"
                            
                        endpoint = url if "chat/completions" in url else f"{url}/v1/chat/completions"
                        if "openrouter" in url.lower() and "api/" not in url:
                            endpoint = f"{url}/api/v1/chat/completions"
                        # Make sure local 9router works too
                        if "20128" in url and "chat/completions" not in url:
                            endpoint = "http://localhost:20128/v1/chat/completions"

                        resp = requests.post(
                            endpoint,
                            json={
                                "model": target_model,
                                "messages": [{"role": "user", "content": prompt}],
                                "stream": True,
                                "temperature": temperature
                            },
                            headers=headers,
                            stream=True,
                            # (connect timeout, read timeout). The read timeout is
                            # per-chunk; the absolute deadline below caps total time.
                            timeout=(5, min(self.timeout, 30))
                        )
                        resp.raise_for_status()
                        logger.info("OpenAI streaming started — model: %s | endpoint: %s", target_model, endpoint)
                    else:
                        ollama_ctx = self.cfg.get("kaggle", {}).get("ollama_num_ctx", 16384)
                        resp = requests.post(
                            f"{url}/api/generate",
                            json={
                                "model": target_model,
                                "prompt": prompt,
                                "stream": True,
                                "think": True,
                                "options": {
                                    "temperature": temperature,
                                    "num_predict": self.num_predict,
                                    "num_ctx": ollama_ctx
                                }
                            },
                            stream=True,
                            timeout=self.timeout
                        )
                        resp.raise_for_status()
                        logger.info("Ollama streaming started — model: %s | num_ctx: %d | num_predict: %d",
                                    target_model, ollama_ctx, self.num_predict)
                    self._update_thinking(phase="thinking")
                    
                    full_response = []
                    line_count = 0
                    thinking_buffer = ""
                    output_buffer = ""
                    in_think_block = False
                    start_time = time.time()
                    done_reason = None
                    has_thinking_field = False  # Gold v2.0: track if Ollama sends separate 'thinking' field
                    first_chunk_logged = False
                    
                    # Local state for _detect_agent_phase (thread-safe)
                    pipeline_idx = 0
                    completed = []

                    for line in resp.iter_lines():
                        # Absolute wall-clock guard: SSE keep-alive comments reset
                        # the per-socket read timeout, so without this an idle or
                        # half-open stream could hang the orchestrator forever.
                        if time.time() - start_time > self.max_inference_seconds:
                            logger.warning(
                                "LLM stream exceeded max_inference_seconds=%ds — aborting stream.",
                                self.max_inference_seconds,
                            )
                            done_reason = "timeout"
                            break
                        if line:
                            line_count += 1
                            decoded = line.decode('utf-8')
                            logger.debug("Streamed line %d: %s", line_count, decoded[:100])
                            # Handle OpenAI vs Ollama stream formats
                            if api_type == "openai":
                                if decoded.startswith("data: "):
                                    payload = decoded[6:]
                                    if payload.strip() == "[DONE]":
                                        done_reason = "stop"
                                        break
                                    try:
                                        data = json.loads(payload)
                                        choices = data.get("choices", [])
                                        if not choices:
                                            continue
                                        delta = choices[0].get("delta", {})
                                        chunk = delta.get("content", "")
                                        if not chunk:
                                            chunk = delta.get("reasoning", "")
                                            if chunk and not has_thinking_field:
                                                has_thinking_field = True
                                                logger.info("OpenAI 'reasoning' field detected")
                                            thinking_chunk = chunk if has_thinking_field else ""
                                            if has_thinking_field: chunk = ""
                                        else:
                                            thinking_chunk = ""
                                            
                                        finish_reason = choices[0].get("finish_reason")
                                        if finish_reason:
                                            if finish_reason == "length": done_reason = "length"
                                            elif finish_reason == "stop": done_reason = "stop"
                                            else: done_reason = finish_reason
                                    except json.JSONDecodeError:
                                        continue
                                else:
                                    continue
                            else:
                                try:
                                    data = json.loads(decoded)
                                    chunk = data.get("response", "")
                                    if data.get("done", False):
                                        done_reason = data.get("done_reason", "stop")
                                    thinking_chunk = data.get("thinking", "")
                                except json.JSONDecodeError:
                                    continue
                                    
                            if chunk:
                                full_response.append(chunk)

                            # Gold v2.0: Log first non-empty chunk for debugging
                            if not first_chunk_logged and chunk:
                                first_chunk_logged = True
                                logger.info("First response chunk: %s", repr(chunk[:200]))

                            # Gold v2.0: Handling thinking fields
                            if thinking_chunk:
                                if not has_thinking_field and api_type == "ollama":
                                    has_thinking_field = True
                                    logger.info("Ollama 'thinking' field detected — using separate field for reasoning")
                                thinking_buffer += thinking_chunk

                            # Gold v2.0: Only parse inline tags if no separate 'thinking' field
                            if not has_thinking_field:
                                remaining = chunk
                                while remaining:
                                    if in_think_block:
                                        ct = remaining.find("</think>")
                                        if ct >= 0:
                                            thinking_buffer += remaining[:ct]
                                            remaining = remaining[ct + 8:]
                                            in_think_block = False
                                        else:
                                            thinking_buffer += remaining
                                            remaining = ""
                                    else:
                                        ot = remaining.find("<think>")
                                        if ot >= 0:
                                            output_buffer += remaining[:ot]
                                            remaining = remaining[ot + 7:]
                                            in_think_block = True
                                        else:
                                            output_buffer += remaining
                                            remaining = ""
                            else:
                                # Separate 'thinking' field: response IS the output
                                output_buffer += chunk

                            # Detect which agent is being analyzed from the streamed text
                            combined = thinking_buffer + output_buffer
                            current_agent, completed, pipeline_idx = self._detect_agent_phase(combined, pipeline_idx, completed)

                            if broadcast_ui:
                                self._update_thinking(
                                    tokens_generated=line_count,
                                thinking_text=thinking_buffer[-2000:],
                                output_text=output_buffer[-2000:],
                                current_agent=current_agent,
                                agents_completed=completed,
                                elapsed_seconds=round(time.time() - start_time, 1)
                            )

                            if done_reason and api_type == "ollama" and data.get("done", False):
                                break
                            elif done_reason and api_type == "openai" and done_reason in ["stop", "length"]:
                                # Some gateways send finish_reason but never emit the
                                # [DONE] sentinel. Break here so we don't block until
                                # the read timeout fires on an already-finished stream.
                                break

                    # Ensure the streaming socket is always released even if a later
                    # parsing step raises; prevents fd/connection leaks in the daemon.
                    try:
                        resp.close()
                    except Exception:
                        pass

                    # Gold v2.0: Fallback — if output_buffer is empty, extract JSON from full_text
                    full_text = "".join(full_response)
                    if not output_buffer and full_text.strip():
                        # Strip any remaining thinking content from full_text to get output
                        cleaned = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL)
                        if cleaned.strip():
                            output_buffer = cleaned.strip()
                            logger.info("Output recovered from full_text via regex fallback (%d chars)", len(output_buffer))
                        else:
                            # No closing think tag found — treat full_text as output
                            output_buffer = full_text
                            logger.info("Output recovered from full_text as-is (%d chars)", len(output_buffer))

                    # Gold v2.0: Parse decision JSON for dashboard conclusion panel
                    decision_data = None
                    output_for_parse = output_buffer if output_buffer else full_text
                    try:
                        json_start = output_for_parse.find('{')
                        json_end = output_for_parse.rfind('}') + 1
                        if json_start >= 0 and json_end > json_start:
                            decision_data = json.loads(output_for_parse[json_start:json_end])
                            logger.info("Decision parsed for conclusion panel: %s (conf: %.2f)",
                                       decision_data.get('recommended_direction'), decision_data.get('confidence', 0))
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.debug("Could not parse decision JSON (non-critical): %s", e)

                    # Gold v2.0: Fallback — if no JSON parsed, keep last valid decision
                    if decision_data is None:
                        # Try 1: in-memory state
                        try:
                            prev = self._thinking_state.get("decision_json")
                            if prev and isinstance(prev, dict) and prev.get("recommended_direction"):
                                decision_data = prev
                                logger.info("Reusing last valid decision (memory): %s (conf: %.2f)",
                                           prev.get('recommended_direction'), prev.get('confidence', 0))
                        except Exception:
                            pass
                        # Try 2: load from DB
                        if decision_data is None:
                            try:
                                db_row = self.db.get_config("last_complete_thinking")
                                if db_row:
                                    db_state = json.loads(db_row)
                                    db_dj = db_state.get("decision_json")
                                    if db_dj and isinstance(db_dj, dict) and db_dj.get("recommended_direction"):
                                        decision_data = db_dj
                                        logger.info("Reusing last valid decision (DB): %s (conf: %.2f)",
                                                   db_dj.get('recommended_direction'), db_dj.get('confidence', 0))
                            except Exception:
                                pass

                    # Gold v2.0: Truncation detection — distinguish complete vs truncated
                    elapsed = round(time.time() - start_time, 1)
                    if done_reason == "length":
                        logger.warning("⚠️ LLM TRUNCATED: done_reason='length' — context window exhausted! "
                                      "Only %d agents completed. Response is INCOMPLETE.", len(completed))
                        self._update_thinking(
                            force=True,
                            active=False, phase="truncated", error="Context window exhausted — incomplete analysis",
                            thinking_text=thinking_buffer[-2000:],
                            output_text=output_buffer[-2000:],
                            decision_json=decision_data,
                            elapsed_seconds=elapsed
                        )
                    else:
                        self._update_thinking(force=True, active=False, phase="complete",
                                              thinking_text=thinking_buffer[-2000:],
                                              output_text=output_buffer[-2000:],
                                              decision_json=decision_data,
                                              elapsed_seconds=elapsed)
                    logger.info("LLM streaming complete — lines: %d | thinking_chars: %d | output_chars: %d | done_reason: %s | elapsed: %.1fs",
                               line_count, len(thinking_buffer), len(output_buffer), done_reason or "unknown", elapsed)
                    return full_text
                else:
                    logger.info("Connecting to Flask inference backend...")
                    resp = requests.post(
                        f"{url}/inference",
                        json={"prompt": prompt, "max_tokens": self.num_predict},
                        timeout=self.timeout
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("response", "")

            except Exception as e:
                logger.warning("LLM query attempt %d/%d failed: %s", attempt, max_retries, e)
                # Clear URL cache on failure so the next attempt/query forces a sync from Supabase
                self._kaggle_url = None
                self._last_url_check = 0
                
                if attempt == max_retries:
                    logger.error("All LLM query attempts failed. Last error: %s", e)
                    self._update_thinking(active=False, phase="error", error=str(e))
                    return ""
                
                self._update_thinking(phase="connecting", error=f"Attempt {attempt} failed. Retrying...")
                time.sleep(retry_delay)


    def get_decision(self, prompt, broadcast_ui=True):
        """Send prompt to the remote LLM server (Ollama or Flask) and return verdict."""
        raw_text = self.query_llm(prompt, temperature=0.3, broadcast_ui=broadcast_ui)
        if raw_text:
            result = self._parse_llm_response(raw_text)
            if result:
                result["decision"] = result.get("recommended_direction", "HOLD")
                logger.info("Kaggle inference decision: %s (confidence: %.2f)",
                            result["decision"], result["confidence"])
                return result
            else:
                logger.warning("Failed to parse LLM response. Raw text was: %s", raw_text)

        # AI brain unavailable or unparseable. Whatever the fallback setting, never
        # return None here: callers index result["decision"], so a None would crash
        # the orchestrator and bypass the safety block entirely.
        if self.fallback_model != "none":
            return self._local_fallback(self._last_catalyst)

        logger.error("❌ AI brain unavailable and fallback_model='none'. BLOCKING trade — no AI validation.")
        return {
            "final_sentiment": "AI_UNAVAILABLE",
            "confidence": 0.0,
            "recommended_direction": "HOLD",
            "decision": "HOLD",
            "source": "blocked_no_ai",
            "retail_trader_sentiment": "AI Brain unreachable — trade blocked for safety.",
            "institutional_whale_outlook": "",
            "policymaker_fundamental_view": "",
            "risk_assessment": "AI validation required but unavailable.",
        }

    def infer(self, news_payload: str, technical_context: dict) -> dict:
        """Legacy method for direct news + technical inference."""
        pair = self.cfg.get("oanda", {}).get("instruments", ["XAU_USD"])[0]
        prompt = self.build_debate_prompt(
            pair=pair,
            direction="BUY",
            entry=1.0000,
            catalyst=news_payload
        )
        return self.get_decision(prompt)

    def _parse_llm_response(self, raw: str) -> dict | None:
        """Extract JSON from LLM text response, with regex fallback for truncated outputs."""
        # 1. Try standard JSON parsing first
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(raw[start:end])
                required = ["final_sentiment", "confidence", "recommended_direction"]
                if all(k in parsed for k in required):
                    parsed["confidence"] = max(0.0, min(1.0, float(parsed["confidence"])))
                    return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. Regex fallback parser for truncated/cut-off responses
        logger.info("Standard JSON parsing failed/truncated. Attempting robust Regex-based key extraction...")
        try:
            final_sentiment = None
            confidence = None
            rec_dir = None

            # Look for final_sentiment
            sent_match = re.search(r'"final_sentiment"\s*:\s*"([^"]+)"', raw)
            if sent_match:
                final_sentiment = sent_match.group(1).strip()

            # Look for confidence
            conf_match = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
            if conf_match:
                try:
                    confidence = float(conf_match.group(1))
                except ValueError:
                    pass

            # Look for recommended_direction
            dir_match = re.search(r'"recommended_direction"\s*:\s*"([^"]+)"', raw)
            if dir_match:
                rec_dir = dir_match.group(1).strip()

            if final_sentiment and confidence is not None and rec_dir:
                parsed = {
                    "final_sentiment": final_sentiment,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "recommended_direction": rec_dir,
                    "retail_trader_sentiment": "Extracted via Regex Fallback (truncated response)",
                    "institutional_whale_outlook": "Extracted via Regex Fallback",
                    "policymaker_fundamental_view": "Extracted via Regex Fallback",
                    "risk_assessment": "Extracted via Regex Fallback",
                    "source": "LLM Engine (Regex Fallback)"
                }
                logger.info("Successfully recovered decision via Regex extraction: %s (%s)", rec_dir, final_sentiment)
                return parsed
        except Exception as e:
            logger.warning("Regex-based JSON fallback parsing failed: %s", e)

        return None


    def ping_health_check(self) -> bool:
        """Ping the LLM endpoint to check health without doing a full inference."""
        url = self._get_kaggle_url()
        if not url:
            return False
            
        try:
            # We can ping the health endpoint if it's Flask, or just do a generic request
            is_ollama = False
            try:
                ver_resp = requests.get(f"{url}/api/version", timeout=3)
                if ver_resp.status_code == 200:
                    is_ollama = True
            except:
                pass
                
            if is_ollama:
                return True
            else:
                # Flask endpoint
                health_resp = requests.get(f"{url}/health", timeout=3)
                return health_resp.status_code == 200
        except Exception:
            return False

    def _local_fallback(self, news_text: str) -> dict:
        """VADER sentiment analysis as offline fallback — BLOCKED when fallback_model='none'."""
        # Gold v2.0: Hard-block fallback when configured
        if self.fallback_model == "none":
            logger.error("❌ Fallback blocked: fallback_model='none'. Returning HOLD.")
            return {
                "final_sentiment": "AI_UNAVAILABLE",
                "confidence": 0.0,
                "recommended_direction": "HOLD",
                "decision": "HOLD",
                "source": "blocked_no_ai",
            }

        if not VADER_AVAILABLE:
            logger.warning("VADER unavailable. Returning NEUTRAL.")
            return {
                "final_sentiment": "NEUTRAL",
                "confidence": 0.0,
                "recommended_direction": "HOLD",
                "source": "no_model",
            }

        # Determine base/quote currency dynamically from settings
        pair = self.cfg.get("oanda", {}).get("instruments", ["XAU_USD"])[0]
        parts = pair.split("_")
        base_cur = parts[0] if len(parts) > 0 else ""
        quote_cur = parts[1] if len(parts) > 1 else ""

        # Check if the catalyst is a technical label
        is_technical = "Mean Reversion" in news_text or "Technical" in news_text or "Momentum" in news_text or "Scalp" in news_text or "Pullback" in news_text
        if is_technical:
            logger.info("Local fallback: validating technical direction '%s' directly", self._last_direction)
            if quote_cur == "USD":
                sentiment = "BULLISH_USD" if self._last_direction == "SELL" else "BEARISH_USD"
            elif base_cur == "USD":
                sentiment = "BULLISH_USD" if self._last_direction == "BUY" else "BEARISH_USD"
            else:
                sentiment = "NEUTRAL"
            return {
                "retail_trader_sentiment": "Retail crowd is aligned with the technical setup.",
                "institutional_whale_outlook": "Institutional whales are participating in the volume expansion.",
                "policymaker_fundamental_view": "No immediate fundamental barriers to the price level.",
                "risk_assessment": "Technical alignment verified. Proceeding with active strategy.",
                "final_sentiment": sentiment,
                "confidence": 0.50, # Reduced from 0.85 to allow filtering in main loop
                "recommended_direction": self._last_direction,
                "source": "technical_bypass",
            }

        # Gold v2.0: Check fallback configuration before using VADER
        if self.fallback_model == "none":
            logger.error("❌ AI brain unavailable and fallback_model='none'. BLOCKING trade — no AI validation.")
            return {
                "final_sentiment": "AI_UNAVAILABLE",
                "confidence": 0.0,
                "recommended_direction": "HOLD",
                "decision": "HOLD",
                "source": "blocked_no_ai",
            }

        analyzer = SentimentIntensityAnalyzer()
        scores = analyzer.polarity_scores(news_text)
        compound = scores["compound"]

        if compound >= 0.15:
            sentiment = "BULLISH_USD"
            if quote_cur == "USD":
                direction = "SELL"  # Strong USD → EUR/USD, XAU/USD goes down
            elif base_cur == "USD":
                direction = "BUY"   # Strong USD → USD/JPY goes up
            else:
                direction = "SELL"
        elif compound <= -0.15:
            sentiment = "BEARISH_USD"
            if quote_cur == "USD":
                direction = "BUY"   # Weak USD → EUR/USD, XAU/USD goes up
            elif base_cur == "USD":
                direction = "SELL"  # Weak USD → USD/JPY goes down
            else:
                direction = "BUY"
        else:
            sentiment = "NEUTRAL"
            direction = "HOLD"

        logger.info("VADER fallback: %s for %s (compound: %.3f)", sentiment, pair, compound)
        return {
            "final_sentiment": sentiment,
            "confidence": abs(compound),
            "recommended_direction": direction,
            "source": "vader_fallback",
        }

    # ─── Live Thinking State Helpers ───────────────────────────────────────────

    def _preserve_last_thinking(self):
        """Save current thinking state to 'last_complete_thinking' before starting new inference.
        Gold v2.0: Now saves ANY state with content (not just complete/truncated) to prevent
        loss of last good analysis during 524 errors or rapid episode cycling."""
        with self._thinking_lock:
            state = dict(self._thinking_state)
            # Save if there's meaningful content regardless of phase
            if state.get("thinking_text") or state.get("output_text") or state.get("decision_json"):
                if state.get("phase") not in ("connecting", "idle"):
                    try:
                        self.db.set_config("last_complete_thinking", json.dumps(state))
                        logger.debug("Previous thinking state preserved (phase: %s)", state.get("phase"))
                    except Exception:
                        pass

    def _update_thinking(self, force=False, **kwargs):
        """Thread-safe update of thinking state. Persists to DB for cross-process dashboard access."""
        with self._thinking_lock:
            for k, v in kwargs.items():
                if k in self._thinking_state:
                    self._thinking_state[k] = v

            # Debounce DB writes: only persist every 0.8s or on phase transitions
            now = time.time()
            is_phase_change = "phase" in kwargs or "active" in kwargs
            last_write = getattr(self, "_last_thinking_write", 0)
            if force or is_phase_change or (now - last_write) >= 0.8:
                self._last_thinking_write = now
                try:
                    self.db.set_config("ai_thinking_state", json.dumps(self._thinking_state))
                except Exception as e:
                    logger.debug("Thinking state DB write failed (non-critical): %s", e)

    def get_thinking_state(self) -> dict:
        """Return a snapshot of the current AI thinking state for the dashboard."""
        with self._thinking_lock:
            return dict(self._thinking_state)

    def _detect_agent_phase(self, text: str, pipeline_idx: int, completed: list) -> tuple:
        """Detect which agent the LLM is currently analyzing using sequential state machine.
        Once an agent is completed, it never goes back — prevents keyword pollution from
        cross-references in later reasoning phases (e.g., Risk Assessor mentioning 'retail FOMO')."""
        # Ordered pipeline: agents must complete in sequence
        PIPELINE = [
            ("retail", ["retail trader", "retail swarm", "retail crowd", "fomo", "panic-buy", "panic-sell", "retail_trader_sentiment", "retail"]),
            ("whale", ["institutional whale", "whale maker", "liquidity", "stop-hunt", "accumulation", "institutional_whale_outlook", "whale", "institutional"]),
            ("policy", ["policymaker", "central bank", "hawk", "dove", "inflation", "interest rate", "policymaker_fundamental_view", "policy", "macro"]),
            ("risk_assessor", ["risk assess", "synthesis", "consensus", "final verdict", "risk assessment", "risk_assessor"]),
            ("self_correction", ["self-correction", "red team", "failure mode", "devil's advocate", "could fail", "self_correction_red_team", "self correction"]),
        ]

        text_lower = text.lower()
        idx = pipeline_idx

        # Sequential detection: only look for agents we haven't passed yet
        while idx < len(PIPELINE):
            agent_id, keywords = PIPELINE[idx]
            found = False
            for kw in keywords:
                if kw in text_lower:
                    found = True
                    break
            if found:
                if agent_id not in completed:
                    completed.append(agent_id)
                idx += 1  # Move to next agent
            else:
                break  # Current agent not yet found — stop scanning

        # Current agent = the one being analyzed (last completed + 1, or last completed)
        if completed:
            current = completed[-1]
        else:
            current = None

        return current, list(completed), idx
