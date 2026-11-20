"""Service layer used by the CLI, the API and the advisor's tools.

A Workspace holds the analysed log of the current run (cached), and enforces the human-in-the-loop rules:
recommendations are created as PENDING (by the AI advisor, the template advisor or a person), and only a named
person can accept or reject them. Every step is written to the audit chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import analysis, discovery, findings, levers, whatif
from .eventlog import check, load, normalise
from .profile import P2P, Profile
from .scenario import Scenario, demo
from .simulate import simulate
from .store import Store


class ServiceError(Exception):
    pass


@dataclass
class Run:
    id: int
    label: str
    source: str                        # simulated | file
    an: analysis.Analysis
    scenario: Scenario | None = None
    _cache: dict = field(default_factory=dict)

    def cached(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]


def is_agent(user: str) -> bool:
    u = (user or "").strip().lower()
    return not u or u.startswith("agent") or u in ("ai", "llm", "system", "claude", "gemini")


class Workspace:
    def __init__(self, store: Store | None = None, profile: Profile = P2P):
        self.store = store or Store()
        self.profile = profile
        self.runs: dict[int, Run] = {}
        self.current: Run | None = None

    # ------------------------------------------------------------------ loading
    def load_scenario(self, s: Scenario | None = None, label: str | None = None) -> Run:
        s = s or demo()
        an = analysis.enrich(normalise(simulate(s)), self.profile)
        return self._add(an, label or s.name, "simulated", s)

    def load_file(self, path: str | Path, mapping: dict | None = None, label: str | None = None) -> Run:
        df = load(path, mapping)
        chk = check(df)
        if not chk.ok:
            raise ServiceError("; ".join(chk.problems))
        an = analysis.enrich(df, self.profile)
        run = self._add(an, label or Path(path).name, "file", None, str(path))
        run._cache["log_check"] = chk.__dict__
        return run

    def _add(self, an, label, source, s, path=None) -> Run:
        run_id = self.store.add_run(source, label, s.to_dict() if s else None, path, analysis.kpis(an))
        run = Run(run_id, label, source, an, s)
        self.runs[run_id] = run
        self.current = run
        self.store.audit("system", "run_loaded", {"run": run_id, "label": label, "source": source,
                                                   "cases": int(len(an.cases))})
        return run

    def run(self, run_id: int | None = None) -> Run:
        run = self.runs.get(run_id) if run_id else self.current
        if run is None:
            raise ServiceError("nema učitanog loga")
        return run

    # ------------------------------------------------------------------ analysis views
    def kpis(self, run: Run | None = None) -> dict:
        run = run or self.run()
        return analysis.kpis(run.an)

    def findings(self, run: Run | None = None) -> list[dict]:
        run = run or self.run()
        return run.cached("findings", lambda: [f.to_dict() for f in findings.detect(run.an)])

    def estimates(self, run: Run | None = None) -> list[dict]:
        run = run or self.run()
        return run.cached("estimates", lambda: [e.to_dict() for e in levers.estimate_all(run.an)])

    def estimate(self, lever: str, run: Run | None = None, **params) -> dict:
        run = run or self.run()
        if lever not in levers.LEVERS:
            raise ServiceError(f"nepoznata mera {lever}; moguće: {', '.join(levers.LEVERS)}")
        if not params:
            for e in self.estimates(run):
                if e["lever"] == lever:
                    return e
        return levers.estimate(run.an, lever, **params).to_dict()

    def package(self, lever_ids: list[str], run: Run | None = None) -> dict:
        """Sum of the log-based estimates of several levers (overlaps are not removed; see `whatif`)."""
        run = run or self.run()
        es = [self.estimate(l, run) for l in lever_ids]
        tot = {k: round(sum(e[k] for e in es)) for k in ("hours_per_year", "labor_rsd_per_year", "cash_rsd_per_year",
                                                          "one_off_rsd", "running_rsd_per_year", "net_rsd_per_year")}
        tot["payback_months"] = round(12 * tot["one_off_rsd"] / tot["net_rsd_per_year"], 1) if tot["net_rsd_per_year"] > 0 else None
        tot["fte"] = round(tot["hours_per_year"] / 1_720, 2)
        return {"levers": lever_ids, "total": tot,
                "note": "zbir pojedinačnih procena; mere koje utiču na iste korake se preklapaju, tačan zbir daje simulacija"}

    def whatif(self, lever_ids: list[str], run: Run | None = None) -> dict:
        run = run or self.run()
        if run.scenario is None:
            raise ServiceError("simulacija 'šta ako' radi samo za log iz ugrađenog simulatora; za pravi log je "
                               "potreban simulacioni model kalibrisan na tom logu")
        bad = [l for l in lever_ids if l not in levers.LEVERS]
        if bad:
            raise ServiceError(f"nepoznate mere: {bad}")
        key = ("whatif", tuple(sorted(lever_ids)))
        return run.cached(key, lambda: whatif.run(run.scenario, lever_ids, run.an))

    def compare(self, run: Run | None = None) -> list[dict]:
        run = run or self.run()
        if run.scenario is None:
            return []
        return run.cached("compare", lambda: [whatif.compare(run.scenario, l, run.an) for l in levers.LEVERS])

    def conformance(self, run: Run | None = None) -> dict:
        run = run or self.run()
        return run.cached("conformance", lambda: discovery.conformance(run.an))

    def model(self, run: Run | None = None) -> dict:
        run = run or self.run()
        return run.cached("model", lambda: discovery.model_summary(run.an))

    def overview(self, run: Run | None = None) -> dict:
        run = run or self.run()
        c = self.conformance(run)
        return {"run": run.id, "label": run.label, "source": run.source, "kpis": self.kpis(run),
                "conformance": {"fitting_cases_pct": c["fitting_cases_pct"], "log_fitness": c["log_fitness"],
                                "top_deviations": c["deviation_patterns"][:4]},
                "hourly_cost_rsd": self.profile.hourly_cost_rsd}

    # ------------------------------------------------------------------ recommendations (human in the loop)
    def propose(self, lever: str, priority: int, rationale: str, source: str, created_by: str,
                run: Run | None = None) -> dict:
        run = run or self.run()
        if lever not in levers.LEVERS:
            raise ServiceError(f"nepoznata mera {lever}")
        for r in self.store.recommendations(run.id):
            if r["lever"] == lever and r["status"] == "PENDING":
                return {**r, "duplicate": True}
        est = self.estimate(lever, run)
        priority = max(1, min(3, int(priority)))
        rec_id = self.store.add_recommendation(run.id, lever, levers.LEVERS[lever].title, priority, rationale.strip(),
                                               est, source, created_by)
        self.store.audit(created_by, "recommendation_proposed", {"run": run.id, "id": rec_id, "lever": lever,
                                                                 "priority": priority, "source": source,
                                                                 "net_rsd_per_year": est["net_rsd_per_year"]})
        return self.store.recommendation(rec_id)

    def decide(self, rec_id: int, accept: bool, user: str, note: str = "") -> dict:
        if is_agent(user):
            raise ServiceError("preporuku može da prihvati ili odbije samo imenovana osoba, ne agent")
        rec = self.store.recommendation(rec_id)
        if rec is None:
            raise ServiceError(f"nema preporuke #{rec_id}")
        if rec["status"] != "PENDING":
            raise ServiceError(f"preporuka #{rec_id} je već {rec['status']}")
        status = "ACCEPTED" if accept else "REJECTED"
        self.store.decide(rec_id, status, user, note)
        self.store.audit(user, "recommendation_" + status.lower(), {"id": rec_id, "lever": rec["lever"], "note": note})
        return self.store.recommendation(rec_id)

    def recommendations(self, run: Run | None = None) -> list[dict]:
        run = run or self.run()
        return self.store.recommendations(run.id)
