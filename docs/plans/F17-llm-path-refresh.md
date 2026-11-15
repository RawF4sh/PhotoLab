# F17. LLM path refresh

Status: spec (2026-11-11). Size S. Plan entry: [FEATURE_PLAN.md](../FEATURE_PLAN.md), section F17.
Builds on [F2](F2-region-instructions.md): the `Adjustment` structure and the instruction parser.

## Goal

1. The optional LLM decider works with the current Haiku model.
2. The LLM can translate an instruction the local parser did not understand into F2's structure.
3. The budget cap has a sensible default and counts real token usage.

The LLM path stays off by default, stays behind explicit consent and a key, and still degrades to
the rules on any error. No test and no check in this feature calls the API.

## Facts from the Claude API reference (read on 2026-11-11)

- The current Haiku is `claude-haiku-5-5` (no date suffix). The packaged default,
  `claude-haiku-4-5-20251001`, predates it.
- On Claude Haiku 5.5 a request with a non-default `temperature` returns a 400. The decider sends
  `temperature=0.0` today, so with the new model id every call would fail and fall back to the
  rules. The parameter must go.
- Thinking is on by default on Claude Haiku 5.5 and counts toward `max_tokens`. A response can
  begin with `thinking` blocks. The decider already picks the `tool_use` block by type; it needs a
  larger `max_tokens` than 1024.
- A forced `tool_choice` (a named tool) is accepted by Claude Haiku 5.5 but returns a 400 on
  Claude Opus 5.5, Claude Sonnet 5.5 and the Fable models. `tool_choice: auto` with `strict: true`
  on the tool works on every current model and keeps the schema guarantee; the model may then
  answer without calling the tool, which the decider treats like any other failure (rules).
- A strict tool schema needs `additionalProperties: false` and `required`, so the open maps of
  today's schema (`{"exposure": 0.2, ...}`) become lists of `{op, value}` pairs with `op` as an
  enum. A made-up op name is then impossible.
- A safety classifier can decline a request: HTTP 200 with `stop_reason: "refusal"`. There is no
  server-side fallback for Claude Haiku 5.5. The decider treats a refusal as a failure (rules).
- `usage.input_tokens` and `usage.output_tokens` give the billed tokens of a call. Claude Haiku
  5.5 costs 0.10 USD per million input tokens and 0.50 USD per million output tokens for prompts
  up to 100,000 tokens.

## Decisions taken without the owner

| Decision | Chosen | Reverse it by |
|---|---|---|
| Default model | `claude-haiku-5-5` | `[llm] model` in `defaults.toml` |
| Default budget cap per run | 1.00 USD (was: unlimited). At the default model that is well over a thousand photos | `[llm] budget_usd`, 0 = unlimited |
| `[llm] temperature` | Still accepted in config files, no longer sent: current models reject it | Nothing to reverse |
| Instruction translation | On whenever the LLM decider is active and `--edit` has a clause the parser did not understand. Only the text of those clauses is sent, no image | `[llm] translate_instructions = false` |

## Files

| File | Change |
|---|---|
| `src/autoedit/decide/llm_decider.py` | request shape, usage-based cost, refusal handling, `translate` |
| `src/autoedit/decide/__init__.py` | pass the two price settings; the disclosure text mentions instruction text |
| `src/autoedit/contract.py` | default model id, default budget, `llm_input_usd_per_mtok`, `llm_output_usd_per_mtok`, `llm_translate_instructions` |
| `src/autoedit/configs/defaults.toml`, `src/autoedit/cli.py` | the `[llm]` keys |
| `src/autoedit/instructions.py` | `clause_of(problem)` |
| `src/autoedit/pipeline.py` | in `build`: translate the clauses the parser rejected, when the decider can |
| `tests/test_llm_refresh.py` | new |

## `decide/llm_decider.py`

Protected file (the network path). Changes:

1. Constructor: `model="claude-haiku-5-5"`, new arguments `input_usd_per_mtok=0.10`,
   `output_usd_per_mtok=0.50`. `temperature` stays an accepted argument and is not used.
   `_COST_PER_CALL_USD` goes away.
2. One tool schema builder, strict:

```python
def _edits_tool(region_names: list[str]) -> dict:
    """The strict tool the model answers with. Ops and region names are enums, so the model
    cannot invent either."""
    edit = {"type": "object", "additionalProperties": False, "required": ["op", "value"],
            "properties": {"op": {"type": "string", "enum": sorted(OP_BOUNDS)},
                           "value": {"type": "number"}}}
    region = {"type": "object", "additionalProperties": False, "required": ["name", "edits"],
              "properties": {"name": {"type": "string", "enum": sorted(region_names)},
                             "edits": {"type": "array", "items": edit}}}
    props = {"global": {"type": "array", "items": edit}}
    if region_names:
        props["regions"] = {"type": "array", "items": region}
    return {"name": "set_edits", "description": "Return the edit values for the photo.",
            "strict": True,
            "input_schema": {"type": "object", "additionalProperties": False,
                             "required": sorted(props), "properties": props}}
```

