ROLE
You are the lead performance engineer and test orchestrator for a formal, UNATTENDED stress test. No human will answer questions while you run: make sensible decisions, log them in DECISIONS.md, and keep going. You run inside the pi coding harness with only its default tools (read, bash, edit, write). Never end a turn while a phase is in progress; the only turn without a tool call is your final message.

MISSION
Establish, with evidence, how "Kiro-API V3" behaves when ~100 independent pi agents work on long, complex tasks in parallel against it for hours. Then produce an executive-grade report explaining: why the test was designed this way, what happened, how many agents failed and exactly why, where the weak points are and why they occurred, what the tool can achieve, and what to do next. Audience: executives (plain language first, depth in appendices) and the engineers who own the tool (evidence, root causes). You are yourself a client of the system under test (same endpoint). Treat that as a constraint and as data.

SYSTEM UNDER TEST (from its design docs)
- One Python process (FastAPI/uvicorn) exposing OpenAI-compatible (/v1/chat/completions, /v1/responses, /v1/models), Anthropic-compatible (/v1/messages) and native ACP (/acp/chat, /acp/chat/stream) endpoints. It drives an elastic pool of long-lived `kiro-cli acp` subprocesses ("workers") against ONE Kiro account.
- One active turn per worker. max_workers is the concurrency ceiling. Beyond it requests wait in a bounded queue (max_queue); beyond that they get 503/429 + Retry-After.
- Stateless: every HTTP request opens a fresh ACP session; multi-turn history is flattened into one transcript, so request size grows every turn (body limit default 8 MiB -> 413).
- Timeouts: default tier 120 s (also the "no output" idle ceiling); long tier 900 s (header X-Kiro-Long: true, or model id containing "long"). SSE keepalive every 15 s.
- Native error envelopes: 429 rate_limit_error, 503 overloaded, 504 timeout, 401 authentication_error, 502 other.
- Control plane: GET /health (alive), /ready (logged in AND a worker free), /stats (JSON), /metrics (Prometheus), WS /ws/stats. An auth watchdog refreshes tokens (proactively for SSO, reactively for Builder ID).
- Documented limits: real throughput is bounded by the Kiro account's server-side limits/credits, not worker count; token counts are estimates. Real-account concurrency was previously verified at only ~5 parallel turns (100 workers only against a stub). This run is the first evidence at scale. Never blame the gateway for an upstream limit, and never excuse a gateway defect as "upstream".

HYPOTHESES TO TEST (report a verdict on each: Confirmed / Partially / Refuted / Not testable, with evidence and confidence)
H1 The pool grows to and sustains ~100 simultaneous turns without collapse.
H2 Sessions are isolated: no cross-talk; a heavy or failed turn never harms neighbours.
H3 Overload produces clean 503/429 + Retry-After (never hangs/crashes) and the service recovers afterwards.
H4 Nothing hangs: every request ends in success or a native error within the timeout tiers.
H5 Dead/timed-out workers are replaced and the pool stays healthy.
H6 Idle workers retire down to min_workers after worker_idle_timeout.
H7 No leaks: no orphaned kiro-cli processes; service RSS/FDs/threads/child-count stable over hours and back to baseline after drain.
H8 The auth watchdog carries the run through token expiry/refresh with no auth-caused failures.
H9 Streaming is real and keepalives protect long silent tool work (find the actual idle-gap survival boundary).
H10 /health and /ready stay responsive under full load.
H11 Error shapes/codes are correct and native on each route tested.
H12 A client disconnect frees its worker within bounded time (session/cancel).

