from __future__ import annotations

import base64
import subprocess
import urllib.parse
from pathlib import Path

import httpx

from ..context import AgentContext

OUT = Path(__file__).resolve().parents[3] / "data" / "workspace"
OUT.mkdir(parents=True, exist_ok=True)


async def image(args: dict, ctx: AgentContext) -> str:
    prompt = args.get("prompt", "")
    size = args.get("size", "1024x1024")
    key = ctx.settings.api_keys.get("openai", "")
    path = OUT / f"img_{abs(hash(prompt)) % 10 ** 8}.png"
    if key:
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "gpt-image-1", "prompt": prompt, "size": size},
                )
            b64 = r.json()["data"][0].get("b64_json")
            if b64:
                path.write_bytes(base64.b64decode(b64))
                if ctx.deliver:
                    await ctx.deliver({"photo": str(path)})
                return f"Image saved: {path}"
        except Exception:
            pass  # fall through to the keyless generator
    url = ("https://image.pollinations.ai/prompt/"
           f"{urllib.parse.quote(prompt)}?width=1024&nologo=true")
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
            r = await c.get(url)
        path.write_bytes(r.content)
        if ctx.deliver:
            await ctx.deliver({"photo": str(path)})
        return f"Image saved: {path}"
    except Exception as e:
        return f"Image generation failed: {e}"


async def tts(args: dict, ctx: AgentContext) -> str:
    text = args.get("text", "")
    path = OUT / f"tts_{abs(hash(text)) % 10 ** 8}.mp3"
    key = ctx.settings.api_keys.get("openai", "")
    if key:
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "tts-1", "voice": "alloy", "input": text},
                )
            path.write_bytes(r.content)
            if ctx.deliver:
                await ctx.deliver({"voice": str(path)})
            return f"Voice saved: {path}"
        except Exception:
            pass
    try:
        proc = subprocess.run(["edge-tts", "--text", text, "--write-media", str(path)],
                              capture_output=True, timeout=180)
        if proc.returncode == 0 and path.exists():
            if ctx.deliver:
                await ctx.deliver({"voice": str(path)})
            return f"Voice saved: {path}"
    except FileNotFoundError:
        return "TTS needs an OpenAI key, or edge-tts installed (pip install edge-tts)."
    return "TTS failed."


async def video(args: dict, ctx: AgentContext) -> str:
    prompt = args.get("prompt", "")
    key = ctx.settings.fal_key or ctx.settings.api_keys.get("fal", "")
    if not key:
        return "Video generation needs a fal.ai key: /set_api_key fal <key>"
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                "https://queue.fal.run/fal-ai/kling-video/v1.6/standard/text-to-video",
                headers={"Authorization": f"Key {key}"},
                json={"prompt": prompt, "duration": "5"},
            )
            info = r.json()
        status = info.get("status", "")
        if status == "IN_QUEUE":
            return "Video queued - run this command again in a minute to fetch it."
        video_url = info.get("video", {}).get("url")
        if not video_url:
            return f"Video request status: {status}"
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as c2:
            data = await c2.get(video_url)
        path = OUT / f"vid_{abs(hash(prompt)) % 10 ** 8}.mp4"
        path.write_bytes(data.content)
        if ctx.deliver:
            await ctx.deliver({"video": str(path)})
        return f"Video saved: {path}"
    except Exception as e:
        return f"Video generation failed: {e}"


async def document(args: dict, ctx: AgentContext) -> str:
    """Create a file (code/doc/text) with LLM content and attach it to chat."""
    prompt = args.get("prompt", "")
    filename = args.get("filename", "output.txt")
    kind = args.get("kind", "codes")
    instr = {
        "codes": "Write complete, runnable code.",
        "docs": "Write well-structured documentation in Markdown.",
        "files": "Write the requested file content.",
    }.get(kind, "Write the file content.")
    msg = await ctx.llm.chat(
        [{"role": "user",
          "content": f"{instr}\n\nRequest: {prompt}\n\nReturn only the file body."}],
        temperature=0.2, max_tokens=6000)
    body = (msg.get("content") or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0]
    path = OUT / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    if ctx.deliver:
        await ctx.deliver({"document": str(path)})
    return f"Saved: {path}"
