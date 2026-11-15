# Harbor documentation

Start with the root [README](../README.md) for what Harbor is and how to run it.

## System reference

- [technical-overview.md](technical-overview.md) — how Harbor is built, in one
  page: tools, assistant layer, memory, safety, models and engines, running
  from source, layout and state on disk (moved here from the root README).
- [HARBOR_OVERVIEW.md](HARBOR_OVERVIEW.md) — the long-form system reference: every
  subsystem, what's broken, and how it got that way.
- [ASSISTANT_ARCHITECTURE.md](ASSISTANT_ARCHITECTURE.md) — the assistant layer
  (commitments, scheduler, brief, outbox, connectors).
- [SMART_SEARCH_DESIGN.md](SMART_SEARCH_DESIGN.md) — the four-tier ⌘⇧F search.
- [RESEARCH_TOOL_PLAN.md](RESEARCH_TOOL_PLAN.md) — Research mode's design and
  implementation status.
- [TYPED_TASK_ENGINE_PLAN.md](TYPED_TASK_ENGINE_PLAN.md) — the typed task
  engine that replaced free-form tool selection for common tasks.
- [CONVERSATION_MEMORY_PLAN.md](CONVERSATION_MEMORY_PLAN.md) — conversation
  memory design (proposed; no extraction or migration has run).
- [BEHAVIOR_ENGINE.md](BEHAVIOR_ENGINE.md) — routine learning, predictions
  and nudges (`HARBOR_BEHAVIOR=1`).
- [inference-providers.md](inference-providers.md) — per-role local, remote and
  cloud inference bindings.
- [REMOTE_INFERENCE.md](REMOTE_INFERENCE.md) — remote-only inference: every
  role on one user-configured server (`HARBOR_REMOTE_ONLY=1`), read/write.

## Tools and routing

- [LING_TOOL_CALLING_RELIABILITY.md](LING_TOOL_CALLING_RELIABILITY.md) — how
  the resident model behaves under tool pressure, and what compensates.
- [routing_stress/](routing_stress/) — the 1,000-prompt routing corpus. These
  Markdown files are the **source**; `scripts/build_routing_stress_suite.py`
  compiles them into `test_fixtures/routing_stress/`, and
  `scripts/report_routing_stress_suite.py` writes results back into
  `routing_stress/results/` (generated, not tracked).

## Build, release and QA

- [build-release.md](build-release.md) — the sealed local candidate build.
- [SIMULATION_QA.md](SIMULATION_QA.md) — the non-mutating release gate.
- [DEBUGGING.md](DEBUGGING.md) — local problem reports, private trace history, and offline Today replay.
- [../TESTING.md](../TESTING.md) — manual test plan.
- [DEMO_TEST_PROMPTS.md](DEMO_TEST_PROMPTS.md) — manual prompt checklist, including notch and display checks.
- [google-cloud-setup.md](google-cloud-setup.md) — Google Cloud project, tester list and OAuth client for Connect Google.
- [releases/](releases/) — curated release notes, one page per version; start with [0.1.5](releases/0.1.5.md).

## Audits and backlog

- [OPTIMIZATION_BACKLOG.md](OPTIMIZATION_BACKLOG.md)
- [STABILITY_PLAN.md](STABILITY_PLAN.md)

## Working in this repo

- [design/apple-ui-refresh/](design/apple-ui-refresh/) — UI concept prototype
  and exports.

Completed per-branch handoffs are intentionally **not** kept here. They are in
git history; `CHANGELOG.md` is the durable record of what shipped.
