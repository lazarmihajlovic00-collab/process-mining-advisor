"""Tools the AI advisor can call. All numbers come from the analysis, the estimators and the simulator; the only
thing the advisor can create is a PENDING recommendation that waits for a person."""

from __future__ import annotations

from .. import analysis
from ..levers import LEVERS
from ..service import ServiceError, Workspace


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


LEVER_ENUM = {"type": "string", "enum": list(LEVERS)}

TOOLS = [
    _tool("pregled_procesa", "KPI procesa (broj slučajeva, period, vreme ciklusa, varijante, ručni sati godišnje) i "
          "usklađenost sa propisanim procesom (conformance).", {}, []),
    _tool("nalazi", "Problemi koje su detektori našli u logu, sa brojevima (evidence) i merama koje ih rešavaju.", {}, []),
    _tool("statistika_aktivnosti", "Statistika po aktivnostima: broj, trajanje rada, čekanje pre aktivnosti, udeo ručnog "
          "rada, ručni sati godišnje. aktivnost = tačan naziv ili null za sve.",
          {"aktivnost": {"type": ["string", "null"]}}, ["aktivnost"]),
    _tool("varijante", "Najčešće varijante (putanje) procesa sa udelom i vremenom ciklusa.",
          {"broj": {"type": "integer"}}, ["broj"]),
    _tool("kandidati_za_automatizaciju", "Ručne aktivnosti rangirane po satima godišnje, aktivnosti sa najviše čekanja "
          "i ponovljeni rad (rework).", {}, []),
    _tool("proceni_meru", "Procena uštede jedne mere iz loga: sati, RSD, uvođenje, povrat investicije, efekti i "
          "pretpostavke. Mere: " + "; ".join(f"{l.id} = {l.title}" for l in LEVERS.values()),
          {"mera": LEVER_ENUM}, ["mera"]),
    _tool("procena_paketa", "Zbir procena za više mera (sati, RSD, uvođenje, povrat). Koristi ovo umesto da sabiraš sam.",
          {"mere": {"type": "array", "items": LEVER_ENUM}}, ["mere"]),
    _tool("simuliraj_sta_ako", "Ponovna simulacija istog perioda sa uvedenim merama: pre/posle za ručne sate, vreme "
          "ciklusa, propuštene popuste, dupla plaćanja, čekanje na odobrenje. Radi samo za simulirani log.",
          {"mere": {"type": "array", "items": LEVER_ENUM}}, ["mere"]),
    _tool("predlozi_preporuku", "Napravi preporuku za uvođenje mere. Preporuka dobija procenu iz sistema i ČEKA odluku "
          "čoveka; ti je ne možeš odobriti. prioritet: 1 = odmah, 2 = ovaj kvartal, 3 = kasnije. obrazlozenje: 1-3 "
          "rečenice na srpskom, samo brojevi iz alata.",
          {"mera": LEVER_ENUM, "prioritet": {"type": "integer"}, "obrazlozenje": {"type": "string"}},
          ["mera", "prioritet", "obrazlozenje"]),
]


def execute(ws: Workspace, name: str, args: dict, user: str, created: list[int]) -> dict:
    run = ws.run()
    try:
        if name == "pregled_procesa":
            return ws.overview(run)
        if name == "nalazi":
            return {"nalazi": ws.findings(run)}
        if name == "statistika_aktivnosti":
            stats = analysis.activity_stats(run.an)
            a = args.get("aktivnost")
            if a:
                stats = [s for s in stats if s["activity"].lower() == a.lower()] or [
                    {"error": f"nema aktivnosti '{a}'", "aktivnosti": [s["activity"] for s in stats]}]
            return {"aktivnosti": stats}
        if name == "varijante":
            return {"varijante": analysis.variants(run.an, max(1, min(int(args.get("broj") or 5), 15)))}
        if name == "kandidati_za_automatizaciju":
            return {"kandidati": analysis.automation_candidates(run.an), "cekanje": analysis.waiting_hotspots(run.an),
                    "rework": analysis.rework(run.an)}
        if name == "proceni_meru":
            return ws.estimate(args["mera"], run)
        if name == "procena_paketa":
            return ws.package(list(args["mere"]), run)
        if name == "simuliraj_sta_ako":
            return ws.whatif(list(args["mere"]), run)
        if name == "predlozi_preporuku":
            rec = ws.propose(args["mera"], int(args.get("prioritet") or 2), args.get("obrazlozenje", ""), "llm",
                             f"agent (za {user})", run)
            if not rec.get("duplicate"):
                created.append(rec["id"])
            e = rec["estimate"]
            return {"preporuka_id": rec["id"], "status": "PENDING (čeka odluku čoveka)", "mera": rec["lever"],
                    "naziv": rec["title"], "net_rsd_per_year": e["net_rsd_per_year"],
                    "payback_months": e["payback_months"], "hours_per_year": e["hours_per_year"]}
        return {"error": f"nepoznat alat {name}"}
    except ServiceError as ex:
        return {"error": str(ex)}
    except (KeyError, ValueError, TypeError) as ex:
        return {"error": f"neispravni argumenti: {ex}"}
