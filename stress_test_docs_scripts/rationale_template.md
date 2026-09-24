<!--
TEMPLATE NOTE (delete this comment before distribution)
Every double-brace placeholder in this document is produced by kiro_env_report.py.
  1. Run:   python3 kiro_env_report.py --run-dir <RUN_DIR> --prepared-by "Name, Role" --reviewed-by Pending --status Draft --fill rationale_template.md
  2. The script writes rationale_template.filled.md with every detected value inserted.
  3. Anything still shown in double braces could not be detected on that host. Enter it by hand or re-run with --merge.
-->

# Kiro-API V3: Stress Test Rationale and Methodology

## Document Control

| Field | Value |
|---|---|
| Document type | Test rationale and methodology |
| System under test | Kiro-API V3, a custom multi-session gateway with an elastic pool of ACP workers. Package version {{KIRO_API_PACKAGE_VERSION}}; source revision {{KIRO_API_COMMIT}} on branch {{KIRO_API_BRANCH}}; working tree: {{KIRO_API_TREE_STATE}} |
| Kiro CLI | {{KIRO_CLI_VERSION}} |
| Test harness | pi coding agent {{PI_VERSION}} on Node.js {{NODE_VERSION}}, default tools only |
| Run ID | {{RUN_ID}} |
| Test window (UTC) | {{TEST_START_UTC}} to {{TEST_END_UTC}} |
| Test environment | {{HOST_ROLE}}. Host {{HOSTNAME}}: {{OS_NAME}}, {{CPU_CORES}} logical cores, {{RAM_TOTAL_GB}} GB RAM (full detail in Section 12) |
| Prepared by | {{PREPARED_BY}} |
| Reviewed by | {{REVIEWED_BY}} |
| Status | {{DOC_STATUS}} |
| Companion document | Stress Test Results Report, same Run ID |

---

## 1. Purpose

This document explains **what was tested, how, and why the test was designed this way**. It is intended to let a reader judge whether the results in the companion Results Report are credible, and to make the test repeatable.

> **In one sentence:** Kiro-API V3 was subjected to approximately 100 concurrent, long-running AI agents for several hours, using tasks with independently verifiable answers, to establish where it holds, where it breaks, and why.

---

## 2. System Under Test

Kiro-API V3 is a locally hosted gateway that exposes OpenAI-compatible, Anthropic-compatible and native ACP HTTP endpoints, and fulfils requests by driving an elastic pool of `kiro-cli acp` subprocesses ("workers") against a single Kiro account.

| Design characteristic | Implication for testing |
|---|---|
| Single Python process (one event loop) fronting a pool of subprocesses | Control-plane starvation, pool sizing and subprocess lifecycle are the primary risk areas. |
| One active turn per worker; `max_workers` is the concurrency ceiling | Concurrency beyond the ceiling must queue or be rejected. |
| Bounded queue (`max_queue`), then `503`/`429` with `Retry-After` | Backpressure behaviour must be tested deliberately, not assumed. |
| Stateless: each request opens a fresh session; history is flattened into one transcript | Request size grows every turn (default body limit 8 MiB), which affects multi-turn agents. |
| Default turn and idle timeout 120 s; long tier 900 s | Long-running work can be cut off by configuration, independent of any defect. |
| Self-healing auth watchdog and worker replacement | Multi-hour duration is required to observe token expiry and worker churn. |
| One Kiro account shared by all workers | Upstream account limits, not worker count, are expected to bound real throughput. |

**Documented state of prior verification.** Per the project documentation, real-account concurrency was previously verified at approximately five parallel turns, and the 100-worker ceiling was verified against a stub. This test is therefore the first evidence at scale against a real account.

---

## 3. Objectives and Interpretation Thresholds

### Objectives

1. Determine whether the pool grows to, and sustains, approximately 100 simultaneous turns.
2. Quantify how many agents fail, and classify every failure by cause.
3. Separate failures attributable to the tool from those caused by the upstream account, the test client, or the AI model.
4. Identify the concurrency level at which behaviour degrades (the "knee").
5. Verify the design's reliability claims (Section 9) against measured evidence.
6. State what the tool can achieve and where its limits are, in terms suitable for executive decision-making.

### Interpretation thresholds

The project documentation defines no service-level objectives. The following are **analyst-defined reference thresholds** used to make results interpretable. They are not pass/fail gates from the tool's specification.

