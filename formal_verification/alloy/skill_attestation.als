/*
 * A2ABreak — Finding #1: Unattested Skill Claims.  Alloy 6.
 * Encodes finding retry4_v2:C-001 in outputs/final_verified.json.
 * (Not in the responsible-disclosure PDF; the attestation fix is sourced from the
 *  paper and the A2A roadmap's QuerySkill proposal.)
 *
 * Scope of the claim (kept narrow): the model proves an IDENTITY vs CAPABILITY
 * separation — Agent Card signing (§8.3/§8.6/§8.7) authenticates the publisher but
 * NOT the truthfulness of advertised skills, so selection is unsound DESPITE
 * signing. It does not model downstream task-misdirection / exfiltration / harm.
 */
sig Publisher {}
sig Skill {}
sig Agent {
  truePublisher : one Publisher,     // who really operates the agent
  cardPublisher : one Publisher,     // publisher identity asserted in the Agent Card
  advertised    : one Skill,         // self-asserted AgentSkill (client can read this)
  actual        : one Skill          // real capability (NOT exposed); every agent has one,
                                     // so a lie is a genuine mismatch (advertised != actual)
}
sig Signed in Agent {}               // agents whose Agent Card carries a valid JWS (§8.3/8.6/8.7)

// What signing ACTUALLY guarantees: for a signed card the asserted publisher is authentic.
fact SigningAuthenticatesPublisher { all a: Signed | a.cardPublisher = a.truePublisher }

// The client selects an agent using ONLY the advertised field.
pred clientSelects[a: Agent, s: Skill] { a.advertised = s }

// Capability soundness, SCOPED TO SIGNED agents — so a counterexample establishes
// "unsound DESPITE signing", not merely "an unsigned agent can lie".
pred signedCapabilityHonored { all a: Signed, s: Skill | clientSelects[a, s] => a.actual = s }

// Proposed primitive (attestation / QuerySkill), scoped to match.
pred signedAttestation { all a: Signed | a.advertised = a.actual }

// --- ANALYTIC (holds at every scope) — the fix is literally the property (scoped),
//     so this justifies the "analytic" label on the fix-check below.
assert AttestationIsSignedCapability { signedAttestation iff signedCapabilityHonored }
check AttestationIsSignedCapability for 8

// --- EMPIRICAL — signing genuinely matters: an UNSIGNED card can forge its publisher.
pred UnsignedForgery { some a: Agent | a not in Signed and a.cardPublisher != a.truePublisher }
run UnsignedForgery for 3                                   // expect: instance found

// --- EMPIRICAL — yet a SIGNED, identity-authentic agent still lies about capability
//     (advertised != actual, both real skills since actual is `one Skill`).
pred SignedButLying { some a: Signed | a.advertised != a.actual }
run SignedButLying for 3                                    // expect: instance found

// --- EMPIRICAL — selection is unsound DESPITE signing.
assert SignedCapabilityHonored { signedCapabilityHonored }
check SignedCapabilityHonored for 4                        // expect: COUNTEREXAMPLE

// --- ANALYTIC — attestation (scoped to signed) closes the gap.
assert SignedAttestationRestores { signedAttestation => signedCapabilityHonored }
check SignedAttestationRestores for 6