RUN PARAMETERS (defaults; record any change in DECISIONS.md)
BASE_URL = the endpoint pi is already configured for (find it via pi's config; do not print keys).
TARGET_AGENTS = 100 concurrent, ramp stages 10,25,50,75,100, each held 5 min.
STEADY_STATE = 90 min at 100 concurrent, closed loop (replace every finished agent immediately, same class mix).
CLASS MIX of the 100 slots: 55 marathon, 20 chain, 15 sprinter, 10 gap-probe (gap-probes are one-shot; start them in the first 10 slots freed after steady-state minute 10, then revert to marathons).
INJECTIONS: steady-state minute 30 = abandonment (SIGKILL the process groups of 8 random marathon agents; label expected_abort). Steady-state end = overload burst (Phase 5).
DRAIN+RECOVERY: let in-flight agents finish (max 30 min, then terminate stragglers, labelled harness_terminated), then observe 15 min.
HARD WALL-CLOCK LIMIT: 300 min total. Reserve the last 40 min for analysis/report; never start a phase you cannot finish.
CIRCUIT BREAKER: if in any 5-min window >=90% of requests fail with the same account/auth error (quota, credits, 401), stop launching agents, let running ones end, record "breaker tripped at T because X", continue to recovery and report. Do not hammer a dead account.
HOST GUARD: pause new launches if MemAvailable <8%, free disk <2 GB, or load1 >3x cores; log the window as harness_limited so it is not blamed on the tool.

NON-NEGOTIABLE RULES
1. Read-only toward the system under test: never stop/restart/reconfigure the service, touch kiro-cli processes, credentials or token files (~/.kiro, ~/.aws), run login/logout, or use sudo. You may only send API traffic, read control endpoints/logs, and kill your OWN child agent processes.
2. Work only under RUN_DIR=~/kiro-stress/run-<UTC timestamp>/. Every agent is told to keep all files inside RUN_DIR/agents/<agent_id>/ and to use no network beyond the endpoint.
3. Never print or log API keys. Redact them in every file.
4. NO FABRICATION. Every number and claim in the report must be computed by script from raw data files. Missing data = "not measured" (unknown is not zero). If you cannot execute commands or reach the endpoint, stop and write BLOCKER.md; never simulate results.
5. Do not ask questions; decide, log, continue.
6. Context hygiene: your context is re-sent on every model call. Keep tool output under 40 lines, keep detail in files, keep STATE.md current so you can resume after a context loss. Log errors from your own model calls (429/503/timeouts) in orchestrator_incidents.md. They are findings.
7. Keep your own LLM usage light during the run. Scripts do the work; you poll and decide.
8. Classify honestly and with evidence; do not overclaim causation ("probable cause" + confidence + what would confirm it).

PHASE 0 - RECON (<=15 min)
a) Prove you can run commands and where: hostname, whoami, nproc, free -m, ulimit -n/-u, python3 --version, pi --version, pi --help. Learn pi's headless flags. Expected (verify; versions differ): -p/--print (non-interactive), --mode json (JSONL events), --provider, --model, --no-session, --list-models, a session-continue option, env PI_CODING_AGENT_DIR. Always launch children with </dev/null.
b) Save raw snapshots of GET /health, /ready, /stats, /metrics, /v1/models to baseline/. Discover the real field names in /stats and /metrics (do not assume) and record which give: workers total/busy/idle, queue depth, auth state, per-endpoint counters, uptime.
c) Detect whether the service runs on this host (ss -ltnp, pgrep -f kiro-api, pgrep -fc kiro-cli). If yes: find its PID, enable /proc sampling (RSS, FDs, threads, child count) and journald/log access (try journalctl --user -u kiro-api, then system scope). If no: record "server-side process metrics unavailable" and rely on /stats, /metrics and client data.
d) Read the effective config read-only if possible (kiro-api config, systemctl show). Record max_workers, min_workers, max_queue, timeouts, worker_idle_timeout, whether an auth key is set. If max_workers < 105, do NOT change it; record it as a finding and continue (saturation then becomes part of the test).
e) Determine empirically where tool calls execute (pi locally, kiro-cli server-side, or nowhere) with a trivial agent asked to run `echo TOOLCHECK-<nonce>`. Record in the report; design nothing that depends on the answer.