| Measure | Reference threshold |
|---|---|
| Safe operating envelope | Highest concurrency at which tool-attributable failures are 2% or less **and** p95 time-to-first-token is 2x the uncontended baseline or less |
| Session isolation | Zero cross-session contamination events (any single event is a critical finding) |
| Resource stability | No statistically meaningful growth in memory, file descriptors, threads or child processes over the steady state; return to baseline after drain |
| Process hygiene | Zero orphaned `kiro-cli` processes after drain (baseline count recorded in Section 12.5) |

---

## 4. Design Principles

| Principle | What was done | Why |
|---|---|---|
| Test the architecture's actual risks | Each phase targets a specific failure zone identified in the design documents | Generic load does not prove the claims that matter |
| Model agents as agents | Real pi processes issue streaming, multi-turn requests with retries | Fixed-payload HTTP load tools do not reproduce long streams, growing context or client retry behaviour |
| Closed-loop load | Finished agents are replaced immediately to hold concurrency at target | Agent fleets wait for responses; they do not fire at a fixed rate. Open-loop load is used only for the overload burst |
| Objective correctness | Every task has a single canonical answer computed independently | Removes subjective judging and enables automated scoring at scale |
| Evidence-based attribution | Every failure receives one classification code with supporting evidence | Answers the executive question "is it the tool's fault?" |
| Scripts own the data | Raw data is written by deterministic code; reports are generated from it | Prevents unmeasured or hallucinated figures |
| Do no harm | The test is read-only toward the service and stops if the account is exhausted | Protects the system, the account and the validity of results |

---

## 5. Test Phases

Total planned duration is approximately 4 to 5 hours, with a hard wall-clock limit of 300 minutes.

| Phase | Activity | Risk targeted | Evidence produced |
|---|---|---|---|
| 0. Reconnaissance | Confirm execution environment; snapshot control endpoints; record effective configuration; detect whether tool calls execute locally, server-side or not at all | Invalid or misconfigured test | Environment record, configuration record |
| 1. Harness build | Build oracle, agent runner, orchestrator, sampler, canary, burst probe, status and report generator; validate the oracle with two independent implementations per problem | Test-tool defects being mistaken for product defects | Verified harness |
| 2. Baseline and rehearsal | Uncontended baseline (3 agents per class, sequential); full end-to-end rehearsal at 5 agents including report generation | No reference point; pipeline bugs discovered late | Baseline latency and correctness; rehearsal archive |
| 3. Ramp | Stages of 10, 25, 50, 75 and 100 concurrent agents, 5 minutes each | Where degradation begins | Knee analysis, per-stage failure and latency |
| 4. Steady state | 90 minutes at 100 concurrent agents with agent replacement, plus injections: idle-gap probes from minute 10; client abandonment at minute 30 | Leaks, token expiry, worker churn, idle-timeout limits, cancel handling | Time-series trends, gap survival curve, abandonment recovery |
| 5. Overload burst | While about 100 agents remain in flight, fire `max_workers + max_queue + 50` short requests (cap 600) within about 10 s, half streaming | Backpressure, native error shape, collateral damage | Status distribution, `Retry-After` presence, effect on running agents |
| 6. Drain and recovery | Stop launching, drain in-flight work (max 30 min), then observe 15 min | Elasticity, cleanup, residual leaks | Pool shrink timing, orphan count, resource return to baseline |
| 7. Analysis and report | Compute metrics, classify failures, score hypotheses, generate report | Unsupported conclusions | Executive report, raw data, metrics file |

### Default run parameters

| Parameter | Default |
|---|---|
| Concurrent agents | 100 |
| Ramp stages | 10, 25, 50, 75, 100 (5 minutes each) |
| Steady state | 90 minutes |
| Class mix of slots | 55 marathon, 20 chain, 15 sprinter, 10 gap-probe |
| Abandonment injection | 8 random marathon agents, steady-state minute 30 |
| Drain and recovery | up to 30 minutes drain, then 15 minutes observation |
| Circuit breaker | Stop launching if at least 90% of requests in a 5-minute window fail with the same account or auth error |
| Host guard | Pause launches if available memory below 8%, free disk below 2 GB, or load average above 3x core count |

---

## 6. Workload Model

The workload mixes long and short sessions, matching the stated production profile of the tool.

