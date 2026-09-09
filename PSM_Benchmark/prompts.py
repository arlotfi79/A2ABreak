#!/usr/bin/env python3
"""
prompts.py
----------
One protocol-generic prompt set for all 14 PSMBench protocols.

Derived from the pipeline's original TCP prompt set with every TCP-specific hint
removed (METHOD.md). The ONLY model-visible protocol
parameter is the protocol name; the RFC number, ground-truth vocabularies,
section pointers and scope hints are gone. Substitution is targeted string
replacement, never str.format(), so JSON braces and the verify placeholders
({section_id}, {title}, {section_text}, {statement_id}, {statement_json})
survive untouched.
"""

from __future__ import annotations

import hashlib
import json
import re

# Placeholders replaced by build_prompts(); chosen so they cannot collide with
# the verify templates' own {curly} placeholders.
_P = "__PROTOCOL__"
_PL = "__PROTOCOL_LOWER__"
_STAGE = "__STAGE_LABEL__"
_SUBJ = "__SUBJECTS__"

PROMPT_KEYS = (
    "stage_a1",
    "stage_a2",
    "verify_stage_a12_n",
    "verify_stage_a12_d",
    "stage_b1",
)

USER_TEMPLATE_KEYS = ("a1_user_template", "a2_user_template")

# Generic subject vocabulary — same for every protocol apart from the protocol
# name itself. Also patched into merge_stage_a12.VALID_SUBJECTS.
BASE_SUBJECTS = (
    "client",
    "server",
    "participant",
    "peer",
    "endpoint",
    "session",
    "connection",
    "transaction",
    "user",
    "message",
    "timer",
    "state_machine",
)

# Protocol-agnostic invariant checks: what a clean prompt set must not contain,
# plus cross-protocol equality after masking the protocol name. No protocol
# vocabulary is listed here, since that would itself be protocol-specific knowledge.
URL_RE = re.compile(r"https?://", re.IGNORECASE)
RFC_NUMBER_RE = re.compile(r"\bRFC\s?(\d+)\b", re.IGNORECASE)
# RFC 2119 is the requirements-language RFC. It names the MUST/SHOULD/MAY vocabulary
# every protocol spec uses and identifies no protocol, so it is the one RFC number a
# neutral prompt may mention; a protocol's OWN RFC number never appears.
_RFC_ALLOW = {"2119"}
SECTION_POINTER_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)*\b")
RESIDUE_TOKENS = ("A2A", "web search")

# Numeric literals that legitimately appear inside the JSON schema examples and
# must not be mistaken for section pointers.
_SCHEMA_NUMERIC_ALLOW = ("1.0", "0.0")


_FOCUS = (
    "Focus on the protocol's state machine — the states a protocol participant "
    "or modeled interaction can be in and the events that move it between them. "
    "Do not extract message field layouts, encoding rules, or implementation "
    "advice unless they directly define a state transition."
)

# ---------------------------------------------------------------------------
# Output-format conventions for Stage B synthesis.
#
# Structural conventions adapted from the benchmark's own baseline prompt builder
# (RFC_PSM_Benchmark/prompt_generation.py:43-76,150-166, commit df0bce6) so our
# synthesis is held to the same shape the baselines were. Concrete examples and
# the fixed action-verb list are DELIBERATELY OMITTED: the originals spell out
# labels such as "Authenticated", "receive USER", "reply 230" and
# "set authenticated true", which coincide exactly with FTP and POP3 ground-truth
# vocabulary. Only format rules survive here — no lexical content that could seed
# a ground-truth label.
# ---------------------------------------------------------------------------
BENCHMARK_CONVENTIONS = """State names (state_name, from_state, to_state):
- 1 to 3 words, at most 30 characters
- CamelCase or snake_case
- name a protocol phase, status, or role
- no punctuation, no spaces, no free-form descriptions

Events (trigger):
- name the trigger that causes the transition, in 1 to 3 words
- it may begin with one of these generic prefixes where one applies:
  `receive ` for a received message, `send ` for a sent message,
  `timeout ` for a timer expiry, `cond ` for an internal condition

Actions (actions):
- one verb followed by at most four words describing the effect
- take the verb and the wording from the source text, not from a fixed list"""