3. `_call_llm`:

```python
        msg = client.messages.create(
            model=self.model,
            max_tokens=4096,           # thinking is on by default and counts toward the cap
            tools=[_edits_tool(list(regions))],
            tool_choice={"type": "auto"},   # a forced call is rejected by several current models
            messages=[{"role": "user", "content": content}],
        )
        self.calls += 1
        self.spent += self._cost(msg)
        if getattr(msg, "stop_reason", None) == "refusal":
            raise RuntimeError("the model declined the request")
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return _from_pairs(block.input)
        raise RuntimeError("LLM returned no tool_use block")
```

   with

```python
    def _cost(self, msg) -> float:
        usage = getattr(msg, "usage", None)
        tokens_in = float(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = float(getattr(usage, "output_tokens", 0) or 0)
        return (tokens_in * self.input_usd_per_mtok + tokens_out * self.output_usd_per_mtok) / 1e6


def _from_pairs(data: dict) -> dict:
    """The tool's lists of {op, value} pairs, back in the shape `_merge` reads."""
    def ops(pairs):
        return {p["op"]: p["value"] for p in pairs or [] if isinstance(p, dict) and "op" in p}
    return {"global": ops(data.get("global")),
            "regions": {r["name"]: ops(r.get("edits")) for r in data.get("regions") or []
                        if isinstance(r, dict) and "name" in r}}
```

   The prompt gets one more sentence at its end: `Call the set_edits tool exactly once.` The op
   list in the prompt is replaced by `", ".join(sorted(OP_BOUNDS))` so it cannot drift from the
   engine. `_merge` and `_apply_proposal` do not change: they still drop unknown regions and ops
   and clamp every value.

4. Instruction translation:

```python
    def translate(self, clauses: list[str], ops: list[str]) -> list[dict]:
        """Ask the model to turn instruction clauses the local parser did not understand into
        [{clause, target, edits: [{op, value}]}]. Text only: no image is sent. Returns [] when
        capped or on any error. The caller validates and clamps everything."""
        if not clauses or self._capped():
            return []
        tool = {
            "name": "set_adjustments", "strict": True,
            "description": "Return the adjustments the instructions ask for.",
            "input_schema": {"type": "object", "additionalProperties": False, "required": ["adjustments"],
                             "properties": {"adjustments": {"type": "array", "items": {
                                 "type": "object", "additionalProperties": False,
                                 "required": ["clause", "target", "edits"],
                                 "properties": {
                                     "clause": {"type": "integer"},
                                     "target": {"type": "string"},
                                     "edits": {"type": "array", "items": {
                                         "type": "object", "additionalProperties": False,
                                         "required": ["op", "value"],
                                         "properties": {"op": {"type": "string", "enum": sorted(ops)},
                                                        "value": {"type": "number"}}}}}}}}}}
        numbered = "\n".join(f"{i}: {c}" for i, c in enumerate(clauses))
        prompt = (
            "A photo editor received instructions it could not parse. Turn each one into "
            "adjustments, or leave it out when it asks for nothing, forbids something, or cannot be "
            "expressed.\n"
            "An adjustment names a target (a short noun phrase for the thing in the photo, in "
            "lower case, or the word global for the whole photo) and edits. Edit ops and their "
            "usual size: exposure in stops (a normal step is 0.3), temp (warmer is positive, a "
            "normal step is 10), saturation (12), clarity (12), contrast (8). Use only the ops the "
            "tool allows. An instruction that excludes something (\"everything but the sky\") "
            "has no safe translation: leave it out.\n"
            f"Instructions, numbered:\n{numbered}\n"
            "Call the set_adjustments tool exactly once.")
        try:
            msg = self._client_or_none().messages.create(
                model=self.model, max_tokens=4096, tools=[tool], tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": prompt}])
            self.calls += 1
            self.spent += self._cost(msg)
            if getattr(msg, "stop_reason", None) == "refusal":
                return []
            for block in msg.content:
                if getattr(block, "type", None) == "tool_use":
                    return list(block.input.get("adjustments") or [])
        except Exception as e:
            print(f"llm instruction translation skipped: {e}", file=sys.stderr)
        return []
```

## `decide/__init__.py`

