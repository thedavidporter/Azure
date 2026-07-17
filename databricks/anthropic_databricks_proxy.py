#!/usr/bin/env python3
"""
Anthropic API → Databricks (OpenAI-compatible) proxy for Claude Code.

Usage:
  1. python3 ~/anthropic_databricks_proxy.py
  2. In a new terminal (or add to ~/.bashrc):
       export ANTHROPIC_BASE_URL=http://localhost:8082
       export ANTHROPIC_API_KEY=$(cat ~/.databricks_app_token)
  3. Run claude normally — it will use your work Databricks endpoint.

Stop with Ctrl+C.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

DATABRICKS_HOST  = "https://adb-5757046586469840.0.azuredatabricks.net"
DATABRICKS_MODEL = "databricks-claude-sonnet-5"
ENDPOINT         = f"{DATABRICKS_HOST}/serving-endpoints/{DATABRICKS_MODEL}/invocations"
PORT             = int(os.environ.get("PROXY_PORT", 8082))
MAX_RETRIES      = 5
RETRY_BASE_S     = 2.0   # exponential backoff base (seconds)

app = FastAPI()


def _backoff(attempt: int, retry_after_hdr: str | None) -> float:
    """Return how long to wait before retry attempt (reads Retry-After header or uses 2^n backoff)."""
    if retry_after_hdr:
        try:
            return max(1.0, float(retry_after_hdr))
        except ValueError:
            pass
    return RETRY_BASE_S * (2 ** attempt)


# ── Request translation: Anthropic → OpenAI ───────────────────────────────────

def _flatten_content(content):
    """Return (text_str_or_None, tool_calls_list, tool_results_list) from Anthropic content blocks."""
    if isinstance(content, str):
        return content, [], []
    texts, tool_calls, tool_results = [], [], []
    for block in content:
        t = block.get("type")
        if t == "text":
            texts.append(block.get("text", ""))
        elif t == "tool_use":
            tool_calls.append({
                "id":   block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name":      block["name"],
                    "arguments": json.dumps(block.get("input", {})),
                },
            })
        elif t == "tool_result":
            rc = block.get("content", "")
            if isinstance(rc, list):
                rc = " ".join(b.get("text", "") for b in rc if b.get("type") == "text")
            tool_results.append({
                "role":         "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content":      str(rc),
            })
    return (" ".join(texts) if texts else None), tool_calls, tool_results


def anthropic_to_openai(body: dict) -> dict:
    messages = []

    system = body.get("system")
    if system:
        if isinstance(system, list):
            system = " ".join(b.get("text", "") for b in system if b.get("type") == "text")
        messages.append({"role": "system", "content": system})

    for msg in body.get("messages", []):
        role    = msg["role"]
        content = msg["content"]
        text, tool_calls, tool_results = _flatten_content(content)

        if role == "assistant":
            m = {"role": "assistant", "content": text}
            if tool_calls:
                m["tool_calls"] = tool_calls
            messages.append(m)
        elif role == "user":
            if tool_results:
                messages.extend(tool_results)
            if text:
                messages.append({"role": "user", "content": text})
            elif not tool_results:
                messages.append({"role": "user", "content": ""})

    oai = {
        "model":      DATABRICKS_MODEL,
        "messages":   messages,
        "max_tokens": body.get("max_tokens", 4096),
        "stream":     body.get("stream", False),
    }
    for k in ("temperature", "top_p"):
        if k in body:
            oai[k] = body[k]
    if "stop_sequences" in body:
        oai["stop"] = body["stop_sequences"]

    tools = body.get("tools")
    if tools:
        oai["tools"] = [
            {
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t.get("description", ""),
                    "parameters":  t.get("input_schema", {}),
                },
            }
            for t in tools
        ]
        tc = body.get("tool_choice", {})
        tc_type = tc.get("type") if isinstance(tc, dict) else None
        if tc_type == "any":
            oai["tool_choice"] = "required"
        elif tc_type == "tool":
            oai["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif tc_type == "auto":
            oai["tool_choice"] = "auto"

    return oai


# ── Response translation: OpenAI stream → Anthropic stream ───────────────────

async def stream_translate(http_resp, model: str, msg_id: str):
    log = open("/tmp/proxy_stream.log", "w")
    def emit(s):
        log.write(s)
        log.flush()
        return s

    yield emit(f"data: {json.dumps({'type':'message_start','message':{'id':msg_id,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':0,'output_tokens':1}}})}\n\n")
    yield emit(f"data: {json.dumps({'type':'ping'})}\n\n")

    text_idx      = None
    tool_blocks   = {}   # oai_index → {anthropic_idx, id, name}
    next_idx      = 0
    stop_reason   = "end_turn"

    async for line in http_resp.aiter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except Exception:
            continue

        choices = chunk.get("choices", [])
        if not choices:
            continue
        choice        = choices[0]
        delta         = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # Text delta — skip reasoning/thinking blocks (Databricks returns them as lists)
        text = delta.get("content")
        if text and isinstance(text, str):
            if text_idx is None:
                text_idx = next_idx
                next_idx += 1
                yield emit(f"data: {json.dumps({'type':'content_block_start','index':text_idx,'content_block':{'type':'text','text':''}})}\n\n")
            yield emit(f"data: {json.dumps({'type':'content_block_delta','index':text_idx,'delta':{'type':'text_delta','text':text}})}\n\n")

        # Tool call deltas
        for tc in delta.get("tool_calls", []):
            oi      = tc.get("index", 0)
            tc_id   = tc.get("id")
            fn      = tc.get("function", {})
            tc_name = fn.get("name")
            tc_args = fn.get("arguments", "")

            if tc_id and tc_name:
                # Close text block if open
                if text_idx is not None:
                    yield emit(f"data: {json.dumps({'type':'content_block_stop','index':text_idx})}\n\n")
                    text_idx = None
                bidx = next_idx
                next_idx += 1
                tool_blocks[oi] = {"idx": bidx, "id": tc_id, "name": tc_name}
                yield emit(f"data: {json.dumps({'type':'content_block_start','index':bidx,'content_block':{'type':'tool_use','id':tc_id,'name':tc_name,'input':{}}})}\n\n")

            if tc_args and oi in tool_blocks:
                yield emit(f"data: {json.dumps({'type':'content_block_delta','index':tool_blocks[oi]['idx'],'delta':{'type':'input_json_delta','partial_json':tc_args}})}\n\n")

        if finish_reason:
            stop_reason = {"tool_calls": "tool_use", "length": "max_tokens"}.get(finish_reason, "end_turn")

    # Close any open blocks
    if text_idx is not None:
        yield emit(f"data: {json.dumps({'type':'content_block_stop','index':text_idx})}\n\n")
    for tb in tool_blocks.values():
        yield emit(f"data: {json.dumps({'type':'content_block_stop','index':tb['idx']})}\n\n")

    yield emit(f"data: {json.dumps({'type':'message_delta','delta':{'stop_reason':stop_reason,'stop_sequence':None},'usage':{'output_tokens':0}})}\n\n")
    yield emit(f"data: {json.dumps({'type':'message_stop'})}\n\n")
    log.close()


# ── Response translation: OpenAI non-stream → Anthropic ──────────────────────

def openai_to_anthropic(oai: dict, model: str, msg_id: str) -> dict:
    choice  = oai.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = []

    raw = message.get("content")
    if isinstance(raw, str):
        if raw:
            content.append({"type": "text", "text": raw})
    elif isinstance(raw, list):
        # Databricks returns list of blocks: reasoning + text; keep only text blocks
        for block in raw:
            if block.get("type") == "text":
                content.append({"type": "text", "text": block.get("text", "")})

    for tc in message.get("tool_calls", []):
        fn = tc.get("function", {})
        try:
            inp = json.loads(fn.get("arguments", "{}"))
        except Exception:
            inp = {}
        content.append({
            "type":  "tool_use",
            "id":    tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
            "name":  fn.get("name", ""),
            "input": inp,
        })

    fr = choice.get("finish_reason", "stop")
    stop_reason = {"tool_calls": "tool_use", "length": "max_tokens"}.get(fr, "end_turn")
    usage = oai.get("usage", {})

    return {
        "id":            msg_id,
        "type":          "message",
        "role":          "assistant",
        "content":       content,
        "model":         model,
        "stop_reason":   stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens":  usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── Routes ────────────────────────────────────────────────────────────────────

_TOKEN_FILE = Path.home() / ".databricks_app_token"

def _get_token(request: Request) -> str:
    # Always prefer the token file — lets Claude Code pass any dummy API key
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text().strip()
    for h in ("x-api-key", "authorization"):
        v = request.headers.get(h, "")
        if v:
            return v.removeprefix("Bearer ").strip()
    return os.environ.get("DATABRICKS_TOKEN", "")


@app.post("/v1/messages")
async def messages(request: Request):
    body  = await request.json()
    token = _get_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="No Databricks token found")

    oai_body  = anthropic_to_openai(body)
    is_stream = oai_body.get("stream", False)
    model     = body.get("model", DATABRICKS_MODEL)
    msg_id    = f"msg_{uuid.uuid4().hex[:24]}"
    headers   = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    print(f"[proxy] {model} stream={is_stream} tools={len(oai_body.get('tools',[]))} msgs={len(oai_body.get('messages',[]))}")

    if is_stream:
        async def generate():
            for attempt in range(MAX_RETRIES + 1):
                try:
                    async with httpx.AsyncClient(timeout=300) as client:
                        async with client.stream("POST", ENDPOINT, headers=headers, json=oai_body) as resp:
                            if resp.status_code == 429:
                                err = await resp.aread()
                                if attempt < MAX_RETRIES:
                                    delay = _backoff(attempt, resp.headers.get("retry-after"))
                                    print(f"[proxy] 429 rate-limited — retry {attempt+1}/{MAX_RETRIES} in {delay:.1f}s")
                                    await asyncio.sleep(delay)
                                    continue
                                # Out of retries — surface as readable error
                                msg_id2 = f"msg_{uuid.uuid4().hex[:24]}"
                                yield f"data: {json.dumps({'type':'message_start','message':{'id':msg_id2,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':0,'output_tokens':1}}})}\n\n"
                                yield f"data: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}\n\n"
                                yield f"data: {json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':f'[Rate limited after {MAX_RETRIES} retries] {err.decode()[:300]}'}})}\n\n"
                                yield f"data: {json.dumps({'type':'content_block_stop','index':0})}\n\n"
                                yield f"data: {json.dumps({'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':1}})}\n\n"
                                yield f"data: {json.dumps({'type':'message_stop'})}\n\n"
                                return
                            if resp.status_code != 200:
                                err = await resp.aread()
                                print(f"[proxy] ERROR {resp.status_code}: {err.decode()[:500]}")
                                msg_id2 = f"msg_{uuid.uuid4().hex[:24]}"
                                yield f"data: {json.dumps({'type':'message_start','message':{'id':msg_id2,'type':'message','role':'assistant','content':[],'model':model,'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':0,'output_tokens':1}}})}\n\n"
                                yield f"data: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}\n\n"
                                yield f"data: {json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':f'[Proxy error {resp.status_code}] {err.decode()[:300]}'}})}\n\n"
                                yield f"data: {json.dumps({'type':'content_block_stop','index':0})}\n\n"
                                yield f"data: {json.dumps({'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':1}})}\n\n"
                                yield f"data: {json.dumps({'type':'message_stop'})}\n\n"
                                return
                            async for chunk in stream_translate(resp, model, msg_id):
                                yield chunk
                            return  # success
                except Exception as e:
                    print(f"[proxy] EXCEPTION: {e}")
                    raise
        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        resp = None
        for attempt in range(MAX_RETRIES + 1):
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(ENDPOINT, headers=headers, json=oai_body)
            if resp.status_code == 429 and attempt < MAX_RETRIES:
                delay = _backoff(attempt, resp.headers.get("retry-after"))
                print(f"[proxy] 429 rate-limited — retry {attempt+1}/{MAX_RETRIES} in {delay:.1f}s")
                await asyncio.sleep(delay)
                continue
            break
        if resp.status_code != 200:
            print(f"[proxy] ERROR {resp.status_code}: {resp.text[:500]}")
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        result = openai_to_anthropic(resp.json(), model, msg_id)
        with open("/tmp/proxy_nonstream.log", "w") as f:
            json.dump({"raw": resp.json(), "translated": result}, f, indent=2)
        return JSONResponse(result)


@app.get("/v1/models")
async def list_models():
    return {"data": [{"id": DATABRICKS_MODEL, "object": "model", "owned_by": "databricks"}], "object": "list"}


@app.api_route("/{path:path}", methods=["GET","POST","PUT","DELETE"])
async def catch_all(path: str, request: Request):
    body = await request.body()
    print(f"[proxy] UNHANDLED {request.method} /{path} body={body[:200]}")
    raise HTTPException(status_code=404, detail=f"Not implemented: /{path}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Anthropic → Databricks proxy")
    print(f"  Endpoint : {ENDPOINT}")
    print(f"  Listening: http://localhost:{PORT}")
    print()
    print("In your Claude Code terminal:")
    print(f"  export ANTHROPIC_BASE_URL=http://localhost:{PORT}")
    print(f"  export ANTHROPIC_API_KEY=$(cat ~/.databricks_app_token)")
    print()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