A1_USER_TEMPLATE = f"""Extract {_P} state-machine structure from the following {_P} specification section.

Apply the structural extraction questions from your system prompt to this section only.
Return a JSON array of extracted statements. If nothing is extractable, return [].

Section ID: {{section_id}}
Section path: {{parent_path}}

Section text:
{{section_text}}"""


A2_USER_TEMPLATE = f"""Extract every RFC 2119 normative statement from the following {_P} specification section.

Apply the behavioral extraction rules from your system prompt to this section only.
Return a JSON array of extracted statements. If nothing is extractable, return [].

Section ID: {{section_id}}
Section path: {{parent_path}}

Section text:
{{section_text}}"""


def _stage_a1_prompt() -> str:
    return f"""
You are a protocol state-machine analyst. Your task is to extract structural
and definitional statements from the specification of the {_P} protocol, for
the {_P} protocol state machine.

You will receive exactly one specification section at a time. Extract only
statements grounded in that section. If the section contains nothing relevant
to the {_P} state machine, return an empty JSON array.

{_FOCUS}

Use one pipeline stage value for every statement: "{_STAGE}".

Valid subject values:
{_SUBJ}

FORMAL FSM MODEL

You are building components of a formal FSM tuple: S, s0, T, A, sf.

  S  = protocol states
  s0 = initial state as specified by the source text
  sf = final/accepting states as specified by the source text
  T  = state transitions
  A  = events that trigger transitions, as named by the source text

Extract exactly one of these categories:

Schema 1 -- STATE -> FSM_STATE
{{
  "statement_id": "D-{{section}}-{{sequence}}",
  "normative_level": "DEFINITION",
  "category": "FSM_STATE",
  "subject": "one of the valid subject values",
  "stage": "{_STAGE}",
  "description": "verbatim or near-verbatim source text",
  "is_inferred": false,
  "confidence": 1.0,
  "source_section": "the Section ID given in the user message, e.g. S014",
  "state_name": "state name",
  "enum_name": "name of the state set this state belongs to",
  "semantic_type": "initial|active|closing|terminal|error",
  "is_initial": false,
  "is_final": false,
  "invariant_cond": "protocol.state = <StateA> or null"
}}

Schema 2 -- CONSTRAINT -> FIELD_PRESENCE | ONEOF_CONSTRAINT
Only use this when the section defines a structural condition directly relevant
to the state machine, such as a required object or a required field for a
transition. Do not extract generic message layouts as FSM constraints.
{{
  "statement_id": "D-{{section}}-{{sequence}}",
  "normative_level": "DEFINITION",
  "category": "FIELD_PRESENCE|ONEOF_CONSTRAINT",
  "subject": "one of the valid subject values",
  "stage": "{_STAGE}",
  "description": "verbatim or near-verbatim source text",
  "is_inferred": false,
  "confidence": 1.0,
  "source_section": "the Section ID given in the user message, e.g. S014",
  "object_name": "the named object the condition applies to",
  "field_name": "the named field or flag",
  "field_type": "e.g. flag, state variable, parameter",
  "required": true,
  "allowed_values": null,
  "guard_semantics": null
}}

Schema 3 -- TRANSITION -> IMPLICIT_TRANSITION
Extract transitions from state diagrams, tables, and prose when both endpoint
states or a clear event/action are present.
{{
  "statement_id": "D-{{section}}-{{sequence}}",
  "normative_level": "DEFINITION",
  "category": "IMPLICIT_TRANSITION",
  "subject": "one of the valid subject values",
  "stage": "{_STAGE}",
  "description": "verbatim or near-verbatim source text",
  "is_inferred": false,
  "confidence": 0.0,
  "source_section": "the Section ID given in the user message, e.g. S014",
  "actors": ["the parties the source text names"],
  "from_state": "state name or null",
  "to_state": "state name or null",
  "trigger": "concise event name or null",
  "pre_cond": "logical guard expression or null",
  "post_cond": "logical effect expression or null",
  "actions": ["ordered transition effects"],
  "modality": "must|shall|should|may|can|other|unspecified"
}}

Rules:
- Use only "{_STAGE}" for stage.
- Use only the listed subject values.
- Use state names exactly as they appear in the source text unless normalizing
  punctuation or whitespace is required for consistency with another extracted
  statement from the same source.
- Use null when unknown. Do not invent states or transitions.
- Conditions must be formal expressions using dot notation, =, &, |, !.
  Use state names only when they are grounded in the source text. Never put
  prose in *_cond fields. Example form: "protocol.state = <StateA>".
- Actions should be concrete protocol effects named by the source text.
- source_section must be the Section ID from the user message, verbatim.
- Return ONLY a valid JSON array. No markdown fences.
""".strip()


