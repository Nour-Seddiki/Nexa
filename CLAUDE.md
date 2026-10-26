# CLAUDE.md - Nexa

Shopping and booking agent on a local Qwen3.5 9B through Ollama. Loop in `agent.py`,
tools in `tools.py` (schemas + receipts) and `handlers.py` (what they do), merchant side
in `market.py`. `README.md` has the design writeup.

## Commands

The project venv one level up is blocked by Windows Application Control. Use the signed
system interpreter:

```bash
PY="C:/Users/seddi/AppData/Local/Programs/Python/Python314/python.exe"

$PY -m unittest discover -s tests -t .     # 61 tests, <1s, no model and no network
$PY -m compileall -q nexa tests            # syntax gate
$PY -m nexa doctor                         # ollama up, model present, tool calling works
$PY -m nexa --ask "..." --state $TEMP/x.json   # one request against the real model (~15s)
$PY -m nexa                                # the chat
```

Set `PYTHONIOENCODING=utf-8` when redirecting output: receipts print euro signs.

`--yes` approves every receipt. Demos only, and never in a test of the gate.

## Conventions

- **Stdlib only.** No third-party imports anywhere, including the tests (`unittest`, not
  pytest). Ollama is reached with `urllib` in `llm.py`. Keep it that way: it is what
  makes the suite run on a bare interpreter.
- **The gate is the product.** Anything that spends money, takes a table or cancels
  belongs in `tools.CONFIRM`, which means it must have a branch in `tools.preview`
  returning a `Receipt`. `Agent._run` asks the human; `handlers.dispatch` re-prices and
  refuses if the total moved. A new committing tool without a preview branch is a bug,
  not a missing feature.
- **Prices are computed from the store and the market, never read off the model's
  arguments.** `test_the_receipt_is_priced_here_not_by_the_model` pins this.
- **The default `confirm` denies.** Never make a permissive one the default to simplify
  a caller.
- **Caps before the human.** `tools._cap_check` runs inside `preview`, so an over-limit
  purchase never even reaches the prompt.
- **Tools return strings, and failures return `ERROR: ...` instead of raising**, so the
  model can correct its next call. `dispatch` is the only place that catches.
- **Merchant text is fenced** with `tools._fence` before it reaches the model, and the
  prompt says instructions inside the fence are not the agent's to follow. Any new tool
  that returns shop-written text fences it too.
- **`market.py` is the provider seam.** Fixtures in `nexa/fixtures/*.json`; availability
  and fares are derived with `_seed`/`_pick` so they are varied but deterministic -
  tests depend on that. Swapping in a real provider means keeping the method names and
  return shapes.
- **One state file** (`data/state.json`), written atomically by `store.Store`. Tests use
  `tests.helpers.temp_context`, never the real one.

## Gotchas

- Ollama wants the assistant message with its `tool_calls` echoed back before the `tool`
  results, and the result messages carry `tool_name`. `Agent._context` only ever cuts
  history at a `user` turn so a tool result is never orphaned.
- `message["thinking"]` from Qwen3.5 is dropped: it is not shown and not fed back.
- Fares and room availability depend on *today*, so tests build dates with
  `tests.test_market.in_days` / `next_weekday` rather than hardcoding them.
- A restaurant with `deposit_per_person` charges money, so `book_restaurant` is gated
  even when the deposit works out to zero.