| Class | Share | Behaviour | What it stresses |
|---|---|---|---|
| **Marathon** | 55% | 10 to 14 sub-problems in one long request lineage; target 8 to 20 minutes; long timeout tier | Sustained worker occupancy, long streams, turn-timeout limits |
| **Chain** | 20% | 8 dependent phases across separate invocations, session continued between them | Transcript growth, request-size limits, per-turn latency growth |
| **Sprinter** | 15% | 30 to 40 short tasks with random think time | Lease and return churn, session creation overhead, scheduler fairness |
| **Gap-probe** | 10 agents (one-shot) | Runs a silent wait of 45, 75, 100, 115, 130, 150, 200, 300, 450 or 700 seconds, then reports | The true idle-gap survival boundary versus the 120 s idle ceiling and 15 s keepalive |

**Design constraints on tasks**

- Each sub-problem is bounded to about 5 seconds of CPU so that host CPU is not a confounding factor; duration comes from volume of work, not heavy computation.
- The idle-gap probe uses a CPU-free wait for the same reason.
- Input data comes from a fixed, language-independent linear congruential generator, so agent and oracle receive identical inputs.
- All agent files are confined to per-agent directories to prevent cross-agent file collisions from being misread as failures.

Agents are real headless pi processes. If host memory cannot support 100 of them, the remainder are lightweight HTTP clients replaying the same prompts. Every agent is tagged with its client type so results can be separated, and any use of the fallback is disclosed in the report.

---

## 7. Correctness and Integrity Method

| Control | Description | Purpose |
|---|---|---|
| Known-answer oracle | Problems from at least eight algorithmic families with a single canonical answer; the harness computes the expected result independently | Objective, reproducible scoring |
| Oracle double-verification | Each solver is cross-checked against a second independent implementation on 20 seeds before use | Prevents a faulty oracle producing false failures |
| Per-agent nonce | Each agent receives a unique random identifier that must be echoed in its result | Detects mismatched or misrouted responses |
| Cross-talk scan | Every response is searched for any other agent's nonce or identifier | Detects cross-session contamination, the most serious defect class for a multi-agent gateway |
| Uncontended baseline | Same tasks run one at a time before the load test | Separates model capability from load-induced corruption |
| Load versus baseline comparison | Wrong-answer rate under load compared with baseline | A higher rate under load implicates the tool; an equal rate implicates the model |

---

## 8. Failure Classification and Attribution

Every failure receives exactly one code, with recorded evidence (HTTP status, error text, timing, log signature).

| Code | Meaning | Attributable to tool? |
|---|---|---|
| GW-SAT | Gateway saturation (`503`/`429` overloaded, queue full) | Yes (configuration limit or defect; stated which) |
| GW-TMO | Turn or idle timeout (`504`) | Yes (configuration limit or defect; stated which) |
| GW-WRK | Worker death, EOF or truncated stream | Yes |
| GW-STR | Stream defect (no terminator, malformed frames, missing keepalive) | Yes |
| GW-413 | Request body too large | Yes (configuration limit) |
| GW-AUTH | `401` while the account should be valid | Yes |
| GW-CTRL | Control plane unresponsive | Yes |
| GW-BUG | Other gateway-origin `5xx` or `502` | Yes |
| UP-RATE | Upstream Kiro throttling, quota or credits | No (capacity ceiling) |
| CL-PROC | Test client process failure (crash, OOM-kill, descriptor exhaustion) | No |
| CL-TMO | Client-side timeout before the service responded | No |
| MD-WRONG | Valid response, wrong or malformed answer | No, unless the rate under load significantly exceeds baseline |
| MD-LOOP | Looping, refusal or giving up | No, same qualification |
| XT-CROSS | Cross-session contamination | Yes (critical) |
| BY-DESIGN | Deliberate abort (abandonment test) | No |
| UNK | Unclassified | Unknown (minimised and explained) |

Gateway-origin `429` (capacity, local rate limit) is distinguished from upstream `429` (throttling, quota) using error text and server logs. Where genuinely ambiguous, the report says so.

**Outcomes:** SUCCESS, SUCCESS_WITH_RETRIES, FAILED_(code), WRONG_ANSWER, ABORTED_BY_DESIGN, HARNESS_TERMINATED. An agent is counted as failed if it did not complete its full assignment with every result verified correct, excluding deliberate aborts. Results are reported at both agent level and request level, with 95% Wilson confidence intervals on failure rates.

---

## 9. Hypotheses Under Test

These derive directly from claims in the project's architecture and design documents. Each receives a verdict of Confirmed, Partially confirmed, Refuted or Not testable, with evidence and a confidence level.