def _stage_a2_prompt() -> str:
    return f"""
You are a protocol analyst specializing in formal FSM extraction from the
specification of the {_P} protocol. Extract every sentence containing RFC 2119
keywords:
MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED,
MAY, OPTIONAL.

The target is the {_P} protocol state machine, not the entire {_P}
implementation. Keep every RFC 2119 sentence, but only populate from_state,
to_state, trigger, pre_cond, post_cond, and actions when the sentence
constrains protocol state, message processing, session establishment or
teardown, or timers relevant to state transitions. Otherwise set the FSM fields
to null/[] as appropriate.

{_FOCUS}

Use one pipeline stage value for every statement: "{_STAGE}".
Valid subject values: {_SUBJ}

Each object MUST have exactly these fields:
{{
  "statement_id": "N-{{section}}-{{sequence}}",
  "normative_level": "MUST|MUST NOT|SHALL|SHALL NOT|SHOULD|SHOULD NOT|RECOMMENDED|MAY|OPTIONAL|REQUIRED",
  "category": "BEHAVIORAL",
  "subject": "one of the valid subject values",
  "stage": "{_STAGE}",
  "description": "verbatim original sentence containing the RFC 2119 keyword",
  "is_inferred": false,
  "confidence": 1.0,
  "source_section": "the Section ID given in the user message, e.g. S014",
  "actors": ["the parties the source text names"],
  "from_state": "state name or null",
  "to_state": "state name or null",
  "trigger": "concise event name or null",
  "pre_cond": "logical guard expression using &, |, ! or null",
  "post_cond": "logical effect expression using &, |, ! or null",
  "actions": ["ordered effects the rule mandates"],
  "modality": "must|shall|should|may|can|other|unspecified"
}}

Subject guidance:
- client: behavior of the initiating party
- server: behavior of the responding party
- participant: a party whose role the source text does not distinguish
- peer: the remote party in a symmetric exchange
- endpoint: a local or remote protocol endpoint
- session: session or association lifecycle
- connection: connection lifecycle and its control block
- transaction: request/response exchange lifecycle
- user: the local service/application interface to the protocol
- message: a sent or received protocol message
- timer: timers relevant to state transitions
- state_machine: the modeled automaton as a whole
- {_PL}: the protocol mechanism as a whole

Rules:
- Use only "{_STAGE}" for stage.
- If a normative sentence spans states, assign the most direct from_state and
  to_state only when the source text states them or the guard/effect clearly
  implies them. Otherwise leave state fields null.
- Use state names exactly as they appear in the source text unless normalizing
  punctuation or whitespace is required for consistency with another extracted
  statement from the same source.
- Conditions must be formal expressions using dot notation, =, &, |, !. Use
  state names only when they are grounded in the source text. Never write
  natural-language sentences in pre_cond or post_cond. Example form:
  "protocol.state = <StateA>".
- Description must be verbatim. Do not paraphrase.
- source_section must be the Section ID from the user message, verbatim.
- Return ONLY a valid JSON array. No markdown fences.
""".strip()


