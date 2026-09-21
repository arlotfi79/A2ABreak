# Ethics

This is the file referenced by `ethics` in `metadata.toml`.

## Responsible disclosure

All eleven vulnerabilities have been reported to the A2A project maintainers under the Linux Foundation.

The paper records the status as of its submission, when a response was still pending; the acknowledgement and the ongoing exchange followed.

Three findings carry disclosure identifiers: **A2A-01** (Cross-Client Context Injection, #3) and **A2A-02** (Multi-Hop Identity Loss, #9); #1 (Unattested Skill Claims) was reported without one.

---

## What is being released

Findings at the level of **protocol design**, not working exploits.

Each of the eleven entries in `outputs/final_verified.json` describes a missing primitive in the specification — context ownership binding, capability attestation, credential provenance across delegation hops — together with the FSM trace showing that a compliant participant can reach the problematic state. None is an implementation bug, and the artifact contains no exploit code, no proof-of-concept attack tool, no scanner, and no payload.

This matters for how the findings behave in the world. They are exploitable by an adversary that follows the specification exactly, so no patch to any single implementation removes them; they are closed by amending the specification. Publishing the analysis is what lets implementers and the standards body act. Withholding it would protect no one, because the specification is public and the gaps are derivable from it — which is precisely what the paper demonstrates.

---

## Data collection

**No human subjects.** No user study, survey, interview or observation. No IRB review was required or sought.

**No personal data.** The artifact processes two public technical documents and nothing else: the A2A protocol specification published by the Linux Foundation, and RFC-derived protocol segments distributed with PSMBench. Neither contains personal information.

**No third-party systems touched.** The analysis is specification-level throughout. At no point does the pipeline connect to, probe, scan or test a live A2A deployment, agent, or any third party's infrastructure. The only network calls the artifact makes are: fetching the public specification, calling the Anthropic API with specification text, downloading two pinned solver jars, and cloning PSMBench.

**No scraping, no circumvention.** Every source is openly published and retrieved as published. No access control, rate limit or terms-of-service restriction was bypassed.

---

## Use of LLMs

The pipeline uses Claude Sonnet 4.6 and Claude Opus 4.6 to extract formal statements and construct state machines from specification prose. Specification text is sent to the Anthropic API; it is public, so no confidential material is transmitted.

Model output is treated as a hypothesis, never as ground truth. Every extracted statement is verified against the source text (`src/verify_stage_a.py`), every FSM trace is checked for validity against the model, and every vulnerability candidate is subjected to adversarial verification before a human reviews it. Three human checkpoints sit at stage boundaries, and the expert review that produced the final eleven findings rejected four of sixteen machine-accepted candidates. Precision is reported as 73.3% rather than claimed to be higher, and `CLAIMS.md` records every point where the artifact's numbers differ from the paper's.

---

## Intended use and limits

**Intended:** studying the A2A protocol's security properties; reusing the statement corpus or unified FSM as a formal model; applying the extraction methodology to other natural-language protocol specifications; building on the findings to propose mitigations.

**Not suitable for:** testing or attacking live A2A deployments; auditing a specific implementation, since the findings are design-level and say nothing about any implementation's correctness; use as an A2A reference implementation, which this is not; automated vulnerability scanning.

The artifact describes gaps in a standard so they can be closed. Using it to attack systems operated by others would be both outside its purpose and, in most jurisdictions, unlawful.

---

## Evaluation conduct

The artifact embeds no analytics, telemetry or tracking of any kind, so nothing in it can reveal the identity of an ACSAC artifact evaluator during the single-blind evaluation period.
