# Source Generated with Decompyle++
# File: kaggle_bridge.cpython-312.pyc (Python 3.12)

import json
import logging
import time
import threading
import re
from datetime import datetime, timezone
logger = logging.getLogger('quantelos.kaggle')

try:
    import requests
    
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        VADER_AVAILABLE = True
        MIROFISH_SWARM_PROMPT = 'You are orchestrating a MiroFish-style multi-agent Forex market crowd simulation to evaluate EUR/USD. You must simulate the conversations, reactions, and order placements of the following three simulated market participants:\n\n1. **RETAIL TRADER SWARM (FOMO & Technical Momentum Driven)**:\n   - Behavior: High emotional reactivity, follows technical indicator squeezes/breakouts, prone to chasing high-impact news spikes.\n\n2. **INSTITUTIONAL WHALE (Liquidity-Seeking Market Maker)**:\n   - Behavior: Counter-trend execution, seeks pools of stop-loss liquidity (often above resistance or below support) to fill large orders.\n\n3. **CENTRAL BANK POLICYMAKER (Macro & Fundamental Strategist)**:\n   - Behavior: Highly analytical, hawk vs. dove positioning, evaluates core macroeconomic drivers.\n\n4. **RISK ASSESSOR (Consensus Synthesis)**:\n   - Behavior: Weighs the conflicting views of the first three agents. Focuses on position sizing, risk/reward, and probability.\n\n5. **RED TEAM / DEVIL\'S ADVOCATE (Self-Correction)**:\n   - Behavior: Actively tries to destroy the Risk Assessor\'s consensus. Identifies failure modes, black swan risks, and "what if we are completely wrong" scenarios.\n\n## News Data Catalyst:\n{news_payload}\n\n## Current Technical Context:\n- ATR(14): {atr_value} pips (Squeeze: {squeeze_status})\n- Bollinger Bandwidth: {bb_bandwidth} (Compressed: {bb_compressed})\n- RSI(14): {rsi_value}\n- Support: {support} | Resistance: {resistance}\n\n## Required Output Format (STRICT JSON):\n```json\n{{\n  "retail_trader_sentiment": "<Retail crowd reaction: fear, FOMO, or indifference>",\n  "institutional_whale_outlook": "<Whale positioning: accumulation, stop-hunting, or distribution>",\n  "policymaker_fundamental_view": "<Central bank outlook: hawk, dove, or neutral impact>",\n  "risk_assessment": "<Risk Assessor\'s synthesis of the simulated crowd reactions>",\n  "self_correction_red_team": "<Devil\'s advocate highlighting failure modes and hidden risks>",\n  "final_sentiment": "BULLISH_USD" | "BEARISH_USD" | "NEUTRAL",\n  "confidence": <float 0.0 to 1.0 representing confirmation strength>,\n  "recommended_direction": "BUY" | "SELL" | "HOLD"\n}}\n```'
        
        class KaggleBridge:
            '''Bridge to Kaggle-hosted LLM inference with local VADER fallback.'''
            
            def __init__(self = None, db_manager = None, timeout = None):
                self.db = db_manager
                self.timeout = timeout
                self._kaggle_url = None
                self._last_url_check = 0
                self._url_cache_ttl = 30
                self._last_catalyst = 'Technical Breakout'
                self._last_direction = 'HOLD'
                self._thinking_lock = threading.Lock()
                self._thinking_state = {
                    'active': False,
                    'phase': 'idle',
                    'model': '',
                    'started_at': None,
                    'tokens_generated': 0,
                    'thinking_text': '',
                    'output_text': '',
                    'current_agent': None,
                    'agents_completed': [],
                    'pair': '',
                    'direction': '',
                    'elapsed_seconds': 0,
                    'error': None,
                    'decision_json': None }
                
                try:
                    import tomllib
                    
                    try:
                        f = open('config/settings.toml', 'rb')
                        self.cfg = tomllib.load(f)
                        
                        try:
                            None(None, None)
                            self.num_predict = self.cfg.get('kaggle', { }).get('num_predict', 4096)
                            self.fallback_model = self.cfg.get('kaggle', { }).get('fallback_model', 'none')
                            logger.info('KaggleBridge initialized — num_predict: %d | fallback_model: %s', self.num_predict, self.fallback_model)
                            return None
                            except ImportError:
                                import tomli as tomllib
                                continue
                            with None:
                                if not None:
                                    pass
                            
                            try:
                                continue
                            except Exception:
                                e = None
                                logger.error('Failed to load config in KaggleBridge: %s', e)
                                self.cfg = { }
                                e = None
                                del e
                                continue
                                e = None
                                del e





            
            def sync_kaggle_url(self = None):
                '''Fetch latest Kaggle URL from Supabase and update local database.'''
                sync_method = self.cfg.get('kaggle', { }).get('sync_method', 'supabase')
                if sync_method == 'supabase':
                    url = self.cfg.get('kaggle', { }).get('supabase_url')
                    key = self.cfg.get('kaggle', { }).get('supabase_key')
                    if not url or key:
                        logger.warning('Supabase URL or Key not configured in settings.toml')
                        return None
                    headers = {
                        'apikey': key,
                        'Authorization': f'''Bearer {key}''',
                        'Content-Type': 'application/json' }
                    
                    try:
                        resp = requests.get(f'''{url}/rest/v1/quantelos_config?key=eq.kaggle_ngrok_url''', headers = headers, timeout = 5)
                        if resp.status_code == 200:
                            data = resp.json()
                            if data and len(data) > 0:
                                val = data[0].get('value')
                                if val:
                                    self.db.set_config('kaggle_ngrok_url', val)
                                    logger.info('Synced Kaggle URL from Supabase: %s', val)
                                    return val
                                None.error('Supabase returned status code %d: %s', resp.status_code, resp.text)
                        return None
                        return None
                    except Exception:
                        e = None
                        logger.error('Failed to sync Kaggle URL from Supabase: %s', e)
                        e = None
                        del e
                        return None
                        e = None
                        del e


            
            def _get_kaggle_url(self = None):
                '''Fetch current Kaggle ngrok/cloudflare URL from database (cached).'''
                now = time.time()
                if now - self._last_url_check < self._url_cache_ttl and self._kaggle_url:
                    return self._kaggle_url
                url = None.db.get_config('kaggle_ngrok_url')
                if url or now - self._last_url_check >= self._url_cache_ttl:
                    sync_method = self.cfg.get('kaggle', { }).get('sync_method', 'supabase')
                    if sync_method == 'supabase':
                        logger.info('Syncing Kaggle URL from Supabase...')
                    else:
                        logger.debug('Kaggle URL sync method set to: %s. Using local DB value.', sync_method)
                    synced_url = self.sync_kaggle_url()
                    if synced_url:
                        url = synced_url
                self._kaggle_url = url if url else None
                self._last_url_check = now
                return self._kaggle_url

            
            def build_debate_prompt(self, pair = None, direction = None, entry = None, catalyst = (None,), past_failures = ('pair', str, 'direction', str, 'entry', float, 'catalyst', str, 'past_failures', list[dict], 'return', str)):
                '''Construct the MiroFish-inspired multi-agent Forex market crowd simulation prompt with past memory and multi-turn reasoning.'''
                self._last_catalyst = catalyst
                self._last_direction = direction
                pair_display = pair.replace('_', '/')
                failures_context = ''
                if past_failures:
                    failures_context = '\n## COGNITIVE MEMORY: PAST FAILURES & LESSONS LEARNED (DO NOT REPEAT):\n'
                    for idx, fail in enumerate(past_failures, 1):
                        failures_context += f'''Mistake #{idx}:\n'''
                        failures_context += f'''  - Context/Pair: {fail.get('pair')}\n'''
                        failures_context += f'''  - Pips Lost: {fail.get('pips_gained'):.1f} pips\n'''
                        failures_context += f'''  - Lesson Learned: {fail.get('ai_lessons_learned')}\n\n'''
                prompt = f'''You are orchestrating a MiroFish-style multi-agent Forex market crowd simulation to evaluate a breaking economic news event for {pair_display}. You must simulate the conversations, reactions, and order placements of the following three simulated market participants:\n\n1. **RETAIL TRADER SWARM (FOMO & Technical Momentum Driven)**:\n   - Behavior: High emotional reactivity, follows technical indicator squeezes/breakouts, prone to chasing high-impact news spikes.\n   - Analysis Focus: Evaluate if retail traders will panic-buy or panic-sell, if they are chasing the breakout, and if they represent a high concentration of stop-loss triggers.\n\n2. **INSTITUTIONAL WHALE (Liquidity-Seeking Market Maker)**:\n   - Behavior: High capital capability, counter-trend execution, seeks pools of stop-loss liquidity (often above resistance or below support) to fill large orders.\n   - Analysis Focus: Evaluate if this technical breakout is an institutional trap (fakeout/whipsaw) to stop-hunt retail, or a genuine long-term position accumulation.\n\n3. **CENTRAL BANK POLICYMAKER (Macro & Fundamental Strategist)**:\n   - Behavior: Highly analytical, hawk vs. dove positioning, evaluates core macroeconomic drivers like CPI inflation, employment, and interest rates.\n   - Analysis Focus: Determine the fundamental significance of the news catalyst. Is it a long-term directional change or temporary market noise?\n\n{failures_context}\n\n## Target Trade Context (Under Evaluation):\n- Pair: {pair}\n- Direction: {direction} (Breakout direction)\n- Entry Price: {entry}\n\n## Execution Protocol (Tree of Thought & Multi-Turn Debate):\nIMPORTANT: Think step-by-step through each agent\'s perspective before providing your final JSON output.\nUse your internal reasoning to thoroughly analyze each market participant\'s reaction.\nYou MUST wrap your internal reasoning inside <think> and </think> tags. Show your full chain-of-thought analysis inside these tags, then provide the final JSON output AFTER the closing </think> tag.\n- Step 1: Simulate the Retail Swarm\'s emotional consensus. Explain their reasoning — are they chasing FOMO, panicking, or indifferent? What technical signals are they reacting to?\n- Step 2: Simulate the Institutional Whale\'s liquidity capture planning. Analyze if this technical setup is a stop-hunt trap (fakeout/whipsaw) or genuine accumulation. What order flow patterns would the Whale exploit?\n- Step 3: Simulate the Policymaker\'s fundamental evaluation. Assess whether the catalyst represents a long-term directional shift or temporary market noise. What macro data supports the view?\n- Step 4 (Risk Assessment): The Risk Assessor synthesizes the debate, reviews potential failure modes, calculates the final consensus, and assigns a confidence level.\n- Step 5 (Red Teaming / Self-Correction): Conduct a mock debate. Let the Whale challenge the Retail trader\'s bias. Check lessons from Past Failures to see if we are walking into a similar trap. Actively try to destroy the Risk Assessor\'s consensus. What counter-arguments exist?\n\n\n## Required Output Format (STRICT JSON at the very end of your response):\n```json\n{{\n  "retail_trader_sentiment": "<Retail crowd reaction: fear, FOMO, or indifference>",\n  "institutional_whale_outlook": "<Whale positioning: accumulation, stop-hunting, or distribution>",\n  "policymaker_fundamental_view": "<Central bank outlook: hawk, dove, or neutral impact>",\n  "risk_assessment": "<Risk Assessor\'s synthesis of the simulated crowd reactions>",\n  "final_sentiment": "BULLISH_USD" | "BEARISH_USD" | "NEUTRAL",\n  "confidence": <float 0.0 to 1.0 representing confirmation strength>,\n  "recommended_direction": "BUY" | "SELL" | "HOLD"\n}}\n```'''
                return prompt

            
            def query_llm(self = None, prompt = None, temperature = None):
                '''Send a generic text prompt to the remote LLM and return the raw text response.'''
                pass
            # WARNING: Decompyle incomplete

            
            def get_decision(self = None, prompt = None):
                '''Send prompt to the remote LLM server (Ollama or Flask) and return verdict.'''
                raw_text = self.query_llm(prompt, temperature = 0.3)
                if raw_text:
                    result = self._parse_llm_response(raw_text)
                    if result:
                        result['decision'] = result.get('recommended_direction', 'HOLD')
                        logger.info('Kaggle inference decision: %s (confidence: %.2f)', result['decision'], result['confidence'])
                        return result
                    None.warning('Failed to parse LLM response. Raw text was: %s', raw_text)
                if self.fallback_model == 'none':
                    logger.error("❌ AI brain unavailable and fallback_model='none'. BLOCKING trade — no AI validation.")
                    return {
                        'final_sentiment': 'AI_UNAVAILABLE',
                        'confidence': 0,
                        'recommended_direction': 'HOLD',
                        'decision': 'HOLD',
                        'source': 'blocked_no_ai',
                        'retail_trader_sentiment': 'AI Brain unreachable — trade blocked for safety.',
                        'institutional_whale_outlook': '',
                        'policymaker_fundamental_view': '',
                        'risk_assessment': 'AI validation required but unavailable.' }

            
            def infer(self = None, news_payload = None, technical_context = None):
                '''Legacy method for direct news + technical inference.'''
                pair = self.cfg.get('oanda', { }).get('instruments', [
                    'XAU_USD'])[0]
                prompt = self.build_debate_prompt(pair = pair, direction = 'BUY', entry = 1, catalyst = news_payload)
                return self.get_decision(prompt)

            
            def _parse_llm_response(self = None, raw = None):
                '''Extract JSON from LLM text response, with regex fallback for truncated outputs.'''
                pass
            # WARNING: Decompyle incomplete

            
            def _local_fallback(self = None, news_text = None):
                """VADER sentiment analysis as offline fallback — BLOCKED when fallback_model='none'."""
                if self.fallback_model == 'none':
                    logger.error("❌ Fallback blocked: fallback_model='none'. Returning HOLD.")
                    return {
                        'final_sentiment': 'AI_UNAVAILABLE',
                        'confidence': 0,
                        'recommended_direction': 'HOLD',
                        'decision': 'HOLD',
                        'source': 'blocked_no_ai' }
                if not None:
                    logger.warning('VADER unavailable. Returning NEUTRAL.')
                    return {
                        'final_sentiment': 'NEUTRAL',
                        'confidence': 0,
                        'recommended_direction': 'HOLD',
                        'source': 'no_model' }
                pair = None.cfg.get('oanda', { }).get('instruments', [
                    'XAU_USD'])[0]
                parts = pair.split('_')
                base_cur = parts[0] if len(parts) > 0 else ''
                quote_cur = parts[1] if len(parts) > 1 else ''
                if not 'Mean Reversion' in news_text:
                    'Mean Reversion' in news_text
                    if not 'Technical' in news_text:
                        'Technical' in news_text
                        if not 'Momentum' in news_text:
                            'Momentum' in news_text
                            if not 'Scalp' in news_text:
                                'Scalp' in news_text
                is_technical = 'Pullback' in news_text
                if is_technical:
                    logger.info("Local fallback: validating technical direction '%s' directly", self._last_direction)
                    if quote_cur == 'USD':
                        sentiment = 'BULLISH_USD' if self._last_direction == 'SELL' else 'BEARISH_USD'
                    elif base_cur == 'USD':
                        sentiment = 'BULLISH_USD' if self._last_direction == 'BUY' else 'BEARISH_USD'
                    else:
                        sentiment = 'NEUTRAL'
                    return {
                        'retail_trader_sentiment': 'Retail crowd is aligned with the technical setup.',
                        'institutional_whale_outlook': 'Institutional whales are participating in the volume expansion.',
                        'policymaker_fundamental_view': 'No immediate fundamental barriers to the price level.',
                        'risk_assessment': 'Technical alignment verified. Proceeding with active strategy.',
                        'final_sentiment': sentiment,
                        'confidence': 0.5,
                        'recommended_direction': self._last_direction,
                        'source': 'technical_bypass' }
                if None.fallback_model == 'none':
                    logger.error("❌ AI brain unavailable and fallback_model='none'. BLOCKING trade — no AI validation.")
                    return {
                        'final_sentiment': 'AI_UNAVAILABLE',
                        'confidence': 0,
                        'recommended_direction': 'HOLD',
                        'decision': 'HOLD',
                        'source': 'blocked_no_ai' }
                analyzer = None()
                scores = analyzer.polarity_scores(news_text)
                compound = scores['compound']
                if compound >= 0.15:
                    sentiment = 'BULLISH_USD'
                    if quote_cur == 'USD':
                        direction = 'SELL'
                    elif base_cur == 'USD':
                        direction = 'BUY'
                    else:
                        direction = 'SELL'
                elif compound <= -0.15:
                    sentiment = 'BEARISH_USD'
                    if quote_cur == 'USD':
                        direction = 'BUY'
                    elif base_cur == 'USD':
                        direction = 'SELL'
                    else:
                        direction = 'BUY'
                else:
                    sentiment = 'NEUTRAL'
                    direction = 'HOLD'
                logger.info('VADER fallback: %s for %s (compound: %.3f)', sentiment, pair, compound)
                return {
                    'final_sentiment': sentiment,
                    'confidence': abs(compound),
                    'recommended_direction': direction,
                    'source': 'vader_fallback' }

            
            def _preserve_last_thinking(self):
                """Save current thinking state to 'last_complete_thinking' before starting new inference.
        Gold v2.0: Now saves ANY state with content (not just complete/truncated) to prevent
        loss of last good analysis during 524 errors or rapid episode cycling."""
                self._thinking_lock
                state = dict(self._thinking_state)
                if (state.get('thinking_text') and state.get('output_text') or state.get('decision_json')) and state.get('phase') not in ('connecting', 'idle'):
                    self.db.set_config('last_complete_thinking', json.dumps(state))
                    logger.debug('Previous thinking state preserved (phase: %s)', state.get('phase'))
                None(None, None)
                return None
                except Exception:
                    continue
                with None:
                    if not None:
                        pass

            
            def _update_thinking(self, force = (False,), **kwargs):
                '''Thread-safe update of thinking state. Persists to DB for cross-process dashboard access.'''
                self._thinking_lock
                for k, v in kwargs.items():
                    if not k in self._thinking_state:
                        continue
                    self._thinking_state[k] = v
                now = time.time()
                if not 'phase' in kwargs:
                    'phase' in kwargs
                is_phase_change = 'active' in kwargs
                last_write = getattr(self, '_last_thinking_write', 0)
                if force and is_phase_change or now - last_write >= 0.8:
                    self._last_thinking_write = now
                    self.db.set_config('ai_thinking_state', json.dumps(self._thinking_state))
                None(None, None)
                return None
                except Exception:
                    e = None
                    logger.debug('Thinking state DB write failed (non-critical): %s', e)
                    e = None
                    del e
                    continue
                    e = None
                    del e
                with None:
                    if not None:
                        pass

            
            def get_thinking_state(self = None):
                '''Return a snapshot of the current AI thinking state for the dashboard.'''
                self._thinking_lock
                None(None, None)
                return 
                with None:
                    if not None, dict(self._thinking_state):
                        pass

            
            def _detect_agent_phase(self = None, text = None):
                """Detect which agent the LLM is currently analyzing using sequential state machine.
        Once an agent is completed, it never goes back — prevents keyword pollution from
        cross-references in later reasoning phases (e.g., Risk Assessor mentioning 'retail FOMO')."""
                PIPELINE = [
                    ('retail', [
                        'retail trader',
                        'retail swarm',
                        'retail crowd',
                        'fomo',
                        'panic-buy',
                        'panic-sell',
                        'retail_trader_sentiment',
                        'retail']),
                    ('whale', [
                        'institutional whale',
                        'whale maker',
                        'liquidity',
                        'stop-hunt',
                        'accumulation',
                        'institutional_whale_outlook',
                        'whale',
                        'institutional']),
                    ('policy', [
                        'policymaker',
                        'central bank',
                        'hawk',
                        'dove',
                        'inflation',
                        'interest rate',
                        'policymaker_fundamental_view',
                        'policy',
                        'macro']),
                    ('risk_assessor', [
                        'risk assess',
                        'synthesis',
                        'consensus',
                        'final verdict',
                        'risk assessment',
                        'risk_assessor']),
                    ('self_correction', [
                        'self-correction',
                        'red team',
                        'failure mode',
                        "devil's advocate",
                        'could fail',
                        'self_correction_red_team',
                        'self correction'])]
                text_lower = text.lower()
                if not hasattr(self, '_agent_pipeline_idx'):
                    self._agent_pipeline_idx = 0
                    self._agent_completed = []
                idx = self._agent_pipeline_idx
                if idx < len(PIPELINE):
                    (agent_id, keywords) = PIPELINE[idx]
                    found = False
                    for kw in keywords:
                        if not kw in text_lower:
                            continue
                        found = True
                        keywords
                    if found:
                        if agent_id not in self._agent_completed:
                            self._agent_completed.append(agent_id)
                        idx += 1
                    
                elif idx < len(PIPELINE):
                    continue
                self._agent_pipeline_idx = idx
                if self._agent_completed:
                    current = self._agent_completed[-1]
                else:
                    current = None
                return (current, list(self._agent_completed))


        return None
        except ImportError:
            logger.error('requests not installed. Run: pip install requests')
            raise 
    except ImportError:
        VADER_AVAILABLE = False
        logger.warning('VADER not available. Local fallback disabled.')
        continue


