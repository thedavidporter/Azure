#!/usr/bin/env python3
"""
Thin proxy: translates Anthropic API format -> Databricks serving endpoint -> back to Anthropic.
Run:  python3 ~/databricks_proxy.py
Then set in Claude Code:
  ANTHROPIC_BASE_URL=http://localhost:4000
  ANTHROPIC_API_KEY=sk-databricks-proxy
"""

import json
import subprocess
import time

import requests
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

# ── config ────────────────────────────────────────────────────────────────────

WS_URL      = "adb-5323951998838804.4.azuredatabricks.net"
PROXY_KEY   = "sk-databricks-proxy"
PORT        = 4000

# Map Anthropic model names -> Databricks endpoint names
MODEL_MAP = {
    "claude-opus-4-8":    "databricks-claude-opus-4-8",
    "claude-opus-4-7":    "databricks-claude-opus-4-7",
    "claude-opus-4-6":    "databricks-claude-opus-4-6",
    "claude-sonnet-4-6":  "databricks-claude-sonnet-4-6",
    "claude-sonnet-4-5":  "databricks-claude-sonnet-4-5",
    "claude-sonnet-4":    "databricks-claude-sonnet-4",
    "claude-haiku-4-5":   "databricks-claude-haiku-4-5",
}
DEFAULT_ENDPOINT = "databricks-claude-sonnet-4-6"

# ── token cache (refreshes when within 5 min of expiry) ───────────────────────

_token_cache = {"token": None, "expires_at": 0}

def get_token() -> str:
    if time.time() < _token_cache["expires_at"] - 300:
        return _token_cache["token"]
    out = subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d",
         "--subscription", "57493fde-eff8-432f-8574-4f1281bd2ce3",
         "--query", "[accessToken,expiresOn]", "-o", "json"],
        stderr=subprocess.DEVNULL,
    ).decode()
    data = json.loads(out)
    _token_cache["token"] = data[0]
    from datetime import datetime
    _token_cache["expires_at"] = datetime.fromisoformat(data[1]).timestamp()
    print(f"[proxy] Token refreshed, expires {data[1]}")
    return _token_cache["token"]


def resolve_endpoint(model: str) -> str:
    for key, ep in MODEL_MAP.items():
        if key in model:
            return ep
    return DEFAULT_ENDPOINT


def db_url(endpoint: str) -> str:
    return f"https://{WS_URL}/serving-endpoints/{endpoint}/invocations"


# ── format conversion ─────────────────────────────────────────────────────────

def anthropic_to_oai(body: dict) -> dict:
    messages = list(body.get("messages", []))
    system = body.get("system")
    if system:
        if isinstance(system, list):
            system = " ".join(b.get("text", "") for b in system if b.get("type") == "text")
        messages.insert(0, {"role": "system", "content": system})
    oai = {"messages": messages, "max_tokens": body.get("max_tokens", 1024)}
    if body.get("temperature") is not None:
        oai["temperature"] = body["temperature"]
    if body.get("stop_sequences"):
        oai["stop"] = body["stop_sequences"]
    return oai


def oai_to_anthropic(oai: dict, model: str) -> dict:
    choice = (oai.get("choices") or [{}])[0]
    usage  = oai.get("usage", {})
    text   = (choice.get("message") or {}).get("content", "")
    finish = choice.get("finish_reason", "stop")
    stop_reason = "end_turn" if finish == "stop" else finish
    return {
        "id":            oai.get("id", "msg_proxy"),
        "type":          "message",
        "role":          "assistant",
        "content":       [{"type": "text", "text": text}],
        "model":         model,
        "stop_reason":   stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens":  usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── app ───────────────────────────────────────────────────────────────────────

app = FastAPI()


@app.post("/v1/messages")
async def messages(request: Request):
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {PROXY_KEY}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body     = await request.json()
    model    = body.get("model", "")
    endpoint = resolve_endpoint(model)
    oai_body = anthropic_to_oai(body)
    token    = get_token()
    url      = db_url(endpoint)
    headers  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    print(f"[proxy] {model} -> {endpoint}")

    if body.get("stream"):
        oai_body["stream"] = True
        r = requests.post(url, headers=headers, json=oai_body, stream=True, timeout=120)

        def event_stream():
            msg_id = "msg_proxy"
            yield f"event: message_start\ndata: {json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'usage':{'input_tokens':0,'output_tokens':0}}})}\n\n"
            yield f"event: content_block_start\ndata: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}\n\n"
            for raw in r.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk   = json.loads(data)
                    delta   = (chunk.get("choices") or [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield f"event: content_block_delta\ndata: {json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':content}})}\n\n"
                except Exception:
                    pass
            yield f"event: content_block_stop\ndata: {json.dumps({'type':'content_block_stop','index':0})}\n\n"
            yield f"event: message_delta\ndata: {json.dumps({'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':0}})}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type':'message_stop'})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    r = requests.post(url, headers=headers, json=oai_body, timeout=120)
    if not r.ok:
        return JSONResponse({"error": r.text}, status_code=r.status_code)
    return JSONResponse(oai_to_anthropic(r.json(), model))


if __name__ == "__main__":
    print(f"[proxy] Fetching initial token...")
    get_token()
    print(f"[proxy] Listening on http://localhost:{PORT}")
    print(f"[proxy] Set in Claude Code:")
    print(f"[proxy]   ANTHROPIC_BASE_URL=http://localhost:{PORT}")
    print(f"[proxy]   ANTHROPIC_API_KEY={PROXY_KEY}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
