# RAV-03 Research Report

Research date: 2026-09-21

This report audits the current repository and recommends the smallest defensible product wedge after reviewing scenario-based ADAS V&V, commercial platforms, open-source tooling, recent research and relevant datasets. It does not claim safety certification or production-ADAS validity.

Detailed evidence:

- [Competitor landscape](research/competitor-landscape.md)
- [Niche analysis](research/niche-analysis.md)
- [Dataset strategy](research/dataset-strategy.md)
- [Technical feasibility](research/technical-feasibility.md)
- [MVP recommendation](research/mvp-recommendation.md)

# Executive Summary

1. The repository is a **product/UI prototype**, not a functional CARLA or research prototype.
2. The backend is the untouched course skeleton: FastAPI + LangGraph echo nodes, with no domain pipeline.
3. The polished browser prototype explicitly uses synthetic data and a mock runner; it provides useful workflow learning but no simulator evidence.
4. No scenario schema, deterministic validator, CARLA adapter, SUT, oracle, search engine, persistence or standards test exists.
5. “Natural language → CARLA scenario” is not a strong standalone wedge; Scenic, ChatScene/Chat2Scenic and commercial tools already demonstrate adjacent capability.
6. General scenario management, parameter variation, coverage and real-log mining are mature commercial categories; RAV-03 should not imitate an enterprise platform.
7. The strongest feasible wedge is **minimum perturbation to failure** for one motorcycle cut-in/AEB-like scenario family.
8. The output should be a reproducible observed PASS/FAIL bracket and neighboring replay cases, not an exact universal “failure boundary.”
9. Use deterministic code for schema validation, map binding, standards export, execution, oracle and search; use an LLM only for draft intent parsing and optional explanation.
10. Use coarse constrained sampling followed by bracketed bisection; compare against random search. Do not begin with RL, CMA-ES or a learned surrogate.
11. Vietnam/motorcycle-heavy traffic is strategically relevant but not yet a defensible data moat. Public Vietnam datasets reviewed are mostly perception/video, not ready behavior distributions.
12. Any `VN_TRAFFIC_PROFILE` numbers must be measured or cited; otherwise mark them as placeholders. pNEUMA, SinD, hetroD or AMIT can validate the extraction method but must not be relabeled as Vietnamese.
13. CARLA ScenarioRunner only partially supports OpenSCENARIO and requires version matching; claim compatibility only for a tested subset.
14. A credible two-week result is one map, one pinned SUT baseline, one cut-in template, 1D boundary search, a deterministic oracle and complete replay evidence.
15. The principal moat would be accumulated reviewed boundary/regression evidence plus local data partnerships—not the LLM prompt or search algorithm.

## Current State

### What is implemented

- FastAPI health/status/chat endpoints and a compiled LangGraph skeleton.
- Pydantic configuration and generic chat schemas.
- Docker/CI/test scaffolding inherited from the template.
- A substantial static HTML/CSS/JS prototype implementing the intended product flow.
- Product Brief, PRD, UI flow, Gate 1 deliverables, project-management workbooks and AI usage logging.
- Two simple tests for echo-style API/agent behavior.

### What is mocked

- The graph's analysis and response nodes format input text; they do not call the configured LLM.
- The knowledge search tool is a stub.
- UI runs, PASS/FAIL decisions, costs, artifacts and CARLA progress are synthetic.
- Architecture, database and vector-store sections remain template placeholders.

### What is missing

- Domain contracts, logical scenario model, parameter constraints and units.
- CARLA/ScenarioRunner installation and execution integration.
- A named SUT baseline, oracle and telemetry contract.
- Search/fuzzing/boundary analysis.
- Real data extraction and validated local parameter priors.
- Persistence, immutable manifests, replay and SUT-version comparison.
- End-to-end evaluation and real evidence.

### Reusable parts

Reuse FastAPI/config structure, Pydantic location, CI ideas, review/provenance concepts and UI information architecture. Replace the generic chat domain, mock UI data layer and template tests. Keep LangGraph optional; a deterministic pipeline is simpler and safer for the core.

### Technical debt