`make_decider` passes `input_usd_per_mtok=cfg.llm_input_usd_per_mtok` and
`output_usd_per_mtok=cfg.llm_output_usd_per_mtok`. `DISCLOSURE` gains one sentence: `With --edit,
the text of an instruction the local parser did not understand is sent too.`

## `contract.py`, `defaults.toml`, `cli.py`

- `llm_model: str = "claude-haiku-5-5"`.
- `llm_budget_usd: float = Field(default=1.0, ge=0.0)`, comment `# per run; 0 = unlimited`.
- `llm_input_usd_per_mtok: float = Field(default=0.10, ge=0.0)` and
  `llm_output_usd_per_mtok: float = Field(default=0.50, ge=0.0)`, comment
  `# prices of llm_model, for the budget cap; update them when you change the model`.
- `llm_translate_instructions: bool = True`.
- `[llm]` in `defaults.toml`: `model = "claude-haiku-5-5"`, `budget_usd = 1.0`,
  `input_usd_per_mtok = 0.10`, `output_usd_per_mtok = 0.50`, `translate_instructions = true`; the
  `temperature` line gets the comment `# accepted for older run files; current models reject it,
  so it is not sent`. `_flatten`'s `_llm_map` gains the three new keys.

## `instructions.py`

```python
def clause_of(problem: str) -> str:
    """The instruction clause inside a problem line (`not understood ...: "<clause>"`)."""
    start, end = problem.find('"'), problem.rfind('"')
    return problem[start + 1:end] if 0 <= start < end else ""


def from_translation(items: list[dict], clauses: list[str], vocab: Vocab, known) -> list[Adjustment]:
    """Validate what the LLM returned for rejected clauses. Anything odd is dropped: an unknown
    clause number, a target that fails the same checks as a parsed one, an op without an
    instruction limit, a value that is not a finite number. The deltas are clamped later, with all
    other instruction deltas, by the limits in instructions.toml."""
    out: list[Adjustment] = []
    known = set(known)
    for item in items:
        try:
            clause = clauses[int(item["clause"])]
            target = _canonical(" ".join(_toks(str(item["target"]))), known, vocab)
            ops = {str(e["op"]): float(e["value"]) for e in item["edits"]
                   if str(e["op"]) in vocab.limits and math.isfinite(float(e["value"]))}
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if target is None or not ops or any(ch.isdigit() for ch in target):
            continue
        out.append(Adjustment(target=target, ops=ops, source=clause))
    return out
```

## `pipeline.py`, in `build`

After the local parse, and only when there are problems, the decider is the LLM decider and
`cfg.llm_translate_instructions` is true:

```python
            if problems and cfg.llm_translate_instructions and hasattr(decider, "translate"):
                clauses = [instructions.clause_of(p) for p in problems]
                extra = instructions.from_translation(
                    decider.translate(clauses, sorted(vocab.limits)), clauses, vocab, known)
                done = {a.source for a in extra}
                adjustments += extra
                problems = [p for p in problems if instructions.clause_of(p) not in done]
```

`decider` is the object `make_decider(cfg)` returns; build it before this block and pass the same
object to the `Pipeline`. A clause the model left out stays in `problems` and is reported as
before.

## Task

One Builder (Sonnet, high: the network path is a protected file). Proving command:
`PYTHONPATH=src python3 -m pytest tests/test_llm_refresh.py tests/test_llm_merge.py tests/test_byokey.py tests/test_instructions.py -q`,
then `PYTHONPATH=src python3 tests/run_all.py`.

**No API call.** Every test replaces `LlmDecider._client_or_none` (or sets `_client`) with a fake
whose `messages.create(**kwargs)` records the arguments and returns a hand-made object with
`content`, `usage` and `stop_reason`. The Builder does not run anything that needs a key.

