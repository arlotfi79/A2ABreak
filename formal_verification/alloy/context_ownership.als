/*
 * A2ABreak — Finding #3 / disclosure A2A-01: Cross-Client Context Injection.
 * Encodes finding run2:C-001:corrected in outputs/final_verified.json.  Alloy 6.
 *
 * What this model establishes: the gap SURVIVES A2A's actual task-authorization
 * rule (§13.1). The gap counterexample is required to satisfy §13.1, so it is not
 * an artifact of turning task protection off. Per the disclosure, §13.1 is
 * task-centric and "never engages the present attack."
 *
 * Scope of the claim (kept honest): the model proves unauthorized context
 * ADMISSION — a non-owner can introduce a task into a foreign context. Under
 * §3.4.1 the agent MAY use that context to maintain state and its response is
 * "informed by another client's conversational state", which is the information
 * leakage; that step is argued from the spec, not separately modeled here.
 */
sig Principal {}
sig Context { owner: one Principal }                       // creator of the context
sig Task {
  creator      : one Principal,                            // authenticated creator (§13.1)
  ctx          : one Context,
  accessibleTo : set Principal                             // who the server lets operate this task
}

// A2A §13.1 (MUST): task access is scoped to the creator.
pred section131 { all t: Task | t.accessibleTo in t.creator }
pred taskConfidentiality { all t: Task, p: Principal | p in t.accessibleTo => p = t.creator }

// A principal ADMITS a task into a context (§3.4.1 MAY / §3.4.3 MAY).
pred reachesContext[p: Principal, c: Context] { some t: Task | t.creator = p and t.ctx = c }
pred contextAdmissionIntegrity { all p: Principal, c: Context | reachesContext[p, c] => p = c.owner }

// Proposed primitive (A2A-01 fix): bind task creation in a context to its owner.
pred contextOwnershipGuard { all t: Task | t.creator = t.ctx.owner }

// --- ANALYTIC (holds at every scope) — these JUSTIFY the "analytic" labels used
//     elsewhere: §13.1 is literally task-confidentiality, and the fix is literally
//     admission integrity. They are confirmations, not empirical results.
assert TaskRuleIsTaskConfidentiality { section131 iff taskConfidentiality }
check TaskRuleIsTaskConfidentiality for 8
assert GuardIsAdmissionIntegrity { contextOwnershipGuard iff contextAdmissionIntegrity }
check GuardIsAdmissionIntegrity for 8

// --- EMPIRICAL — the gap survives §13.1: a counterexample where task auth is fully
//     satisfied yet a non-owner still admits a task into a foreign context.
assert GapSurvivesSection131 { section131 => contextAdmissionIntegrity }
check GapSurvivesSection131 for 6                          // expect: COUNTEREXAMPLE

// --- EMPIRICAL (non-vacuous witness) — a genuinely §13.1-protected task (its own
//     creator can access it) nonetheless sits in a context it does not own.
pred ProtectedTaskInForeignContext {
  section131
  some t: Task | t.creator in t.accessibleTo and t.creator != t.ctx.owner
}
run ProtectedTaskInForeignContext for 4                    // expect: instance found

// --- ANALYTIC — the fix closes admission (guard entails integrity).
assert Mitigated { contextOwnershipGuard => contextAdmissionIntegrity }
check Mitigated for 6
