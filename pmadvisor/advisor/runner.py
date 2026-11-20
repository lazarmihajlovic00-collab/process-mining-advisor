"""AI advisor: a tool-using agent that reads the analysis and writes a Serbian report with prioritised automation
recommendations. Runs on Gemini function calling or the Anthropic Messages API (same tools, same prompt).

The advisor never computes savings itself and cannot approve anything: estimates come from `levers.py`, what-if
numbers from the simulator, and each recommendation is PENDING until a named person accepts or rejects it. The
final text is checked: every number must appear in a tool result (`grounding.py`).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .. import llm
from ..service import Workspace
from . import grounding, template
from .tools import TOOLS, execute

MAX_TURNS = 14

SYSTEM_PROMPT = """Ti si savetnik za automatizaciju poslovnih procesa (process mining). Analiziraš proces nabavke
i plaćanja (purchase-to-pay) jedne firme iz njenog event loga i predlažeš šta prvo automatizovati. Pišeš na srpskom
(latinica), za finansijskog direktora: jasno, konkretno, bez marketinga.

Pravila:
- Svaki broj u tvom tekstu mora doći iz rezultata alata. Ne računaj sam zbirove, procente ni uštede; za zbir
  više mera koristi procena_paketa, za efekat na vreme ciklusa simuliraj_sta_ako (ako je dostupno).
- Prvo pozovi pregled_procesa i nalazi. Za svaku meru koju razmatraš pozovi proceni_meru.
- Za svaki ozbiljan nalaz napravi preporuku alatom predlozi_preporuku (najviše 6). Ne predlaži meru za problem
  koji nalazi ne pokazuju. Mera sa negativnom ili nultom neto uštedom može biti preporučena samo kao kontrolna mera
  (rizik, usklađenost), i to reci.
- Razlikuj izmerene procene (confidence "measured") od pretpostavljenih ("assumed") i to napiši.
- Preporuke samo čekaju odluku čoveka. Nikad ne tvrdi da je nešto odobreno ili uvedeno.