def _verify_n_prompt() -> str:
    return f"""
You are verifying one {_P} BEHAVIORAL normative statement (N- prefix) against
one section of the {_P} specification.

STEP 1 -- EXISTENCE
Find the statement's description as verbatim or near-verbatim text in the
source. If not found, set existence to "not_found" and status to
"hallucinated".

STEP 2 -- FIELD CHECK
If found, compare every field to the source text. Correct wrong
normative_level, subject, stage, actors, state names, trigger, formal
conditions, actions, modality, confidence, and source_section.

Valid subject values: {_SUBJ}
Valid stage value: {_STAGE}
Normative keywords: MUST|MUST NOT|SHALL|SHALL NOT|SHOULD|SHOULD NOT|RECOMMENDED|MAY|OPTIONAL|REQUIRED

Return ONLY valid JSON:
{{
  "statement_id": "{{statement_id}}",
  "status": "<verified|corrected|hallucinated|vague>",
  "existence": "<found|not_found>",
  "location_in_text": "<first 10 words of sentence in source, or null>",
  "corrections": {{
    "normative_level": {{"was": "...", "should_be": "...", "reason": "..."}},
    "subject":         {{"was": "...", "should_be": "...", "reason": "..."}},
    "stage":           {{"was": "...", "should_be": "...", "reason": "..."}},
    "description":     {{"was": "...", "should_be": "...", "reason": "..."}},
    "modality":        {{"was": "...", "should_be": "...", "reason": "..."}},
    "actors":          {{"was": [], "should_be": [], "reason": "..."}},
    "from_state":      {{"was": "...", "should_be": "...", "reason": "..."}},
    "to_state":        {{"was": "...", "should_be": "...", "reason": "..."}},
    "trigger":         {{"was": "...", "should_be": "...", "reason": "..."}},
    "pre_cond":        {{"was": "...", "should_be": "...", "reason": "..."}},
    "post_cond":       {{"was": "...", "should_be": "...", "reason": "..."}},
    "actions":         {{"was": [], "should_be": [], "reason": "..."}},
    "is_inferred":     {{"was": false, "should_be": false, "reason": "..."}},
    "confidence":      {{"was": 0.0, "should_be": 0.0, "reason": "..."}},
    "source_section":  {{"was": "...", "should_be": "...", "reason": "..."}},
    "is_global_constraint": {{"was": false, "should_be": false, "reason": "..."}},
    "id":              {{"was": "...", "should_be": "...", "reason": "..."}}
  }}
}}

Use null for any correction key you are not changing. "was" must match the
current statement value for that field. "should_be" is the corrected value
(string, number, boolean, array, or null as appropriate). Never change
statement_id.

SOURCE TEXT (section {{section_id}} -- {{title}}):
{{section_text}}

STATEMENT TO VERIFY:
{{statement_json}}
""".strip()