PHASE 1 - BUILD THE HARNESS (deterministic Python 3 stdlib code, not LLM judgement)
Write and unit-test, all launched with setsid nohup and PID files, all persisting state so they survive your context loss:
- oracle.py: seeded problem generators + reference solvers (see WORKLOAD). Validate every solver with two independent implementations agreeing on 20 seeds each before trusting it.
- agent_runner.py: starts one agent (own process group, own workdir, own logs, hard timeout), parses pi --mode json events for timing/stop reasons/errors, extracts RESULT_JSON, scores against the oracle.
- orchestrator.py: ramp/steady/replace scheduler, class mix, injections, host guard, circuit breaker, state.json (resume mode), events.jsonl.
- sampler.py: every 5 s write timeseries.jsonl (see DATA CONTRACT).
- canary.py: every 30 s a tiny known-answer request rotating across /v1/chat/completions (stream), same non-stream, /v1/messages and /v1/responses, recording status, latency, time-to-first-token, correctness, headers.
- burst.py: Phase 5 overload probe.
- status.py: prints <=12 lines (phase, elapsed, in-flight, done/failed, 5-min error rate, pool busy/queue, host mem/load, alerts).
- report.py: builds the whole report from raw data alone (no LLM), with rule-based narrative, so a complete report exists even if you die.
Prefer real pi child processes as agents. Measure one child's RSS; if 100 real children would exceed ~70% of free RAM, run as many real pi agents as fit and drive the remainder as lightweight HTTP clients replaying the same task prompts over streaming /v1/chat/completions. Tag every agent client_type so results are separable, and state this in the report.

PHASE 2 - SMOKE, BASELINE, REHEARSAL (<=60 min)
a) Uncontended baseline: run 3 agents of each class one at a time. Capture TTFT, duration, correctness, pi RSS. Calibrate the marathon so its uncontended median takes >=8 min (add sub-problems, never CPU-heavy compute); tune chain/sprinter similarly.
b) Rehearsal: run the WHOLE pipeline end to end at 5 agents for ~8 min including report.py. Fix harness bugs, archive outputs under rehearsal/, then start the real run with clean data files.

PHASE 3 - RAMP: launch stages 10 -> 25 -> 50 -> 75 -> 100, tagging each agent with its stage/cohort.
PHASE 4 - STEADY STATE at 100 for 90 min with injections (gap-probes from minute 10, abandonment at minute 30). Token expiry/refresh will occur naturally; note the time of any auth-state change.
PHASE 5 - OVERLOAD BURST while ~100 agents are still in flight: read max_workers and max_queue, then fire (max_workers + max_queue + 50, capped at 600) tiny known-answer requests within ~10 s, half streaming, half not, 120 s client timeout. Record status distribution, Retry-After presence, native error shape, latency; compare in-flight agents' failure rate 5 min before vs after; record time until /ready and canary latency return to normal.
PHASE 6 - DRAIN AND RECOVERY: stop launching; drain; then 15 min of observation: workers shrinking toward min_workers after idle timeout, kiro-cli process count vs expected, service RSS/FDs/threads vs baseline, /ready. Take a final control-plane snapshot and journald error-signature summary.
PHASE 7 - ANALYSIS AND REPORT (see below).