| ID | Claim being tested |
|---|---|
| H1 | The pool grows to and sustains approximately 100 simultaneous turns without collapse |
| H2 | Sessions are isolated; a heavy or failed turn never harms neighbours |
| H3 | Overload yields clean `503`/`429` with `Retry-After`, never hangs or crashes, and the service recovers |
| H4 | Nothing hangs; every request ends in success or a native error within the timeout tiers |
| H5 | Dead or timed-out workers are replaced and the pool stays healthy |
| H6 | Idle workers retire down to `min_workers` after the idle timeout |
| H7 | No leaks: no orphaned processes; stable memory, descriptors, threads and child counts; return to baseline after drain |
| H8 | The auth watchdog carries the run through token expiry and refresh without auth-caused failures |
| H9 | Streaming is real, and keepalives protect long silent tool work (actual survival boundary measured) |
| H10 | `/health` and `/ready` remain responsive under full load |
| H11 | Error shapes and status codes are correct and native on each route tested |
| H12 | A client disconnect frees its worker within bounded time |

---

## 10. Safeguards

| Safeguard | Detail |
|---|---|
| Read-only toward the service | The test never stops, restarts or reconfigures the service, touches worker processes, credentials or token files, or uses elevated privileges. It sends API traffic, reads control endpoints and logs, and terminates only its own client processes |
| Workspace isolation | All test artefacts live under a single run directory; each agent is confined to its own subdirectory |
| Secret handling | API keys are never printed or logged and are redacted from all files |
| Circuit breaker | Stops launching agents if the account or authentication is failing wholesale, to avoid hammering an unavailable account |
| Host guard | Pauses launches when the test host is resource-constrained; affected windows are marked as harness-limited so they are not attributed to the tool |
| Rehearsal | The complete pipeline, including report generation, is exercised at small scale before the real run |
| Disposable environment | The test should run in a disposable VM or dedicated account, as the client harness has no permission prompts and launches over 100 processes |

---

## 11. Data Integrity and Reporting Controls

- **Separation of duties.** Deterministic scripts collect and store raw data; the report generator builds every chart and table from that data. The AI orchestrator contributes interpretation only.
- **Placeholder discipline.** Narrative text references numbers through placeholders resolved from a computed metrics file. The generator fails on any unresolved placeholder, so no statistic is hand-typed.
- **Automated lint.** Reports are checked for unresolved placeholders, invalid values, empty charts and file integrity before release.
- **Provenance.** The report footer carries a SHA-256 hash of the raw data set. The environment values in Section 12 come from a read-only collection script (Appendix C), not from manual entry.
- **Resilience.** The report generator works from raw data alone, so a complete report can be produced even if the orchestrating agent fails mid-run.
- **Missing data is not zero.** Unmeasured quantities are labelled "not measured".

---

## 12. Environment, Preconditions and Recorded Configuration

### 12.1 Preconditions

| Area | Requirement |
|---|---|
| Service | Started with sufficient headroom (approximately 120 workers for a 100-agent test); JSON logging recommended; long-tier timeout raised if 900 s would otherwise mask deeper failures |
| Client | pi installed and configured with a custom provider pointing at the `/v1` endpoint; long-tier request header set where supported |
| Host | Sized for over 100 client processes plus over 100 worker processes (indicatively 32 GB RAM and 16 cores if co-located); a separate client host is preferred |
| Account | A Kiro account whose credit and rate limits are understood, because the test can consume significant quota |

### 12.2 Test environment (recorded)

| Item | Value |
|---|---|
| Deployment topology | {{HOST_ROLE}} |
| Host name | {{HOSTNAME}} |
| Operating system | {{OS_NAME}}, kernel {{KERNEL_VERSION}}, architecture {{CPU_ARCH}} |
| CPU | {{CPU_CORES}} logical cores |
| Memory | {{RAM_TOTAL_GB}} GB total; {{RAM_AVAILABLE_GB}} GB available at collection time |
| Shell limits | Open files: {{ULIMIT_NOFILE}}. Processes: {{ULIMIT_NPROC}} |
| Service manager | {{SVC_MANAGER}} |
| Service task limit (`TasksMax`) | {{SVC_TASKS_MAX}} |
| Service open-file limit | {{SVC_NOFILE_LIMIT}} |

### 12.3 Software under test (recorded)

