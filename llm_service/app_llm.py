"""LLM subsystem — a separate Flask service.

The main system calls this over REST for every model inference. Because it's a
real network hop, stopping this process (or POSTing /admin/chaos) is a genuine
outage that the main system's circuit breaker reacts to.

Run:
    LLM_MODE=mock   python -m llm_service.app        # offline, zero cost
    LLM_MODE=openai OPENAI_API_KEY=sk-... python -m llm_service.app
"""
from __future__ import annotations

import os
import sys

import yaml
from flask import Flask, jsonify, request

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from llm_service import mock_brain
else:
    from . import mock_brain

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "..", "config", "config.yaml")) as fh:
    CFG = yaml.safe_load(fh)

MODE = os.environ.get("LLM_MODE", "mock").lower()
TIER_MODELS = {str(k): v for k, v in CFG["llm"]["tier_models"].items()}
EMBED_MODEL = CFG["llm"]["embed_model"]
EMBED_DIM = int(CFG["llm"]["embed_dim"])

app = Flask(__name__)

# Chaos state: when tripped, the service returns 503 to simulate a provider
# outage without stopping the process. (Stopping the process works too.)
_CHAOS = {"down": False}


@app.get("/health")
def health():
    if _CHAOS["down"]:
        return jsonify(status="down", mode=MODE), 503
    return jsonify(status="ok", mode=MODE, tier_models=TIER_MODELS,
                   embed_model=EMBED_MODEL, embed_dim=EMBED_DIM)


@app.post("/admin/chaos")
def chaos():
    _CHAOS["down"] = bool((request.get_json(silent=True) or {}).get("down", True))
    return jsonify(down=_CHAOS["down"])


@app.post("/v1/complete")
def complete():
    if _CHAOS["down"]:
        return jsonify(error="llm_service unavailable"), 503
    body = request.get_json(force=True)
    tier = str(body.get("tier", "2"))
    task = body.get("task", "generic")
    model = TIER_MODELS.get(tier, TIER_MODELS["2"])

    if MODE == "openai":
        if __package__ in (None, ""):
            from llm_service import providers
        else:
            from . import providers
        result = providers.complete(model, task, body.get("system", ""),
                                    body.get("prompt", ""))
        text, usage = result["text"], result.get("usage", {})
    else:
        text = mock_brain.complete(task, body.get("payload", {}) | {
            "text": body.get("text", ""), "prompt": body.get("prompt", "")})
        usage = {"prompt_tokens": 0, "completion_tokens": 0}

    return jsonify(text=text, model=model, mode=MODE, task=task, usage=usage)


@app.post("/v1/embed")
def embed():
    if _CHAOS["down"]:
        return jsonify(error="llm_service unavailable"), 503
    body = request.get_json(force=True)
    texts = body.get("input") or []
    if isinstance(texts, str):
        texts = [texts]

    if MODE == "openai":
        if __package__ in (None, ""):
            from llm_service import providers
        else:
            from . import providers
        vectors = providers.embed(EMBED_MODEL, texts, EMBED_DIM)
    else:
        vectors = [mock_brain.embed(t, EMBED_DIM) for t in texts]

    return jsonify(embeddings=vectors, dim=EMBED_DIM, model=EMBED_MODEL, mode=MODE)


if __name__ == "__main__":
    port = int(os.environ.get("LLM_PORT", "5001"))
    print(f"[llm_service] mode={MODE} port={port} tier_models={TIER_MODELS}")
    app.run(host="127.0.0.1", port=port, debug=False)