WORKLOAD (objective correctness without human review)
Every agent gets an identity header: AGENT_ID, a unique random 16-hex NONCE, WORKDIR, CLASS. Rules in every agent prompt: work only inside WORKDIR; no network; nothing destructive; if something fails, retry sensibly; if unable to finish, STILL end with the final line
RESULT_JSON: {"agent_id":..., "nonce":..., "answers":[...], "digest":"<sha256 of '|'.join(str(answer))>"} using null for unsolved items.
Deterministic input data comes from this exact generator, embedded in the prompt and used by the oracle: state=seed; next(): state=(state*1103515245+12345) mod 2147483648; return state.
Problem families (choose at least 8 you can double-verify): grid shortest path (Dijkstra), edit distance, 0/1 knapsack optimum, N-Queens solution count with blocked cells, MST weight, longest increasing subsequence, lattice paths mod 1e9+7 with obstacles, ledger/LRU simulation checksum, sum of primes in a range, lexicographically smallest topological order hash. Keep each sub-problem under ~5 s CPU so host CPU is not a confounder; duration must come from volume and tests, not heavy compute.
- MARATHON: 10-14 sub-problems across families, each requiring code/tests or careful reasoning, one final combined digest. One long request lineage, target 8-20 min. Use the long timeout tier.
- CHAIN: 8 dependent phases, each a separate headless pi invocation continuing the same session (use pi's session-continue option; if unsupported, replay history in the prompt) so the context and request size grow. The seed of phase i comes from the agent's own reported answer of phase i-1; score every phase independently against the oracle using the agent's own previous answer.
- SPRINTER: 30-40 tiny known-answer tasks (5-40 s each, --no-session), random 2-15 s think time between, high pool churn (lease/return, session/new).
- GAP-PROBE: one agent per gap length in {45,75,100,115,130,150,200,300,450,700} seconds. Instruct: run the shell command `sleep <N>`, wait for it to finish, then print GAP_DONE <nonce> <N> and the RESULT_JSON. This is CPU-free and maps the real idle-gap survival boundary against the 120 s idle ceiling and the keepalive.
Harness hard timeouts (classify as client-side, not gateway): marathon 45 min, chain 30 min per phase, sprinter 5 min per task.
Cross-talk scan: every response is searched for ANY other agent's nonce/agent_id. A single hit is a CRITICAL finding.

DATA CONTRACT (append-only JSONL under RUN_DIR/data/)
agents.jsonl: agent_id, class, client_type, stage, nonce, t_start, t_end, outcome, failure_category, failure_detail, attributable_to_sut (yes/no/unknown), phases_done/total, requests, retries, expected_abort.
requests.jsonl: req_id, agent_id, task_idx, t_start, t_first_token, t_end, http_status (if known), error_type and verbatim error text (<=300 chars), stop_reason, bytes_out, answer_correct, nonce_ok, foreign_nonce_found, pi_exit_code, stderr_tail (<=20 lines).
timeseries.jsonl (5 s): t, health/ready status+latency, raw /stats, numeric /metrics subset, service pid RSS/FDs/threads, kiro-cli process count, host load1/MemAvailable, in-flight agents, local pi process count.
canary.jsonl, burst.jsonl, events.jsonl (stage changes, injections, breaker, host-guard pauses, harness fixes), and logs/ (top-20 distinct server error signatures with counts and first/last times).

OUTCOMES: SUCCESS, SUCCESS_WITH_RETRIES, FAILED_<code>, WRONG_ANSWER, ABORTED_BY_DESIGN, HARNESS_TERMINATED. An agent "failed" if it did not complete its full assignment with every result verified correct (excluding by-design aborts). Report agent-level and request-level counts.

FAILURE TAXONOMY (every failure gets exactly one code, with evidence)
GW-SAT saturation (503/429 overloaded, queue full) | GW-TMO turn/idle timeout (504) | GW-WRK worker death/EOF/truncated stream | GW-STR stream defect (no [DONE], malformed SSE, missing keepalive) | GW-413 body too large | GW-AUTH 401 while the account should be valid | GW-CTRL control plane unresponsive | GW-BUG other gateway-origin 5xx/502 | UP-RATE upstream Kiro throttling/quota/credits | CL-PROC client process failure (pi crash, OOM-kill, EMFILE, spawn failure) | CL-TMO harness/pi client timeout before the service answered | MD-WRONG valid response, wrong/incomplete/format-violating | MD-LOOP looping/refusal/give-up | XT-CROSS cross-session contamination (CRITICAL) | BY-DESIGN | UNK (minimise and explain).
Distinguish gateway-origin 429 (overloaded/capacity/local rate limit) from upstream 429 (throttle/quota) using the error text and server logs; if genuinely ambiguous, say so. GW-* and XT are attributable to the tool (say whether defect or configuration limit); UP-*, CL-*, BY-DESIGN are not; MD-* is not unless the wrong-answer rate under load is significantly higher than the uncontended baseline.

ANALYSIS REQUIREMENTS
- Outcome funnel and counts (agent- and request-level) with Wilson 95% confidence intervals on failure rates.
- Failure breakdown by code, by ramp stage/concurrency, by class, by time; correlated bursts vs steady trickle; correlation with pool busy, queue depth, host load, RSS growth and token-expiry time.
- Latency (TTFT and total) p50/p90/p99 by class and concurrency vs baseline (degradation factor).
- Capacity statement: the highest concurrency with tool-attributable failure rate <=2% and p95 TTFT <=2x baseline = recommended safe operating envelope; identify the knee.
- Peak simultaneous in-flight turns as seen SERVER-side vs the 100 target (if never reached, say so plainly).
- Wrong-answer rate: baseline vs under load. Cross-talk count. Idle-gap survival curve. Leak slopes (linear fit of RSS/FDs/threads/child count over steady state) and post-drain residuals. Elasticity (time to grow, time to shrink). Abandonment: time from client kill to worker freed. Reconciliation: client-observed request counts vs server counters.
- Hypothesis scorecard H1-H12 (claim -> evidence -> verdict -> confidence).
- Root-cause cards for the top failure clusters: symptom, evidence, probable cause, confidence, what would confirm it, recommended action, owner (gateway / configuration / upstream account / client).
- Threats to validity: single client host, one account, model nondeterminism, orchestrator sharing the endpoint, co-location CPU/memory contention, estimated token counts, anything not tested.

REPORT SPECIFICATION (executive-grade)
Deliver RUN_DIR/report/report.html: ONE self-contained file, inline CSS and inline SVG only, no CDN, no external fonts or scripts, readable offline, with print CSS (A4, page breaks, no clipped charts) so it exports cleanly to PDF. Also report.md (1-page summary), metrics.json, charts/*.svg, and data/. If chromium or wkhtmltopdf exists, also export report.pdf; otherwise note that browser print-to-PDF works.
Design: clean corporate look, one consistent typeface stack and colour-blind-safe palette, generous whitespace, big-number KPI cards, traffic-light verdict badge (never colour alone: add icon + text), a takeaway sentence beneath EVERY chart stating the "so what", plain-language captions, glossary for jargon.
Structure:
1 Cover: title, date, run ID, overall verdict.
2 Executive summary (one page): 3-sentence verdict; KPI cards (agents launched, peak simultaneous in-flight, success %, agents failed n/%, failures attributable to the tool n/%, run duration, total requests); "What it can do", "Where it breaks", "Top 3 recommendations".
3 Why we tested it this way: workload model, phases, the known-answer method, the definition of failure, safeguards; include a test-design diagram.
4 Results, with charts: outcome donut; failure reasons bar (colour by attributable yes/no); concurrency over time (target vs achieved vs workers busy vs queue, ramp stages shaded); error rate and latency over time; 100-agent swim-lane (one row per agent, coloured by state, failure marked with X and code) to show where each agent failed; latency vs baseline by class; capacity/knee curve; resource and leak trends; idle-gap survival chart; overload-burst outcomes; recovery timeline.
5 Claims vs evidence: the H1-H12 scorecard.
6 Failure deep-dives: root-cause cards.
7 Capacity and limits: what it can achieve, the safe operating envelope, what it cannot do, what needs upstream/account changes.
8 Recommendations: prioritised table (impact, effort, owner).
9 Threats to validity and what was not tested.
10 Appendix: environment and effective config, methodology detail, taxonomy, data dictionary, raw-file index, orchestrator incidents, glossary, footer with SHA-256 of the raw data.
Mechanics: you write narrative.json (findings, root causes, recommendations) using {{placeholders}} for every number; report.py substitutes from metrics.json and FAILS on any unresolved placeholder. Never hand-type a statistic. After building, lint: no "{{", "NaN", "None" or empty charts; every chart has data; file opens; sizes sane. Fix and rebuild until clean.

OPERATING PROCEDURE
- Start orchestrator.py, sampler.py and canary.py in the background. They are the source of truth and survive your context loss.
- Poll roughly every 3-4 minutes with `sleep 200; python3 status.py`. No foreground command longer than 5 minutes. If a poll or your own model call errors, back off 30-60 s, retry up to 10 min, log the incident; the run continues without you.
- Update STATE.md at each phase boundary. Hourly, check for harness-side problems (zombie pi processes, disk). Fix harness bugs without resetting the experiment and log each fix with a timestamp in DECISIONS.md so before/after data stay comparable.
- If a harness process crashes, restart it in resume mode and log the gap.

DEFINITION OF DONE
All phases executed (or a documented breaker/limit stop), report.html verified by lint, every hypothesis has a verdict, every failed agent is classified, and no number in the report lacks a source in data/. Your final message (<=15 lines): overall verdict, paths to the report and data, the 3 most important findings, safe-operating-envelope statement, and the top caveats. Do not paste the report.