| Component | Version or revision |
|---|---|
| Kiro-API V3 package | {{KIRO_API_PACKAGE_VERSION}} |
| Source revision | Commit {{KIRO_API_COMMIT}} on branch {{KIRO_API_BRANCH}}; working tree: {{KIRO_API_TREE_STATE}} |
| Kiro CLI | {{KIRO_CLI_VERSION}} (version verified in the project documentation: 2.23.1) |
| pi coding agent | {{PI_VERSION}} on Node.js {{NODE_VERSION}} |
| Python | {{PYTHON_VERSION}} |
| FastAPI / Uvicorn / Pydantic | {{FASTAPI_VERSION}} / {{UVICORN_VERSION}} / {{PYDANTIC_VERSION}} |

### 12.4 Service configuration (recorded)

| Setting | Documented default | Value in this run |
|---|---|---|
| Bind address | 127.0.0.1:8787 | {{SVC_BIND}} |
| Endpoint under test | n/a | {{BASE_URL}} |
| Max workers (concurrency ceiling) | 8 | {{MAX_WORKERS}} |
| Min workers | 0 | {{MIN_WORKERS}} |
| Max queue | 256 | {{MAX_QUEUE}} |
| Worker idle timeout (s) | 300 | {{WORKER_IDLE_TIMEOUT_S}} |
| Default-tier turn and idle timeout (s) | 120 | {{TIMEOUT_SHORT_S}} |
| Long-tier turn timeout (s) | 900 | {{TIMEOUT_LONG_S}} |
| SSE keepalive interval (s) | 15 | {{SSE_KEEPALIVE_S}} |
| Maximum request body | 8388608 bytes (8 MiB) | {{MAX_BODY_BYTES}} |
| Rate limit (requests per window) | 0 (off) | {{RATE_LIMIT}} |
| Default model | auto | {{DEFAULT_MODEL}} |
| Bridge auth key set | No | {{AUTH_KEY_SET}} |
| Log format | text | {{LOG_FORMAT}} |
| Reasoning effort (`KIRO_ACP_EFFORT`) | not set | {{ACP_EFFORT}} |
| Trust tools (`KIRO_ACP_TRUST_TOOLS`) | true | {{ACP_TRUST_TOOLS}} |
| Surface thinking (`KIRO_ACP_SURFACE_THINKING`) | true | {{ACP_SURFACE_THINKING}} |
| ACP engine (`KIRO_ACP_ENGINE`) | v2 | {{ACP_ENGINE}} |

These settings are **calibration inputs**: reasoning effort affects turn duration and credit consumption, and the tool-trust setting determines where tool calls execute. Compare the concurrency ceiling ({{MAX_WORKERS}} workers) with the 100-agent target. If the ceiling is below the target plus headroom, it is not changed; saturation then becomes part of the result. Where the Value column shows a documented default rather than a detected value, the Results Report must say so.

### 12.5 Service state at collection time

| Check | Result |
|---|---|
| Collected at (UTC) | {{COLLECTED_AT_UTC}} |
| Liveness (`/health`) | {{HEALTH_CHECK}} |
| Readiness (`/ready`) | {{READY_CHECK}} |
| Authentication state | {{AUTH_STATE}} |
| Pool snapshot | {{POOL_SNAPSHOT}} |
| Models exposed | {{MODELS_AVAILABLE}} |
| `kiro-cli` worker processes before the test | {{KIRO_CLI_PROCS_AT_START}} (baseline for the orphan check in hypothesis H7) |

---

## 13. Limitations and Threats to Validity

| Limitation | Effect on interpretation |
|---|---|
| Single client host and single Kiro account | Results describe this configuration; they do not generalise to multi-account or multi-host deployments |
| Model non-determinism | Some wrong answers reflect model variability; controlled by baseline comparison |
| The orchestrating agent is itself a client of the tool | Its own errors may be affected by load; they are logged and reported as data rather than excluded |
| Deployment topology: {{HOST_ROLE}} | Where clients, workers and tool executions share a host, CPU or memory contention can inflate latency and failures; host metrics are recorded and affected windows flagged |
| Token counts are estimated by the tool | Token-based metrics are indicative only |
| Tool-call passthrough behaviour is not documented | Determined empirically in Phase 0 and reported as an environment fact; the workload is valid either way |
| Single run | Run-to-run variance is not characterised; repeated runs would strengthen conclusions |
| Fault injection not performed | Worker kills, service restarts and forced token expiry are out of scope by design (see Section 14) |

---

## 14. Alternatives Considered