Na kraju napiši izveštaj u ovom obliku (Markdown):
## Sažetak
2-3 rečenice: stanje procesa i najveća prilika.
## Preporuke po prioritetu
Za svaku: naziv mere, problem iz loga (sa brojem), procena (sati i/ili RSD godišnje, povrat investicije),
da li je izmereno ili pretpostavka.
## Šta pokazuje simulacija
Ako je simulacija dostupna: efekat paketa mera pre/posle. Ako nije, napiši da je potreban kalibrisan model.
## Rizici i šta proveriti
2-4 stavke."""


@dataclass
class AdvisorResult:
    text: str
    source: str
    model: str | None
    recommendation_ids: list[int] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    tool_outputs: list = field(default_factory=list)
    grounding: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    llm_calls: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("tool_outputs")
        return d


def advise(ws: Workspace, user: str = "analiticar", question: str | None = None) -> AdvisorResult:
    p = llm.provider()
    if p is None:
        res = template.advise(ws, user)
    elif p == "gemini":
        res = _run_gemini(ws, user, question)
    else:
        res = _run_anthropic(ws, user, question)
    if res.source != "template":
        res.grounding = grounding.check(res.text, res.tool_outputs)
    ws.store.add_report(ws.run().id, res.source, res.model, res.text, res.grounding, res.tool_calls)
    ws.store.audit(f"agent (za {user})" if res.source != "template" else "system", "report_written",
                   {"run": ws.run().id, "source": res.source, "model": res.model,
                    "recommendations": res.recommendation_ids, "grounded_pct": res.grounding.get("grounded_pct")})
    return res


def _user_message(ws: Workspace, question: str | None) -> str:
    run = ws.run()
    sim = "dostupna" if run.scenario is not None else "NIJE dostupna (pravi log)"
    q = question or "Analiziraj proces i napiši izveštaj sa preporukama."
    return f"(log: {run.label}; simulacija šta-ako: {sim})\n{q}"


# ------------------------------------------------------------------------------------------------ anthropic
def _run_anthropic(ws: Workspace, user: str, question: str | None) -> AdvisorResult:
    model = llm.CLAUDE_MODEL
    res = AdvisorResult("", "llm", model)
    messages = [{"role": "user", "content": _user_message(ws, question)}]
    tools = [{**t, "strict": True} for t in TOOLS]
    t0 = time.perf_counter()
    for _ in range(MAX_TURNS):
        t_call = time.perf_counter()
        try:
            resp = llm.client().messages.create(model=model, max_tokens=16000, system=SYSTEM_PROMPT, tools=tools,
                                                messages=messages, output_config={"effort": "medium"})
        except Exception as ex:  # network, auth, rate limit
            res.error = f"{type(ex).__name__}: {ex}"
            break
        u = resp.usage
        cost = llm.cost_usd(model, u.input_tokens, u.output_tokens)
        res.input_tokens += u.input_tokens
        res.output_tokens += u.output_tokens
        res.cost_usd += cost
        res.llm_calls += 1
        ws.store.log_llm(model, u.input_tokens, u.output_tokens, cost, int((time.perf_counter() - t_call) * 1000))
        if resp.stop_reason == "refusal":
            res.error = "refusal"
            break
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            res.text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            break
        outs = []
        for tu in uses:
            args = tu.input if isinstance(tu.input, dict) else json.loads(tu.input)
            out = execute(ws, tu.name, args, user, res.recommendation_ids)
            res.tool_calls.append({"name": tu.name, "input": args})
            res.tool_outputs.append(out)
            outs.append({"type": "tool_result", "tool_use_id": tu.id, "content": grounding.dumps(out),
                         **({"is_error": True} if "error" in out else {})})
        messages.append({"role": "user", "content": outs})
    else:
        res.error = "max_turns"
    res.latency_ms = int((time.perf_counter() - t0) * 1000)
    return _fallback_text(res)


# ------------------------------------------------------------------------------------------------ gemini
def _gemini_schema(schema: dict) -> dict:
    out: dict = {}
    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        out["type"] = non_null[0].upper()
        if "null" in t:
            out["nullable"] = True
    elif t:
        out["type"] = t.upper()
    for k in ("description", "enum"):
        if k in schema:
            out[k] = schema[k]
    if "items" in schema:
        out["items"] = _gemini_schema(schema["items"])
    if "properties" in schema:
        out["properties"] = {k: _gemini_schema(v) for k, v in schema["properties"].items()}
        if schema.get("required"):
            out["required"] = list(schema["required"])
    return out


def gemini_tools() -> list[dict]:
    decls = []
    for t in TOOLS:
        d = {"name": t["name"], "description": t["description"]}
        if t["input_schema"].get("properties"):
            d["parameters"] = _gemini_schema(t["input_schema"])
        decls.append(d)
    return [{"functionDeclarations": decls}]


def _gemini_post(url: str, body: dict, retries: int = 5):
    delay = 8.0
    for attempt in range(retries):
        resp = llm.gemini_http().post(url, headers={"x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")}, json=body)
        if resp.status_code not in (429, 500, 503) or attempt == retries - 1:
            return resp
        time.sleep(delay)
        delay *= 2
    return resp


def _run_gemini(ws: Workspace, user: str, question: str | None) -> AdvisorResult:
    model = llm.GEMINI_MODEL
    res = AdvisorResult("", "llm", model)
    contents = [{"role": "user", "parts": [{"text": _user_message(ws, question)}]}]
    url = f"{llm.GEMINI_URL}/models/{model}:generateContent"
    t0 = time.perf_counter()
    for _ in range(MAX_TURNS):
        t_call = time.perf_counter()
        try:
            resp = _gemini_post(url, {"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]}, "contents": contents,
                                      "tools": gemini_tools(), "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}}})
        except Exception as ex:
            res.error = f"{type(ex).__name__}: {ex}"
            break
        if resp.status_code >= 400:
            res.error = f"Gemini {resp.status_code}: {resp.text[:300]}"
            break
        body = resp.json()
        usage = body.get("usageMetadata", {})
        tin, tout = usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        res.input_tokens += tin
        res.output_tokens += tout
        res.llm_calls += 1
        ws.store.log_llm(model, tin, tout, 0.0, int((time.perf_counter() - t_call) * 1000))
        cands = body.get("candidates") or []
        if not cands or "content" not in cands[0]:
            res.error = f"no content ({(cands[0].get('finishReason') if cands else None) or body.get('promptFeedback')})"
            break
        content = cands[0]["content"]
        contents.append(content)  # parts go back unchanged (thought signatures)
        calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]
        if not calls:
            res.text = "\n".join(p["text"] for p in content.get("parts", []) if "text" in p and not p.get("thought")).strip()
            break
        parts = []
        for call in calls:
            args = call.get("args") or {}
            if "prioritet" in args and args["prioritet"] is not None:
                args["prioritet"] = int(args["prioritet"])
            if "broj" in args and args["broj"] is not None:
                args["broj"] = int(args["broj"])
            out = execute(ws, call["name"], args, user, res.recommendation_ids)
            res.tool_calls.append({"name": call["name"], "input": args})
            res.tool_outputs.append(out)
            fr = {"name": call["name"], "response": json.loads(grounding.dumps(out))}
            if "id" in call:
                fr["id"] = call["id"]
            parts.append({"functionResponse": fr})
        contents.append({"role": "user", "parts": parts})
    else:
        res.error = "max_turns"
    res.latency_ms = int((time.perf_counter() - t0) * 1000)
    return _fallback_text(res)


def _fallback_text(res: AdvisorResult) -> AdvisorResult:
    if not res.text:
        res.text = f"Savetnik nije završio izveštaj ({res.error or 'bez teksta'}). Preporuke koje je napravio su ispod."
    return res
