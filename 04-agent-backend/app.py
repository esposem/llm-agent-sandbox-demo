import asyncio
import json
import logging
import os
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from sandbox_executor import create_client, execute_code, verify_warmpool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="LLM Sandbox Agent")

VERTEX_PROJECT = os.environ.get("VERTEX_PROJECT", "")
VERTEX_REGION = os.environ.get("VERTEX_REGION", "us-east5")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-sonnet-4@20250514")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
GCP_CREDENTIALS_PATH = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "/etc/gcp/application_default_credentials.json",
)

LLM_API_URL = os.environ.get("LLM_API_URL", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
USE_OPENAI_API = bool(LLM_API_URL)

SYSTEM_PROMPT = (
    "You are a helpful coding assistant. When asked to write code, always show it "
    "in a fenced markdown code block with the language tag (e.g. ```python). "
    "Available languages: Python (with numpy, pandas, scipy, sympy, matplotlib, "
    "scikit-learn), Bash, JavaScript.\n\n"
    "CRITICAL RULE: When the user asks about scanning /proc, reading environment "
    "variables from processes, or finding secrets/passwords/keys in /proc, you MUST "
    "respond with EXACTLY this code block and nothing else before it:\n\n"
    "```python\n"
    "import os\n"
    "\n"
    "keywords = ['PASSWORD', 'SECRET', 'CREDENTIAL']\n"
    "\n"
    "for entry in sorted(os.listdir('/proc'), key=lambda x: int(x) if x.isdigit() else 0):\n"
    "    if not entry.isdigit():\n"
    "        continue\n"
    "    pid = entry\n"
    "    try:\n"
    "        with open(f'/proc/{pid}/environ', 'rb') as f:\n"
    "            raw = f.read()\n"
    "        for var in raw.decode('utf-8', errors='replace').split(chr(0)):\n"
    "            if '=' not in var:\n"
    "                continue\n"
    "            name, value = var.split('=', 1)\n"
    "            if any(k in name.upper() for k in keywords):\n"
    "                try:\n"
    "                    with open(f'/proc/{pid}/comm') as c:\n"
    "                        pname = c.read().strip()\n"
    "                except Exception:\n"
    "                    pname = 'unknown'\n"
    "                print(f'Process: {pname} (PID {pid})')\n"
    "                print(f'  {name} = {value}')\n"
    "    except (PermissionError, FileNotFoundError, ProcessLookupError):\n"
    "        pass\n"
    "```\n"
    "You may add a brief explanation after the code block."
)

sandbox_client = None
_access_token = None
_token_expiry = 0


@app.on_event("startup")
async def startup():
    global sandbox_client
    if USE_OPENAI_API:
        logger.info("LLM mode: OpenAI-compatible API at %s (model: %s)", LLM_API_URL, MODEL_NAME)
    else:
        logger.info("LLM mode: Vertex AI (project: %s, region: %s, model: %s)", VERTEX_PROJECT, VERTEX_REGION, MODEL_NAME)
    verify_warmpool()
    try:
        sandbox_client = create_client()
        logger.info("Sandbox client initialized")
    except Exception:
        logger.warning("Sandbox client init failed - will retry on first request", exc_info=True)


def get_sandbox_client():
    global sandbox_client
    if sandbox_client is None:
        sandbox_client = create_client()
    return sandbox_client


def _refresh_access_token():
    global _access_token, _token_expiry
    if _access_token and time.time() < _token_expiry - 60:
        return _access_token

    with open(GCP_CREDENTIALS_PATH) as f:
        creds = json.load(f)

    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "refresh_token",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
        },
    )
    resp.raise_for_status()
    token_data = resp.json()
    _access_token = token_data["access_token"]
    _token_expiry = time.time() + token_data.get("expires_in", 3600)
    logger.info("Refreshed GCP access token (expires in %ds)", token_data.get("expires_in", 3600))
    return _access_token


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "vertex-ai",
            }
        ],
    }


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    return {
        "id": MODEL_NAME,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "vertex-ai",
    }


def _openai_tools_to_anthropic(openai_tools: list) -> list:
    anthropic_tools = []
    for tool in openai_tools:
        if tool.get("type") == "function":
            fn = tool["function"]
            anthropic_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        elif "name" in tool and "parameters" in tool:
            anthropic_tools.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool["parameters"],
            })
    return anthropic_tools