| Alternative | Why it was not chosen |
|---|---|
| Generic HTTP load tool with fixed payloads | Does not reproduce long streaming turns, growing multi-turn context or real client retry and timeout behaviour |
| Open-loop fixed arrival rate | Agent fleets are closed-loop; used only for the overload burst where arrival independence is the point |
| Short spike test only | Cannot reveal leaks, token expiry effects, worker churn or slow degradation |
| Human or model-judged scoring of outputs | Subjective, costly and not reproducible at this scale; a known-answer oracle is objective |
| Simulated clients only | Loses fidelity to real harness behaviour; retained only as a disclosed memory-limited fallback |
| Stub-based test | Already performed; cannot reveal upstream account limits, which are the most likely real ceiling |
| Active fault injection (kill workers, restart service, force token expiry) | Riskier and requires service-side control; recommended as a follow-on once baseline behaviour under load is known |

---

## 15. Cost and Resource Considerations

Running approximately 100 concurrent long-running turns for several hours may consume a material share of the Kiro account's credits and can trigger upstream throttling. This is by design: upstream limits are a real constraint on capacity, and the circuit breaker prevents sustained hammering of an exhausted account. Duration, stage sizes and concurrency are adjustable in the run parameters to trade thoroughness against cost.

---

## 16. Deliverables

| Deliverable | Format | Audience |
|---|---|---|
| Executive report | Self-contained HTML (print-ready to PDF; PDF exported if a renderer is available) | Executives and engineers |
| Summary | Markdown, one page | Executives |
| Computed metrics | JSON | Engineers, audit |
| Charts | SVG | Reuse in presentations |
| Raw data | JSONL (agents, requests, time series, canary, burst, events) and server log signatures | Audit, re-analysis |
| Environment record | `run_metadata.json` and `run_metadata.md` (Appendix C) | Audit, repeatability |
| Decision log and incident log | Markdown | Audit |
| Rehearsal archive | Directory | Method validation |

The report contains: cover and verdict; one-page executive summary; test rationale; results with charts (outcome breakdown, failure reasons, concurrency over time, error and latency trends, per-agent timeline, capacity curve, resource trends, idle-gap survival, overload outcomes, recovery); hypothesis scorecard; root-cause analyses; capacity and limits statement; prioritised recommendations; threats to validity; and appendices.

---

## Appendix A: Glossary

| Term | Meaning |
|---|---|
| Agent | One autonomous AI worker (a pi process, or a disclosed lightweight client) executing an assigned task |
| Worker | A `kiro-cli acp` subprocess in the gateway's pool; handles one active turn at a time |
| Turn | One request-response cycle from an agent to the gateway |
| Pool saturation | All workers busy; new requests queue or are rejected |
| Backpressure | The gateway deliberately queueing or rejecting work to protect itself |
| Time to first token (TTFT) | Delay between sending a request and receiving the first streamed output |
| Knee | The concurrency level at which failures or latency begin to rise sharply |
| Safe operating envelope | The concurrency range within which reference thresholds are met (Section 3) |
| Oracle | Independent code that computes the correct answer to each task |
| Nonce | A unique random identifier used to detect misrouted or cross-contaminated responses |
| Keepalive | Periodic no-op stream frames that stop clients aborting during silent upstream work |
| Closed-loop load | Load where a new request follows only after the previous one completes |
| Cross-talk | One agent receiving content belonging to another agent's session |

## Appendix B: Source Documents

`ARCHITECTURE.md`, `V3-DESIGN.md`, `CONFIGURATION.md`, `USER-GUIDE.md`, `DEPENDENCIES.md`

## Appendix C: How the Environment Values Were Collected

The values in Sections 12.2 to 12.5 and the Document Control table were collected by `kiro_env_report.py` (read-only, Python standard library only) at {{COLLECTED_AT_UTC}} on host {{HOSTNAME}}. The script only:

- runs version queries (`kiro-api version`, `kiro-cli --version`, `pi --version`, `node --version`) and the read-only `kiro-api config` and `git log/status` commands;
- reads process information from `/proc` (command-line flags and environment of the running service), `systemctl show` output and `/proc/meminfo`;
- sends `GET` requests to `/health`, `/ready`, `/stats` and `/v1/models`.

It never restarts or reconfigures anything, and never records secrets: a bridge auth key is reported only as "Yes" or "No". Where a value could not be detected it is marked as not found or as an unconfirmed documented default, and the full per-value provenance (source of each value) is preserved in `run_metadata.md` and `run_metadata.json`.