def _verify_d_prompt() -> str:
    return f"""
You are verifying one {_P} STRUCTURAL statement (D- prefix: FSM_STATE,
FIELD_PRESENCE, ONEOF_CONSTRAINT, IMPLICIT_TRANSITION) against one section of
the {_P} specification.

STEP 1 -- EXISTENCE
Check whether the state, structural constraint, or implicit transition is
actually present in this section. It may appear in prose, a figure, a state
diagram, a table, or a processing rule.

STEP 2 -- FIELD CHECK
If found, compare every field to the source and correct wrong subject, stage,
description, state name, semantic type, initial/final flags, guards, actions,
actors, trigger, or source_section.

Valid subject values: {_SUBJ}
Valid stage value: {_STAGE}

Return ONLY valid JSON:
{{
  "statement_id": "{{statement_id}}",
  "status": "<verified|corrected|hallucinated>",
  "existence": "<found|not_found>",
  "location_in_text": "<table row / diagram line / sentence start, or null>",
  "corrections": {{
    "category":        {{"was": "...", "should_be": "...", "reason": "..."}},
    "subject":         {{"was": "...", "should_be": "...", "reason": "..."}},
    "stage":           {{"was": "...", "should_be": "...", "reason": "..."}},
    "description":     {{"was": "...", "should_be": "...", "reason": "..."}},
    "normative_level": {{"was": "...", "should_be": "...", "reason": "..."}},
    "state_name":      {{"was": "...", "should_be": "...", "reason": "..."}},
    "enum_name":       {{"was": "...", "should_be": "...", "reason": "..."}},
    "semantic_type":   {{"was": "...", "should_be": "...", "reason": "..."}},
    "is_initial":      {{"was": false, "should_be": false, "reason": "..."}},
    "is_final":        {{"was": false, "should_be": false, "reason": "..."}},
    "invariant_cond":  {{"was": "...", "should_be": "...", "reason": "..."}},
    "object_name":     {{"was": "...", "should_be": "...", "reason": "..."}},
    "field_name":      {{"was": "...", "should_be": "...", "reason": "..."}},
    "field_type":      {{"was": "...", "should_be": "...", "reason": "..."}},
    "required":        {{"was": false, "should_be": false, "reason": "..."}},
    "allowed_values":  {{"was": [], "should_be": [], "reason": "..."}},
    "guard_semantics": {{"was": "...", "should_be": "...", "reason": "..."}},
    "from_state":      {{"was": "...", "should_be": "...", "reason": "..."}},
    "to_state":        {{"was": "...", "should_be": "...", "reason": "..."}},
    "trigger":         {{"was": "...", "should_be": "...", "reason": "..."}},
    "pre_cond":        {{"was": "...", "should_be": "...", "reason": "..."}},
    "post_cond":       {{"was": "...", "should_be": "...", "reason": "..."}},
    "actions":         {{"was": [], "should_be": [], "reason": "..."}},
    "actors":          {{"was": [], "should_be": [], "reason": "..."}},
    "modality":        {{"was": "...", "should_be": "...", "reason": "..."}},
    "is_inferred":     {{"was": false, "should_be": false, "reason": "..."}},
    "confidence":      {{"was": 0.0, "should_be": 0.0, "reason": "..."}},
    "source_section":  {{"was": "...", "should_be": "...", "reason": "..."}},
    "id":              {{"was": "...", "should_be": "...", "reason": "..."}}
  }}
}}

Use null for any correction key you are not changing. "was" must match the
current statement value for that field. "should_be" is the corrected value
(string, number, boolean, array, or null as appropriate). Never change
statement_id.

SOURCE TEXT (section {{section_id}} -- {{title}}):
{{section_text}}

STATEMENT TO VERIFY:
{{statement_json}}
""".strip()


