---------------------------- MODULE IdentityLoss ----------------------------
(***************************************************************************)
(* A2ABreak - Finding #9 / disclosure A2A-02: Identity Loss in Multi-Hop   *)
(* Delegation Chains.  Chain: User(P0) -> A -> B(adversary) -> C(cred prov).*)
(*                                                                         *)
(* The disclosure's mitigation is THREE-part and LAYERED, so provenance and *)
(* credential-harvest are INDEPENDENT failures with DIFFERENT fixes:        *)
(*   - PropagateOrigin : carry originating-principal identity + chain of    *)
(*     custody (RFC 8693 token exchange; amend 7.6.1). Restores provenance. *)
(*   - BindCredential  : audience-restrict (RFC 8707) + sender-constrain    *)
(*     (DPoP RFC 9449 / mTLS) the issued credential to the true originator, *)
(*     "now that it is identifiable". Restores no-harvest -- and is only     *)
(*     effective once the origin is propagated (CredBound below).           *)
(*                                                                         *)
(* The immediate-hop identity is modeled explicitly (hopCallerC). The         *)
(* end-to-end origin is NOT a separate propagating variable: PropagateOrigin   *)
(* is an abstract feature flag whose successful EFFECT is modeled directly --  *)
(* when set, C authorizes for Origin instead of its immediate caller.          *)
(* Likewise BindCredential abstracts a successful audience/sender binding:     *)
(* whether B can USE the credential it holds is STIPULATED by CredBound, not   *)
(* derived from a modeled "B uses the credential" action. B always physically  *)
(* receives the credential (bHoldsCred); the property is about usability.      *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS PropagateOrigin, BindCredential, EnforceHopAuth

Origin == "P0"
Nobody == "none"
Spoof  == "spoofed"

\* Binding is only effective once the origin is identifiable (disclosure part 3
\* depends on parts 1-2): a credential can be audience-restricted to the true
\* originator only if that originator is propagated to the issuer.
CredBound == (BindCredential /\ PropagateOrigin)

VARIABLES
  phase,             \* 0 start .. 1 atC .. 2 decided .. 3 credAtB .. 4 done
  hopCallerC,        \* identity B presents to C (true immediate sender is "B")
  providerBelief,    \* who C authorizes for
  bHoldsCred         \* TRUE once the returning credential transits Agent B

vars == <<phase, hopCallerC, providerBelief, bHoldsCred>>

Init ==
  /\ phase = 0
  /\ hopCallerC = Nobody
  /\ providerBelief = Nobody
  /\ bHoldsCred = FALSE

\* Forward: the delegated request reaches C. Transport auth (7.2) pins the
\* presented identity to the real sender (B); without it, B may spoof.
Deliver ==
  /\ phase = 0 /\ phase' = 1
  /\ (IF EnforceHopAuth THEN hopCallerC' = "B" ELSE hopCallerC' \in {"B", Spoof})
  /\ UNCHANGED <<providerBelief, bHoldsCred>>

\* C authorizes for the end-to-end origin if it was propagated; otherwise for
\* the only identity it has, its immediate caller.
Decide ==
  /\ phase = 1 /\ phase' = 2
  /\ providerBelief' = (IF PropagateOrigin THEN Origin ELSE hopCallerC)
  /\ UNCHANGED <<hopCallerC, bHoldsCred>>

\* The issued credential returns along the chain and necessarily transits B.
CredBack ==
  /\ phase = 2 /\ phase' = 3
  /\ bHoldsCred' = TRUE
  /\ UNCHANGED <<hopCallerC, providerBelief>>

Finish ==
  /\ phase = 3 /\ phase' = 4
  /\ UNCHANGED <<hopCallerC, providerBelief, bHoldsCred>>

Next == Deliver \/ Decide \/ CredBack \/ Finish \/ (phase = 4 /\ UNCHANGED vars)

Spec == Init /\ [][Next]_vars

\* ---- Correspondence control: the identity C sees equals the true sender (B).
\*      Genuinely violable: fails when EnforceHopAuth = FALSE (B spoofs).
HopAuthentic == (phase >= 1) => (hopCallerC = "B")

\* ---- Provenance: C authorizes for the true origin. Restored by PropagateOrigin.
ProvenanceHolds == (phase >= 2) => (providerBelief = Origin)

\* ---- Credential usability by B. STIPULATIVE: CredBound abstracts a successful
\*      audience/sender binding (effective only once origin is propagated); the
\*      property asserts "if B holds the credential it is bound (hence unusable)".
\*      This is a feature-ablation over the fix flags, not a solver search.
CredentialUnusableByB == bHoldsCred => CredBound

TypeOK ==
  /\ phase \in 0..4
  /\ hopCallerC \in {Nobody, "B", Spoof}
  /\ providerBelief \in {Nobody, Origin, "B", Spoof}
  /\ bHoldsCred \in BOOLEAN
=============================================================================
