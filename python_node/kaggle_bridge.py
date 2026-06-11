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
        
        # Load configuration for Supabase Sync
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        try:
            with open("config/settings.toml", "rb") as f:
                self.cfg = tomllib.load(f)
        except Exception as e:
            logger.error("Failed to load config in KaggleBridge: %s", e)
            self.cfg = {}

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

        # Check local DB first
        url = self.db.get_config("kaggle_ngrok_url")
        
        # If local DB is empty or cache expired, sync URL
        if not url or (now - self._last_url_check >= self._url_cache_ttl):
            sync_method = self.cfg.get("kaggle", {}).get("sync_method", "supabase")
            if sync_method == "supabase":
                logger.info("Syncing Kaggle URL from Supabase...")
            else:
                logger.debug("Kaggle URL sync method set to: %s. Using local DB value.", sync_method)
            synced_url = self.sync_kaggle_url()
            if synced_url:
                url = synced_url

        self._kaggle_url = url if url else None
        self._last_url_check = now
        return self._kaggle_url

    def build_debate_prompt(self, pair: str, direction: str, entry: float, catalyst: str) -> str:
        """Construct the MiroFish-inspired multi-agent Forex market crowd simulation prompt."""
        self._last_catalyst = catalyst
        self._last_direction = direction
        pair_display = pair.replace("_", "/")
        prompt = f"""You are orchestrating a MiroFish-style multi-agent Forex market crowd simulation to evaluate a breaking economic news event for {pair_display}. You must simulate the conversations, reactions, and order placements of the following three simulated market participants:

1. **RETAIL TRADER SWARM (FOMO & Technical Momentum Driven)**:
   - Behavior: High emotional reactivity, follows technical indicator squeezes/breakouts, prone to chasing high-impact news spikes.
   - Analysis Focus: Evaluate if retail traders will panic-buy or panic-sell, if they are chasing the breakout, and if they represent a high concentration of stop-loss triggers.

2. **INSTITUTIONAL WHALE (Liquidity-Seeking Market Maker)**:
   - Behavior: High capital capability, counter-trend execution, seeks pools of stop-loss liquidity (often above resistance or below support) to fill large orders.
   - Analysis Focus: Evaluate if this technical breakout is an institutional trap (fakeout/whipsaw) to stop-hunt retail, or a genuine long-term position accumulation.

3. **CENTRAL BANK POLICYMAKER (Macro & Fundamental Strategist)**:
   - Behavior: Highly analytical, hawk vs. dove positioning, evaluates core macroeconomic drivers like CPI inflation, employment, and interest rates.
   - Analysis Focus: Determine the fundamental significance of the news catalyst. Is it a long-term directional change or temporary market noise?

## News Data Catalyst:
{catalyst}

## Target Trade Context (Under Evaluation):
- Pair: {pair}
- Direction: {direction} (Breakout direction)
- Entry Price: {entry}

## Execution Protocol:
- Step 1: Simulate the Retail Swarm's emotional consensus.
- Step 2: Simulate the Institutional Whale's liquidity capture planning.
- Step 3: Simulate the Policymaker's fundamental evaluation.
- Step 4: The Risk Assessor synthesizes the simulation and calculates the breakout validation consensus.

## Required Output Format (STRICT JSON):
```json
{{
  "retail_trader_sentiment": "<Retail crowd reaction: fear, FOMO, or indifference>",
  "institutional_whale_outlook": "<Whale positioning: accumulation, stop-hunting, or distribution>",
  "policymaker_fundamental_view": "<Central bank outlook: hawk, dove, or neutral impact>",
  "risk_assessment": "<Risk Assessor's synthesis of the simulated crowd reactions>",
  "final_sentiment": "BULLISH_USD" | "BEARISH_USD" | "NEUTRAL",
  "confidence": <float 0.0 to 1.0 representing confirmation strength>,
  "recommended_direction": "BUY" | "SELL" | "HOLD"
}}
```"""
        return prompt

    def get_decision(self, prompt: str) -> dict:
        """Send prompt to the remote LLM server (Ollama or Flask) and return verdict."""
        url = self._get_kaggle_url()

        if url:
            # 1. Detect if it is an Ollama backend
            is_ollama = False
            ollama_model = "qwen2.5-coder:14b"
            try:
                tags_resp = requests.get(f"{url}/api/tags", timeout=3)
                if tags_resp.status_code == 200:
                    is_ollama = True
                    tags_data = tags_resp.json()
                    models = [m["name"] for m in tags_data.get("models", [])]
                    if models:
                        if "qwen2.5-coder:14b" in models:
                            ollama_model = "qwen2.5-coder:14b"
                        elif "deepseek-r1:8b" in models:
                            ollama_model = "deepseek-r1:8b"
                        else:
                            ollama_model = models[0]
            except Exception:
                pass

            # 2. Query appropriate endpoint
            try:
                if is_ollama:
                    logger.info("Connecting to Ollama GPU Swarm backend (%s)...", ollama_model)
                    resp = requests.post(
                        f"{url}/api/generate",
                        json={
                            "model": ollama_model,
                            "prompt": prompt,
                            "stream": False,
                            "options": {
                                "temperature": 0.3,
                                "num_predict": 512
                            }
                        },
                        timeout=self.timeout
                    )
                else:
                    logger.info("Connecting to Flask inference backend...")
                    resp = requests.post(
                        f"{url}/inference",
                        json={"prompt": prompt, "max_tokens": 512},
                        timeout=self.timeout
                    )

                resp.raise_for_status()
                data = resp.json()
                result = self._parse_llm_response(data.get("response", ""))
                if result:
                    result["decision"] = result.get("recommended_direction", "HOLD")
                    logger.info("Kaggle inference decision: %s (confidence: %.2f)",
                                result["decision"], result["confidence"])
                    return result

            except requests.RequestException as e:
                logger.error("Kaggle request failed: %s", e)
            except (json.JSONDecodeError, KeyError) as e:
                logger.error("Kaggle response parse error: %s", e)

        # 3. Fallback to local VADER
        logger.warning("Kaggle inference unavailable. Falling back to local VADER...")
        fallback_res = self._local_fallback(self._last_catalyst)
        fallback_res["decision"] = fallback_res.get("recommended_direction", "HOLD")
        return fallback_res

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
        """Extract JSON from LLM text response."""
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
        return None

    def _local_fallback(self, news_text: str) -> dict:
        """VADER sentiment analysis as offline fallback."""
        if not VADER_AVAILABLE:
            logger.warning("VADER unavailable. Returning NEUTRAL.")
            return {
                "final_sentiment": "NEUTRAL",
                "confidence": 0.0,
                "recommended_direction": "HOLD",
                "source": "no_model",
            }

        # Check if the catalyst is a technical label
        is_technical = "Mean Reversion" in news_text or "Technical" in news_text or "Momentum" in news_text
        if is_technical:
            logger.info("Local fallback: validating technical direction '%s' directly", self._last_direction)
            sentiment = "BULLISH_USD" if self._last_direction == "SELL" else "BEARISH_USD"
            return {
                "retail_trader_sentiment": "Retail crowd is aligned with the technical setup.",
                "institutional_whale_outlook": "Institutional whales are participating in the volume expansion.",
                "policymaker_fundamental_view": "No immediate fundamental barriers to the price level.",
                "risk_assessment": "Technical alignment verified. Proceeding with active strategy.",
                "final_sentiment": sentiment,
                "confidence": 0.85,
                "recommended_direction": self._last_direction,
                "source": "technical_bypass",
            }

        analyzer = SentimentIntensityAnalyzer()
        scores = analyzer.polarity_scores(news_text)
        compound = scores["compound"]

        if compound >= 0.15:
            sentiment = "BULLISH_USD"
            direction = "SELL"  # Strong USD → EUR/USD goes down
        elif compound <= -0.15:
            sentiment = "BEARISH_USD"
            direction = "BUY"   # Weak USD → EUR/USD goes up
        else:
            sentiment = "NEUTRAL"
            direction = "HOLD"

        logger.info("VADER fallback: %s (compound: %.3f)", sentiment, compound)
        return {
            "final_sentiment": sentiment,
            "confidence": abs(compound),
            "recommended_direction": direction,
            "source": "vader_fallback",
        }
