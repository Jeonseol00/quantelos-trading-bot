# =============================================================================
# Quantelos AI Trader — Kaggle Inference Notebook
# =============================================================================
# Deploy this script on Kaggle Notebooks (Dual T4 GPU environment).
# It hosts an LLM (Qwen-2.5 / Llama-3 / Gemma) and exposes an HTTP
# inference endpoint via ngrok tunnel.
# =============================================================================
# USAGE:
#   1. Upload this file to a Kaggle Notebook
#   2. Set NGROK_TOKEN and SUPABASE_URL/KEY as Kaggle Secrets
#   3. Enable GPU accelerator (T4 x2)
#   4. Run all cells
# =============================================================================

import os
import json
import time
import threading
from datetime import datetime, timezone

# ── Step 1: Install dependencies ──────────────────────────────────────────────
# !pip install -q transformers accelerate flask pyngrok supabase

from flask import Flask, request, jsonify

# ── Step 2: Load LLM Model ───────────────────────────────────────────────────
print("[KAGGLE] Loading LLM model...")

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import torch

    MODEL_NAME = "Qwen/Qwen2.5-Coder-7B-Instruct"  # Or any compatible model
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
    )
    print(f"[KAGGLE] Model loaded: {MODEL_NAME}")
    MODEL_LOADED = True
except Exception as e:
    print(f"[KAGGLE] Model load failed: {e}")
    MODEL_LOADED = False

# ── Step 3: Flask Inference Server ────────────────────────────────────────────
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "alive", "model": MODEL_NAME if MODEL_LOADED else "none",
                    "timestamp": datetime.now(timezone.utc).isoformat()})


@app.route("/inference", methods=["POST"])
def inference():
    if not MODEL_LOADED:
        return jsonify({"error": "Model not loaded"}), 503

    data = request.json
    prompt = data.get("prompt", "")
    max_tokens = data.get("max_tokens", 512)

    if not prompt:
        return jsonify({"error": "Empty prompt"}), 400

    try:
        messages = [
            {"role": "system", "content": "You are an expert Forex market analyst."},
            {"role": "user", "content": prompt},
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=0.3,
                do_sample=True,
                top_p=0.9,
            )

        response_text = tokenizer.decode(outputs[0][inputs.input_ids.shape[-1]:],
                                         skip_special_tokens=True)
        return jsonify({"response": response_text, "model": MODEL_NAME})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Step 4: Ngrok Tunnel & URL Sync ──────────────────────────────────────────
def start_tunnel_and_sync():
    """Start ngrok tunnel and sync URL to Supabase/Gist."""
    try:
        from pyngrok import ngrok

        ngrok_token = os.environ.get("NGROK_TOKEN", "")
        if ngrok_token:
            ngrok.set_auth_token(ngrok_token)

        tunnel = ngrok.connect(5000)
        public_url = tunnel.public_url
        print(f"[KAGGLE] Ngrok tunnel: {public_url}")

        # Sync URL to Supabase
        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_key = os.environ.get("SUPABASE_KEY", "")
        if supabase_url and supabase_key:
            try:
                from supabase import create_client
                sb = create_client(supabase_url, supabase_key)
                sb.table("quantelos_config").upsert({
                    "key": "kaggle_ngrok_url",
                    "value": public_url,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
                print(f"[KAGGLE] URL synced to Supabase.")
            except Exception as e:
                print(f"[KAGGLE] Supabase sync failed: {e}")

    except Exception as e:
        print(f"[KAGGLE] Ngrok failed: {e}")


# ── Step 5: Launch ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sync_thread = threading.Thread(target=start_tunnel_and_sync, daemon=True)
    sync_thread.start()
    time.sleep(2)
    app.run(host="0.0.0.0", port=5000)