def _stage_b_prompt() -> str:
    return f"""
You are building the complete protocol state machine (FSM) for the {_P}
protocol from statements extracted from its specification.

The input stage label is "{_STAGE}", which is only a pipeline bucket. Do not
invent phase-boundary states and leave inter_stage entry/exit lists empty
unless the input explicitly requires otherwise.

Work only from the statements given to you. You have no other sources: do not
rely on outside documents, and do not add states, events, or edges that the
input does not support.

Your tasks:
1. Deduplicate states and normalize obvious aliases only when the input makes
   them equivalent. Do not introduce state names absent from the input.
2. Keep only protocol states. Discard message fields, option names, variables,
   and algorithms that are not FSM states.
3. Build transitions from IMPLICIT_TRANSITION and BEHAVIORAL entries. If a
   transition references a state absent from fsm_input.states, add that state
   only when the input grounds it.
4. Do not invent transitions. If an edge is plausible but not grounded in the
   input, record it in unresolved or gaps instead.

FORMATTING CONVENTIONS (apply to every state name, trigger, and action):
{BENCHMARK_CONVENTIONS}

Output exactly one valid JSON object:
{{
  "stage": "{_STAGE}",
  "states": [
    {{
      "state_name": "<state name from the input>",
      "semantic_type": "initial",
      "actor": "endpoint",
      "is_initial": true,
      "is_final": false,
      "invariant_cond": "protocol.state = <state name from the input>",
      "is_inferred": false,
      "confidence": 1.0,
      "source_ids": ["D-S014-001"]
    }}
  ],
  "transitions": [
    {{
      "from_state": "<source state from the input>",
      "to_state": "<destination state from the input>",
      "trigger": "<event from the input>",
      "guard": "protocol.state = <source state from the input>",
      "actors": ["client", "server"],
      "modality": "unspecified",
      "actions": ["<transition action from the input>"],
      "is_inferred": false,
      "confidence": 1.0,
      "source_ids": ["D-S014-010"]
    }}
  ],
  "inter_stage": {{
    "entry_from_previous": [],
    "exit_to_next": []
  }},
  "discarded_states": [],
  "unresolved": [],
  "gaps": []
}}

Constraints:
- semantic_type must be one of: initial, active, closing, terminal, error.
- actor must be one of: endpoint, user, remote_endpoint, shared, unknown.
- Exactly one state must have is_initial true.
- Each (from_state, to_state, trigger) combination must be unique.
- State, event, and action wording must stay close to the source wording while
  respecting the formatting conventions above.
- Return only JSON. No markdown, no commentary.
""".strip()


_RAW_BUILDERS = {
    "stage_a1": _stage_a1_prompt,
    "stage_a2": _stage_a2_prompt,
    "verify_stage_a12_n": _verify_n_prompt,
    "verify_stage_a12_d": _verify_d_prompt,
    "stage_b1": _stage_b_prompt,
}


def stage_label(protocol: str) -> str:
    return f"{protocol.lower()}_fsm"


def subjects(protocol: str) -> list[str]:
    return sorted({protocol.lower(), *BASE_SUBJECTS})


def _substitute(text: str, protocol: str) -> str:
    subject_list = "|".join(subjects(protocol))
    return (
        text.replace(_SUBJ, subject_list)
        .replace(_STAGE, stage_label(protocol))
        .replace(_PL, protocol.lower())
        .replace(_P, protocol)
    )


def build_prompts(protocol: str) -> dict[str, str]:
    """Rendered system prompts + the two Stage A user templates."""
    out = {key: _substitute(builder(), protocol)
           for key, builder in _RAW_BUILDERS.items()}
    out["a1_user_template"] = _substitute(A1_USER_TEMPLATE, protocol)
    out["a2_user_template"] = _substitute(A2_USER_TEMPLATE, protocol)
    return out


class PromptLintError(RuntimeError):
    """A rendered prompt violates one of the neutrality invariants."""


def _section_pointer_hits(text: str) -> list[str]:
    """Numbers like "3.3.2" that would point the model at a specific section."""
    hits = []
    for match in SECTION_POINTER_RE.finditer(text):
        token = match.group(0)
        if token in _SCHEMA_NUMERIC_ALLOW:
            continue
        hits.append(token)
    return hits