- Runtime still identifies as “AI20K Agent.”
- Dependencies are not locked. The repository `.venv` uses Python 3.11.9 and the full test suite passes (`5 passed`); its interpreter requires execution outside the restricted audit sandbox because it resides under the user's `AppData` directory.
- README and architecture mix product and course-template material.
- UI declares CARLA 0.9.15 without a verified runtime.
- Standards compatibility is an aspiration, not an implementation.

**Maturity: Prototype.** There is enough product design and UI behavior to exceed “Idea,” but no genuine domain execution to qualify as a functional or research prototype.

## Actual Problem

### A. Real industry pain points

**FACT:** ISO 21448 addresses unreasonable risk from functional or performance insufficiencies and provides V&V guidance. Scenario-based testing is one response to the infeasibility of covering the operational space with physical mileage alone. [ISO 21448](https://www.iso.org/standard/77490.html)

**FACT:** ASAM OpenSCENARIO represents parameterized scenarios and parameter distributions, enabling multiple concrete tests from a logical scenario. [ASAM parameter distributions](https://simulation.pages.asam.net/openscenario/openscenario-antora-gen/ASAM_OpenSCENARIO_XML/current_xml_v1.x/09_reuse_mechanisms/09_03_parameter_distribution.html)

The important engineering problems are:

- authoring executable, semantically valid scenarios from ambiguous safety intent;
- keeping constraints physically/map/simulator feasible;
- reducing an enormous parameter space to informative runs;
- defining a trustworthy oracle and continuous robustness signal;
- distinguishing SUT failure from invalid input and infrastructure failure;
- showing coverage and provenance;
- replaying exact cases across SUT versions;
- mining realistic distributions without overstating geographic transfer.

### B. Cool features that do not solve enough

- One-shot LLM generation of a dramatic CARLA scene.
- An unexplained crash counter.
- A visually rich dashboard driven by synthetic metrics.
- “Vietnam mode” with invented actor ratios or gap distributions.
- Exporting an `.xosc` file without executing a documented supported subset.
- An RL adversary before a stable deterministic runner and oracle.

## Competitor Conclusion

Commercial products from Foretellix, dSPACE, Siemens, MathWorks, Cognata, AVL, IPG and Vector already cover broad parts of authoring, parameter variation, orchestration, KPI evaluation, coverage and real-world data reuse. Open-source projects cover simulation and scenario languages; research projects cover fuzzing and LLM authoring. See the [full matrix](research/competitor-landscape.md).

**INFERENCE:** RAV-03's opportunity is not breadth. It is an accessible CARLA-first workflow that produces an engineer-readable boundary experiment under a small budget.

## White Space and Niche Decision

The highest-scoring niches were:

1. Minimum perturbation that causes failure: 63/70.
2. Failure-boundary discovery: 61/70.
3. Near-miss to parameterized search: 57/70, but not feasible first.
4. Reproducibility/regression and motorcycle-profile work: 55/70.

The selected product is the intersection of the first, second and reproducibility niches. See [scoring and moat analysis](research/niche-analysis.md).

## What we should build

A CARLA-first tool that finds and replays the smallest observed parameter perturbation that changes a reviewed motorcycle cut-in scenario from PASS to FAIL under a pinned SUT, oracle and run budget.

## Target user

An ADAS/simulation engineer or autonomy research team using CARLA who needs reproducible evidence around a critical threshold.

## Primary pain point

Today the user can create or find a failing case but spends too much manual effort determining how close it is to a passing case and whether the transition moves after a SUT change.

## Core differentiator

RAV-03 returns a confirmed, replayable PASS/FAIL neighbor pair with complete provenance and budget statistics, rather than merely generating another scenario.

## Why this is not just another LLM wrapper

The LLM is outside the trusted execution path. It may draft a canonical scenario and list ambiguities. Deterministic code validates units, ranges, actor/map feasibility and supported features; compiles the concrete scenario; executes CARLA; calculates oracle metrics; selects samples; confirms the transition; hashes artifacts; and reports replay evidence. The core product remains usable when the user supplies JSON and disables the LLM.

## Failure Boundary Analysis

For scenario vector `x` and robustness `r(x)`, the mathematical boundary is `r(x)=0`. The MVP cannot establish the full boundary of a stochastic, high-dimensional system. It reports an **observed local transition bracket** along one controlled slice:

```text
context fixed: map, SUT, weather, speeds, seed policy, oracle
search variable: initial longitudinal gap g
result: g_fail ≤ boundary ≤ g_pass
evidence: replayed FAIL/PASS neighbors + bracket width + run count
```

The algorithm should first sample coarsely to detect non-monotonic behavior and find a bracket. Only then apply bisection. Repeat near the transition if identical configurations can change outcome. Random sampling under the same bounds and budget is the baseline.

Bayesian optimization becomes useful after the runner and continuous robustness are reliable. Evolutionary algorithms and CMA-ES are better for finding critical points in non-convex spaces than for producing a simple calibrated 1D bracket. Active learning and RL are later research tracks.

## Vietnam Traffic Profile Verdict

**FACT:** PHENIKAA provides 1,500 20-second multi-camera/LiDAR sequences from Vietnamese traffic and emphasizes a rider class and dense annotations, but the reviewed official page does not establish trajectory/map/interaction labels suitable for cut-in parameter distributions. [PHENIKAA](https://github.com/phenikaa-dataset/phenikaa-devkit)

**FACT:** UIT-ADrone contains 51 drone videos covering 6.5 hours and 206k frames at three Ho Chi Minh City roundabouts with ten abnormal-event types. It is valuable for anomaly taxonomy, not automatically a trajectory prior. [UIT-ADrone](https://ieeexplore.ieee.org/document/10158513/)

**FACT:** pNEUMA, SinD, hetroD and AMIT provide stronger trajectory-oriented material for motorcycle/heterogeneous interaction research, but they are from Greece, China or Taiwan. [pNEUMA](https://open-traffic.epfl.ch/) · [SinD](https://github.com/SOTIF-AVLab/SinD) · [hetroD](https://levelxdata.com/hetrod-dataset/) · [AMIT-SI](https://dataverse.lib.nycu.edu.tw/dataset.xhtml?persistentId=doi:10.57770/2SFJ2G)

**RECOMMENDATION:** Implement `VN_TRAFFIC_PROFILE` as a provenance-aware container whose fields can be `placeholder`, `measured` or `reviewed`. Do not publish numeric distributions until a Vietnam trajectory source supports them. Use non-Vietnam data only to validate extraction code and explicitly label the geography.

## Recommended datasets

Minimum set:

1. Hand-authored cut-in fixtures and CARLA-generated run data.
2. A small pNEUMA or hetroD/AMIT sample for trajectory feature extraction methodology.
3. SinD samples or paper for SOTIF-oriented interaction/risk semantics.
4. PHENIKAA and UIT-ADrone as local perception/anomaly references, not immediate downloads or priors.
5. Nexar/DADA-2000/DoTA only for accident taxonomy; video reconstruction is deferred.

See [dataset table and licenses](research/dataset-strategy.md).

## Recommended algorithms

- Constraint-aware coarse grid or Latin hypercube.
- Bracket detection and bisection on one monotonic axis.
- Exact-configuration caching.
- Repeated transition runs when nondeterminism appears.
- Random-search baseline under equal budget.
- P1: Gaussian-process Bayesian optimization on continuous robustness if 2D/3D search becomes necessary.

## Recommended standards

- Internal versioned canonical JSON/Pydantic schema as source of truth.
- ASAM OpenSCENARIO 1.x export for a declared, executor-tested subset.
- ASAM OpenDRIVE map reference, not a custom map editor.
- OSI-style signal naming where useful, without claiming full OSI integration.
- ISO 21448 as safety-process context, not a certification claim.

## Recommended CARLA architecture

- Separate lightweight API/UI process from a CARLA worker.
- Pin CARLA and ScenarioRunner matching versions; the official repository warns they must match. [ScenarioRunner](https://github.com/carla-simulator/scenario_runner)
- Use synchronous/fixed-step mode and named seed policy.
- Normalize PASS/FAIL/INVALID/INFRA_ERROR.
- Store an immutable manifest and hashes for every completed run.
- Keep search/oracle simulator-agnostic behind interfaces.
- Run headless/no-rendering for search; render only selected evidence cases.

## MVP

The first working product accepts or drafts one logical motorcycle cut-in scenario, validates it, runs a bounded CARLA campaign, finds a local PASS/FAIL gap bracket, replays both neighbors, and exports a report containing parameters, metrics, versions, seeds, hashes and limitations.

## MVP success metrics

- Valid scenario rate and human correction rate on a fixed intent set.
- CARLA completion rate for valid submissions.
- Replay consistency for exact configurations.
- Simulations required to find and confirm a target-width bracket.
- Boundary confirmation failure rate.
- Search efficiency versus random under equal budget.
- Artifact completeness.
- OpenSCENARIO execution rate for the declared subset.
- Wall-clock/compute minutes per confirmed bracket.

## What NOT to build

- General-purpose simulation platform.
- Video-to-simulation pipeline.
- Perception model training or photorealistic sensor validation.
- Whole-city Vietnam digital twin or uncited traffic distributions.
- Full ODD coverage dashboard.
- Multi-simulator abstraction before one CARLA path works.
- RL/CMA-ES/surrogate learning before simple search has a measured baseline.
- Certification, homologation or production-safety claims.

## Biggest technical risk

The simulator/SUT may be nondeterministic or non-monotonic, making a crisp threshold misleading. Mitigate with fixed-step runs, coarse scans, repeated transition samples and bracket/probability reporting.

## Biggest product risk

The team may build a technically appealing CARLA demo without access to a real ADAS tester or SUT owner who confirms that boundary/regression evidence changes their workflow.

## Biggest research gap

There is no verified public Vietnam trajectory dataset in the reviewed set that directly supplies map-aligned, interaction-level motorcycle cut-in distributions suitable for CARLA parameterization.

## Strongest competitive advantage

If executed, the strongest advantage is a curated corpus of reviewed motorcycle interaction templates and longitudinal boundary/regression results with exact provenance, later calibrated through Vietnam data partnerships.

## Demo storyboard

1. Enter Vietnamese cut-in intent.
2. Review structured scenario; show which range is placeholder/measured.
3. Catch one deterministic validation error.
4. Launch a fixed-budget search.
5. Display the coarse results and narrowing bracket.
6. Replay nearest PASS/FAIL cases with TTC/min-distance traces and manifests.
7. Compare a second SUT version/config and show movement or no movement.
8. State scope honestly: one map, one baseline, one slice, no certification.

## Two-week and four-week execution

The day-by-day plan, P0/P1/P2 scope and branch contracts are in [MVP recommendation](research/mvp-recommendation.md). The first two weeks end with a real runner, a real oracle and one reproducible boundary campaign. Weeks three and four add the reviewed LLM parser, tested standards subset, repeated-run uncertainty, SUT comparison and a small provenance-aware data-profile method.

## Next 10 engineering tasks

1. Freeze one map, exact CARLA/ScenarioRunner versions, one SUT baseline, one oracle and a seed/timestep policy.
2. Define versioned `ScenarioSpec`, `ConcreteScenario`, `RunRequest`, `RunResult`, `OracleResult` and `BoundaryEstimate` contracts.
3. Add positive and negative cut-in fixtures with explicit units and constraints.
4. Implement deterministic schema, rule and map-capability validation.
5. Build a synthetic deterministic runner/oracle for fast contract and search tests.
6. Repair and pin the Python environment; separate API dependencies from CARLA worker dependencies.
7. Execute one known-good real CARLA/ScenarioRunner scenario and capture all artifacts.
8. Implement normalized telemetry, oracle robustness and immutable run manifests.
9. Implement coarse sampling, bracket detection and bisection; verify on synthetic functions first.
10. Run the first real campaign and compare simulations-to-bracket against random search.

## Source quality and limitations

Primary sources were preferred: official ASAM/ISO/CARLA documentation, official company product pages, official dataset pages/repositories and papers. Product pages describe vendor capabilities but do not independently prove performance. Prices and several license/download-size fields were not publicly verifiable and are marked accordingly. No datasets or large dependencies were downloaded during this research.
