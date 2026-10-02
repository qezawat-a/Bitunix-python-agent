"""Probe every model on an OpenAI-compatible gateway and report which work.

Why this exists: `/models` lists everything the gateway *sells*, not what your
key can actually call. Probing shows the real picture — in this account most
upstreams answer 402/403/401/429 and only a handful actually serve.

Usage:
    python tools/probe_models.py                 # all models
    python tools/probe_models.py --limit 100     # first 100
    python tools/probe_models.py --contains kc   # filter by substring

Writes a JSON report and prints a summary. Costs one ~16-token request per
model; concurrency is deliberately modest to avoid tripping gateway rate
limits (we see "403 ... reset after 2m").
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402

PROBE_PROMPT = "Reply with the single word OK"
PROBE_MAX_TOKENS = 16
DECODER = json.JSONDecoder()
UPSTREAM_MARKERS = ("[402]", "[403]", "[401]", "[429]", "[404]", "[500]")


def parse_body(text: str) -> dict:
    """
    Parse a chat-completion body that may carry an SSE trailer.

    This gateway answers *non-streaming* requests with the JSON object and then
    appends `data: [DONE]\\n\\n`. Plain `json.loads`/`r.json()` dies with
    "Extra data: line 1 column 769" on exactly the models that WORK, so decode
    only the leading JSON value.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        obj, _ = DECODER.raw_decode(text.lstrip())
        return obj


def classify(status: int, body: dict) -> tuple[str, str]:
    """Map an HTTP status + error body to (verdict, short reason)."""
    if status == 200 and body.get("choices"):
        return "OK", ""
    err = body.get("error") or {}
    msg = err.get("message") or body.get("msg") or ""
    for marker in UPSTREAM_MARKERS:
        if marker in msg:
            return "upstream" + marker[1:-1], msg[:160]
    return "HTTP%d" % status, msg[:160]


async def probe(client: httpx.AsyncClient, base: str, headers: dict,
                sem: asyncio.Semaphore, model: str) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": PROBE_MAX_TOKENS,
    }
    t0 = time.monotonic()
    async with sem:
        try:
            r = await client.post(f"{base}/chat/completions", headers=headers, json=body)
        except httpx.TimeoutException:
            return {"model": model, "ok": False, "verdict": "timeout",
                    "detail": "", "ms": int((time.monotonic() - t0) * 1000)}
        except Exception as e:
            return {"model": model, "ok": False, "verdict": type(e).__name__,
                    "detail": str(e)[:160], "ms": int((time.monotonic() - t0) * 1000)}
    ms = int((time.monotonic() - t0) * 1000)
    try:
        parsed = parse_body(r.text)
    except Exception as e:
        return {"model": model, "ok": False, "verdict": "badjson",
                "detail": str(e)[:160], "ms": ms}
    verdict, detail = classify(r.status_code, parsed)
    return {"model": model, "ok": verdict == "OK", "verdict": verdict,
            "detail": detail, "ms": ms}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="probe at most N models")
    ap.add_argument("--contains", default="", help="only models containing this substring")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--out", default="data/working_models.json")
    args = ap.parse_args()

    base = Config.AI_BASE_URL.rstrip("/")
    headers = {"Authorization": "Bearer " + Config.AI_API_KEY,
               "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        try:
            r = await client.get(base + "/models", headers=headers, timeout=20)
            ids = sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
        except Exception as e:
            print("Could not list models: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return 1

        if args.contains:
            ids = [m for m in ids if args.contains.lower() in m.lower()]
        total_listed = len(ids)
        if args.limit:
            ids = ids[: args.limit]

        print("Probing %d of %d models (concurrency=%d, timeout=%ss)"
              % (len(ids), total_listed, args.concurrency, args.timeout),
              file=sys.stderr)
        sem = asyncio.Semaphore(args.concurrency)
        t0 = time.monotonic()
        results = await asyncio.gather(
            *[probe(client, base, headers, sem, m) for m in ids]
        )

    elapsed = time.monotonic() - t0
    results.sort(key=lambda x: (not x["ok"], x["model"]))
    ok = [r for r in results if r["ok"]]

    tally: dict[str, int] = {}
    for r in results:
        if not r["ok"]:
            tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "base_url": base,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probed": len(results),
        "working": len(ok),
        "results": results,
    }, indent=2))

    print("\n=== %d working / %d probed in %.0fs ===\n"
          % (len(ok), len(results), elapsed))
    if ok:
        print("WORKING:")
        for r in ok:
            print("  OK  %-62s %6dms" % (r["model"], r["ms"]))
    print("\nFAILURE REASONS:")
    for verdict, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        sample = next((r["detail"] for r in results
                       if not r["ok"] and r["verdict"] == verdict and r["detail"]), "")
        print("  %-12s %4d   e.g. %s" % (verdict, n, sample[:110]))
    print("\nReport: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