def lint_prompts(rendered: dict[str, str], *, protocol: str) -> None:
    """Hard-fail on anything that would make a prompt protocol-specific."""
    problems: list[str] = []
    for key, text in rendered.items():
        for placeholder in (_P, _PL, _STAGE, _SUBJ):
            if placeholder in text:
                problems.append(f"{key}: unsubstituted placeholder {placeholder}")
        if URL_RE.search(text):
            problems.append(f"{key}: contains a URL")
        rfc_hits = [m.group(1) for m in RFC_NUMBER_RE.finditer(text)
                    if m.group(1) not in _RFC_ALLOW]
        if rfc_hits:
            problems.append(f"{key}: RFC number reference(s) {sorted(set(rfc_hits))}")
        for token in RESIDUE_TOKENS:
            if re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text,
                         flags=re.IGNORECASE):
                problems.append(f"{key}: contains {token!r}")
        pointers = _section_pointer_hits(text)
        if pointers:
            problems.append(f"{key}: section-number pointer(s) {sorted(set(pointers))}")

    for key in USER_TEMPLATE_KEYS:
        fields = set(re.findall(r"\{(\w+)\}", rendered[key]))
        if fields != {"section_id", "parent_path", "section_text"}:
            problems.append(f"{key}: unexpected format fields {sorted(fields)}")

    for key in ("verify_stage_a12_n", "verify_stage_a12_d"):
        for placeholder in ("{section_id}", "{title}", "{section_text}",
                            "{statement_id}", "{statement_json}"):
            if placeholder not in rendered[key]:
                problems.append(f"{key}: missing placeholder {placeholder}")

    if problems:
        raise PromptLintError(
            f"prompt lint failed for {protocol}:\n  " + "\n  ".join(problems)
        )


def _mask(text: str, protocol: str) -> str:
    """Replace every protocol-derived token so two protocols' prompts can be compared."""
    # Order matters: collapse the composite tokens (subject list, stage label)
    # BEFORE the bare name, otherwise the name inside them is masked first and the
    # composite no longer matches. The subject list also has to go as a whole,
    # because the protocol's own name sorts to a different alphabetical position in
    # each list — a benign difference that must not read as a prompt divergence.
    masked = text.replace("|".join(subjects(protocol)), "<subjects>")
    masked = masked.replace(stage_label(protocol), "<stage>")
    masked = masked.replace(protocol, "<P>")
    masked = masked.replace(protocol.lower(), "<p>")
    return masked


def assert_cross_protocol_identical(protocols: list[str]) -> dict[str, str]:
    """
    Every protocol must get the SAME prompt set modulo its own name. This is the
    positive counterpart to the lint: it proves no protocol received special
    treatment, without needing a list of words to forbid.
    """
    reference_protocol = protocols[0]
    reference = {k: _mask(v, reference_protocol)
                 for k, v in build_prompts(reference_protocol).items()}
    for protocol in protocols[1:]:
        masked = {k: _mask(v, protocol) for k, v in build_prompts(protocol).items()}
        for key in reference:
            if masked.get(key) != reference[key]:
                raise PromptLintError(
                    f"prompt {key!r} differs between {reference_protocol} and "
                    f"{protocol} beyond the protocol name"
                )
    return {key: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for key, value in sorted(reference.items())}


def template_sha256() -> str:
    """
    Protocol-INDEPENDENT identity of the prompt set: the hash of the raw builders
    before {protocol_name} substitution. This is the single number the paper
    freezes and that METHOD.md's re-run rule keys on; the rendered
    per-protocol hash necessarily differs between protocols.
    """
    raw = {key: builder() for key, builder in _RAW_BUILDERS.items()}
    raw["a1_user_template"] = A1_USER_TEMPLATE
    raw["a2_user_template"] = A2_USER_TEMPLATE
    raw["_base_subjects"] = "|".join(BASE_SUBJECTS)
    payload = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prompt_set_sha256(rendered: dict[str, str]) -> str:
    """Stable hash over the whole rendered set (order-independent)."""
    payload = json.dumps(rendered, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prompt_hashes(rendered: dict[str, str]) -> dict[str, str]:
    return {
        key: hashlib.sha256(value.encode("utf-8")).hexdigest()
        for key, value in sorted(rendered.items())
    }
