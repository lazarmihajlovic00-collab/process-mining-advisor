# Decision log

Short records of the design decisions behind this project: the question, the options, what was chosen and why.
Lazar Mihajlović owns the project (problem, scope, goals, final say). The implementation was built with Claude (AI)
as the builder. Lazar asked the builder to make the design decisions for this project ("radi ti sve, ti bolje
znaš"), so every decision below is a builder recommendation that Lazar accepted by that instruction, unless it
says otherwise.

## D0. Problem and scope (Lazar, 2026-11-19)
**Question:** Which third portfolio project shows process analysis and automation consulting, next to the SEF
e-invoice agent (AP automation) and the route dispatcher (optimization)?
**Chosen:** A process-mining automation advisor: it reads an ERP event log, discovers how the process really runs,
finds bottlenecks, rework and rule breaks, estimates what automation would save, and lets an AI advisor write the
recommendations, which a person accepts or rejects.
**Why:** it is the work done by digital transformation, business analysis and automation teams (and by process
mining tools like Celonis), and it ties to the first project: "invoices typed in by hand from SEF" is one of the
findings.

## D1. Process: purchase-to-pay at a fictional Serbian distributor
P2P is the most common process-mining use case, every company has it, and it connects to SEF e-invoices, three-way
matching and early-payment discounts that finance people understand immediately. The company (Panonija Trade d.o.o.,
Novi Sad) and all names are fictional.

## D2. Synthetic logs from a queue simulator, with planted problems
**Options:** (a) a public real log (BPI Challenge 2019, P2P of a large company), (b) random synthetic events,
(c) a discrete-event simulator with people, working hours, approval windows and payment runs.
**Chosen:** (c). Waiting times are not drawn from a distribution; they come out of queues and calendars, like in a
company. Each of 8 problems can be switched on or off, so we know the right answer and can measure detection.
The same simulator re-runs a quarter with a fix applied (what-if), which checks the savings estimates.
**Trade-off:** results are on synthetic data and the same builder wrote the simulator and the detectors (see D9).
Real logs load through CSV/XES + a profile (D5); the BPI 2019 log is the natural next test.

## D3. pm4py for discovery and conformance; own pandas code for metrics
pm4py (Inductive Miner, token-based replay, BPMN export) is the standard open-source process-mining library.
Waiting times, rework, resource workload and the automation ranking are plain pandas, because they need business
definitions (what counts as manual, what is vendor lead time) that a library does not know.
**License note:** pm4py is AGPL-3.0. This repository's own code is MIT; anyone shipping a closed-source product with
it must replace `discovery.py` or buy pm4py's commercial license.

## D4. Rule-based detectors with thresholds written in code, not LLM judgement
Each of the 8 detectors measures one number and compares it with a threshold in `findings.THRESHOLDS`
(e.g. "more than 40% of SEF invoices typed by hand"). A finding always carries its numbers. **Why:** a CFO must be
able to ask "why is this a problem?" and get a number and a rule, the same answer every time.

## D5. A profile maps a company's activity names to meanings
The generic analysis (map, variants, waits, rework, automation ranking) works on any log. The P2P detectors and
savings levers need to know which activity is "invoice entry" or "payment"; that mapping is a JSON profile, so a
real ERP export can be analysed without changing code.

## D6. Savings: estimated from the log, checked by re-simulation
Each lever (SEF→ERP integration, automatic matching, daily payment run, approval escalation, duplicate check,
price catalog sync, framework contracts, "no PO, no pay") has an estimator that reads the log: manual minutes that
disappear, discounts that would have been captured. Process levers whose effect depends on people use an explicit
assumed reduction and are labelled `assumed`. Implementation costs are rough assumptions, used for payback only.
**Why the what-if check:** an estimate that ignores queues and working hours can be badly wrong. The first version
of the approval-escalation estimate assumed the deputy reacts in ~1 hour and was off by about 40% against the
simulation (the 24-hour limit often expires at night or on a weekend). Version 2 uses the hours of the week in
which approvers actually work, taken from the log; the error dropped to about 2%.

## D7. The LLM never computes or approves (same principle as the earlier projects)
The AI advisor (Gemini or Claude, one setting) calls tools: overview, findings, activity statistics, lever
estimates, package sums, what-if simulation, and `predlozi_preporuku`, which creates a PENDING recommendation with
the system's own estimate attached. It writes the report in Serbian. Only a named person can accept or reject a
recommendation (names starting with "agent" are refused); everything goes to a hash-chained audit log.

## D8. Number grounding check on the AI report
Every number in the advisor's text is extracted (Serbian formats: 1.251.000, 15,4%, 1,3 miliona) and looked up in
the tool results, allowing for rounding. Numbers that cannot be found are highlighted in the dashboard and counted
in the evaluation. **Why:** "the model said it saves 2 million" is not acceptable in a finance report.

## D9. Honest evaluation
The evaluation reports detection on clear, mild (near-threshold), noisy and no-start-time logs, estimate vs
simulation per lever, and (with a key) the AI advisor's coverage of planted problems, extra recommendations and
grounding. Two changes were made after the first evaluation run, both on the same synthetic data, so they are
tuning and are documented as such:
- the approval bottleneck threshold was lowered from 16 to 8 working hours of median waiting (one working day),
  and waiting before an approval is now measured from its defined predecessor (requisition created, PO changed),
  because ±30 minute timestamp noise reordered the approval and the PO created minutes later (recall on noisy logs
  went from 25% to 100%);
- a case with a requisition but no PO event counts as a missing event, not as buying without a PO (this removed
  5 false alarms on noisy logs).

## D10. Plain-JS dashboard with vendored libraries
Cytoscape.js + dagre for the process map, marked for the report, no build step, libraries vendored in `web/vendor`
so it runs on an internal network without CDN access.