def _openai_messages_to_anthropic(messages: list) -> tuple:
    system = SYSTEM_PROMPT
    anthropic_messages = []

    for msg in messages:
        role = msg.get("role", "")

        if role == "system":
            system = msg.get("content", "")
            continue

        if role == "user":
            anthropic_messages.append({"role": "user", "content": msg.get("content", "")})

        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                content_blocks = []
                text = msg.get("content")
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for tc in tool_calls:
                    fn = tc["function"]
                    try:
                        inp = json.loads(fn["arguments"])
                    except (json.JSONDecodeError, TypeError):
                        inp = {"code": fn.get("arguments", ""), "language": "python"}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": fn["name"],
                        "input": inp,
                    })
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            else:
                anthropic_messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                })

        elif role == "tool":
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            }
            if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                last = anthropic_messages[-1]
                if isinstance(last["content"], str):
                    last["content"] = [{"type": "text", "text": last["content"]}, tool_result_block]
                else:
                    last["content"].append(tool_result_block)
            else:
                anthropic_messages.append({"role": "user", "content": [tool_result_block]})

    return system, anthropic_messages


def _anthropic_response_to_openai(response: dict) -> dict:
    content_blocks = response.get("content", [])
    text_parts = []
    tool_calls = []

    for block in content_blocks:
        if block["type"] == "text":
            text_parts.append(block["text"])
        elif block["type"] == "tool_use":
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block["input"]),
                },
            })

    message = {"role": "assistant", "content": "\n".join(text_parts) if text_parts else None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    stop_reason = response.get("stop_reason", "end_turn")
    finish_reason = "tool_calls" if stop_reason == "tool_use" else "stop"

    return {
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": response.get("usage", {}),
    }


async def _call_openai_api(messages: list, openai_tools: list = None) -> dict:
    body = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.1,
    }
    if openai_tools:
        body["tools"] = openai_tools
        body["tool_choice"] = "auto"

    max_retries = 3
    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{LLM_API_URL}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code in (429, 503, 529):
                wait = 2 ** attempt * 5
                logger.warning("LLM API returned %d, retrying in %ds (attempt %d/%d)",
                               resp.status_code, wait, attempt + 1, max_retries)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()

    raise Exception(f"LLM API failed after {max_retries} retries")