## Tests: `tests/test_llm_refresh.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_request_shape` | With a fake client: the request has `model == "claude-haiku-5-5"` by default, no `temperature`, `top_p` or `top_k` key, `tool_choice == {"type": "auto"}`, `max_tokens >= 4096`, one tool with `strict` true, whose schema has `additionalProperties` false at every object level and whose `op` enum equals the engine's op names; the region enum equals the baseline's region names; with no regions the schema has no `regions` property |
| 2 | `test_reply_is_merged_and_bounded` | A fake reply `{"global": [{"op": "exposure", "value": 9}], "regions": [{"name": "sky", "edits": [{"op": "contrast", "value": 500}]}]}` in refine mode ends within the refine limits and the op bounds, exactly as the existing merge tests expect for the map form |
| 3 | `test_thinking_blocks_are_skipped` | A reply whose content starts with a `thinking` block and then the `tool_use` block works |
| 4 | `test_refusal_and_missing_tool_fall_back_to_rules` | `stop_reason == "refusal"` gives the rules baseline; a reply with only a text block gives the rules baseline; a client that raises gives the rules baseline |
| 5 | `test_budget_counts_real_usage` | Two calls with `usage(input_tokens=2000, output_tokens=1000)` at the default prices add 0.0014 USD to `spent` (within 1e-9); with `budget_usd=0.001` the second photo is not sent (the fake records one call) |
| 6 | `test_defaults` | `RunConfig().llm_model == "claude-haiku-5-5"`, `llm_budget_usd == 1.0`; the packaged `defaults.toml` agrees; a run file with `[llm] temperature = 0.3` still loads |
| 7 | `test_translate_request_and_result` | `translate(["make the lake pop"], ops)` sends text only (no image block), a strict tool with the op enum, and returns the fake's list; when capped it returns `[]` without a call; when the client raises it returns `[]` |
| 8 | `test_from_translation_validates` | Items with a bad clause number, a six-word target, a target with a digit, an op that has no instruction limit, a `nan` value are dropped; a good item becomes an `Adjustment` with the clause as `source`; `"Whole Photo"` becomes the target `global` |
| 9 | `test_pipeline_uses_the_translation` | A pipeline built with a fake LLM decider (replace `decide.make_decider` for the build, restored in `finally`; the fake has `name = "llm"`, a `decide` that returns the rules result, and a `translate` that returns one adjustment for clause 0) and `edit="make the lake pop, brighten the sky"`: `pipe.adjustments` holds the parsed sky adjustment and the translated one; `pipe.adjust_problems` is empty. With `llm_translate_instructions=False`, or with the rules decider, the clause stays in `adjust_problems` and `translate` is not called |
| 10 | `test_no_default_run_touches_the_llm` | `RunConfig()` builds a pipeline whose decider is the rules decider, with `edit` set to a clause the parser rejects as well |

The existing `tests/test_llm_merge.py` and `tests/test_byokey.py` must pass; where one of them
builds the old map-shaped reply for `_call_llm`, adapt it to the pair shape and report it.

## Acceptance checks (the session runs these)

1. Both test suites green; default renders byte-identical to the baseline.
2. Checker review of `llm_decider.py`.
3. Not run, and listed as open for the owner: one real call with the owner's key and consent.

## Result

Built on 2026-11-11, in the unattended run. Status: done as code; not verified against the live
API (this run makes no API call).

- **Built** by one Builder (Sonnet, high) from this spec. In the protected file
  `decide/llm_decider.py` only the listed changes were made: the default model
  `claude-haiku-5-5`, no `temperature` in the request, `tool_choice` auto with a strict tool whose
  edits are lists of `{op, value}` pairs, `max_tokens` 4096, a refusal treated like any other
  failure (rules), the cost counted from the token usage with two price settings, and the
  translation of instruction clauses that the local parser did not understand.
- **Still true:** the LLM path is off by default, needs `allow_cloud` and a key, sends a
  thumbnail and statistics only, and falls back to the rules on a missing package, a missing key,
  the budget cap or any error.
- **New defaults:** a budget cap of 1.00 USD per run (0 = unlimited) and instruction translation
  on whenever the LLM decider is active; both are listed above with how to change them.
- **Deviation accepted:** the budget test uses a cap of 0.0005 USD, because one call costs 0.0007
  USD at the configured prices and the cap of the spec's table (0.001) would have let a second
  call through.
- **Tests:** `tests/test_llm_refresh.py`, ten tests with a stand-in client. The Builder showed
  that the tests of the tool choice, the cost, the refusal and the translated clauses fail when
  their feature is undone.
- **Final check** (Checker, Opus, high), fixed in "Review round 2" in the [F11 spec](F11-user-config-layer.md): the translation was given
  every clause the local parser had rejected, including the ones it rejects for safety, so
  "don't brighten the face" could come back as an instruction to brighten it. Now a clause
  rejected as a negation or an exclusion is never sent. Also: a clause index outside the list is
  dropped, a value that is not finite is dropped, and one process asks only once for the same
  text. As built, the translation gets only clauses that were plainly "not understood"; a clause
  rejected for any stated reason (negation, exclusion, two verbs, numbers, no target) is not
  sent. That is stricter than this spec's first idea, on purpose. The Checker's own probe with a stand-in client confirmed what leaves the machine: a
  thumbnail without metadata, statistics, the brief and the text of the clauses; no path, no
  file name; the key appears in no sidecar, manifest, note or error line.
- **Not verified in this run:** a real request. Nobody has seen the live API accept the strict
  tool schema or answer with the new model. One run with the owner's key and consent closes it:
  `autoedit run <photos> <out> --decider llm --allow-cloud`, then read the notes in a sidecar.