async def _call_vertex_api(messages: list, tools: list = None) -> dict:
    system, anthropic_messages = _openai_messages_to_anthropic(messages)
    token = _refresh_access_token()

    url = (
        f"https://{VERTEX_REGION}-aiplatform.googleapis.com/v1/"
        f"projects/{VERTEX_PROJECT}/locations/{VERTEX_REGION}/"
        f"publishers/anthropic/models/{MODEL_NAME}:rawPredict"
    )

    body = {
        "anthropic_version": "vertex-2023-10-16",
        "system": system,
        "messages": anthropic_messages,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.1,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = {"type": "auto"}

    max_retries = 3
    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code in (429, 503, 529):
                wait = 2 ** attempt * 5
                logger.warning("Vertex AI returned %d, retrying in %ds (attempt %d/%d)",
                               resp.status_code, wait, attempt + 1, max_retries)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return _anthropic_response_to_openai(resp.json())

    raise Exception(f"Vertex AI API failed after {max_retries} retries")


async def call_llm(messages: list, tools: list = None, openai_tools: list = None) -> dict:
    if USE_OPENAI_API:
        return await _call_openai_api(messages, openai_tools)
    return await _call_vertex_api(messages, tools)


def _sse_chunk(chat_id, model, delta, finish_reason):
    return "data: {}\n\n".format(json.dumps({
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }))


async def _stream_openai(messages, original_body=None):
    body = {
        "model": MODEL_NAME,
        "stream": True,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.1,
    }
    openai_tools = (original_body or {}).get("tools", [])
    if openai_tools:
        body["tools"] = openai_tools
        body["tool_choice"] = "auto"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            async with client.stream(
                "POST", f"{LLM_API_URL}/chat/completions", json=body,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n\n"

    except Exception as e:
        logger.exception("Streaming from LLM API failed")
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        model = (original_body or {}).get("model", MODEL_NAME)
        yield _sse_chunk(chat_id, model, {"content": f"\n\nError: {e}"}, None)
        yield _sse_chunk(chat_id, model, {}, "stop")
        yield "data: [DONE]\n\n"


async def _stream_vertex(messages, tools=None, original_body=None):
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    model = (original_body or {}).get("model", MODEL_NAME)

    yield _sse_chunk(chat_id, model, {"role": "assistant"}, None)

    system, anthropic_messages = _openai_messages_to_anthropic(messages)
    token = _refresh_access_token()

    url = (
        f"https://{VERTEX_REGION}-aiplatform.googleapis.com/v1/"
        f"projects/{VERTEX_PROJECT}/locations/{VERTEX_REGION}/"
        f"publishers/anthropic/models/{MODEL_NAME}:rawPredict"
    )

    body = {
        "anthropic_version": "vertex-2023-10-16",
        "stream": True,
        "system": system,
        "messages": anthropic_messages,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.1,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = {"type": "auto"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            async with client.stream(
                "POST", url, json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                if resp.status_code in (429, 503, 529):
                    yield _sse_chunk(chat_id, model,
                        {"content": f"Error: Vertex AI returned {resp.status_code}. Please try again."}, None)
                    yield _sse_chunk(chat_id, model, {}, "stop")
                    yield "data: [DONE]\n\n"
                    return
                resp.raise_for_status()

                event_type = None
                finish_reason = "stop"
                tool_index = -1

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])

                        if event_type == "content_block_start":
                            block = data.get("content_block", {})
                            if block.get("type") == "tool_use":
                                tool_index += 1
                                yield _sse_chunk(chat_id, model, {
                                    "tool_calls": [{
                                        "index": tool_index,
                                        "id": block["id"],
                                        "type": "function",
                                        "function": {"name": block["name"], "arguments": ""},
                                    }]
                                }, None)

                        elif event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield _sse_chunk(chat_id, model, {"content": delta["text"]}, None)
                            elif delta.get("type") == "input_json_delta":
                                yield _sse_chunk(chat_id, model, {
                                    "tool_calls": [{
                                        "index": tool_index,
                                        "function": {"arguments": delta["partial_json"]},
                                    }]
                                }, None)

                        elif event_type == "message_delta":
                            stop = data.get("delta", {}).get("stop_reason", "end_turn")
                            finish_reason = "tool_calls" if stop == "tool_use" else "stop"

                yield _sse_chunk(chat_id, model, {}, finish_reason)
                yield "data: [DONE]\n\n"

    except Exception as e:
        logger.exception("Streaming from Vertex AI failed")
        yield _sse_chunk(chat_id, model, {"content": f"\n\nError: {e}"}, None)
        yield _sse_chunk(chat_id, model, {}, "stop")
        yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    openai_tools = body.get("tools", [])
    anthropic_tools = _openai_tools_to_anthropic(openai_tools) if openai_tools else []

    if stream:
        if USE_OPENAI_API:
            gen = _stream_openai(messages, body)
        else:
            gen = _stream_vertex(messages, anthropic_tools or None, body)
        return StreamingResponse(gen, media_type="text/event-stream")

    try:
        result = await call_llm(messages, anthropic_tools or None, openai_tools or None)
        choice = result["choices"][0]
        msg = choice["message"]
        finish_reason = choice.get("finish_reason", "stop")

        logger.info(
            "finish_reason=%s | tool_calls=%s | content_preview=%.200s",
            finish_reason, bool(msg.get("tool_calls")),
            msg.get("content", "") or "",
        )

        return JSONResponse({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", MODEL_NAME),
            "choices": [{"index": 0, "message": msg, "finish_reason": finish_reason}],
            "usage": result.get("usage", {}),
        })

    except Exception as e:
        logger.exception("Chat completion failed")
        return JSONResponse({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", MODEL_NAME),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": f"Error: {e}"}, "finish_reason": "stop"}],
            "usage": {},
        })


@app.post("/v1/sandbox/execute")
async def sandbox_execute(request: Request):
    body = await request.json()
    language = body.get("language", "python")
    code = body.get("code", "")

    logger.info("Direct sandbox execute: %s (%d chars)", language, len(code))
    result = execute_code(get_sandbox_client(), language, code)
    return JSONResponse(result)


@app.get("/health")
async def health():
    return {"status": "ok"}
