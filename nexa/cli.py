"""The terminal app: chat, the confirmation prompt, and a few local commands."""

from __future__ import annotations

import argparse
import sys

from . import config, llm
from .agent import Agent, ToolEvent
from .handlers import dispatch
from .market import Market
from .store import Store
from .tools import Context, Receipt

HELP = """Commands: /cart  /orders  /bookings  /budget  /profile  /help  /quit
Anything else goes to Nexa: "noise cancelling headphones under 150", "table for 2 in
Paris on Friday evening", "cheapest flight to Barcelona in three weeks"."""


def _dim(text: str) -> str:
    return f"\x1b[2m{text}\x1b[0m" if sys.stdout.isatty() else text


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _on_tool(event: ToolEvent) -> None:
    mark = {True: "+", False: "x", None: "·"}[event.approved]
    print(_dim(f"  {mark} {event.name}"))


def ask_confirm(receipt: Receipt) -> bool | str:
    """Show exactly what will be charged and wait for a yes."""
    print()
    print(receipt.render())
    try:
        answer = input("  approve? [y/N] ").strip()
    except EOFError:
        return "no answer"
    if answer.lower() in ("y", "yes", "oui", "ok"):
        return True
    return answer or "no"


def always_yes(receipt: Receipt) -> bool:
    print()
    print(receipt.render())
    print("  approved automatically (--yes)")
    return True


def chat(agent: Agent) -> int:
    print(f"Nexa — shopping and booking, on {agent.model}. /help for commands, /quit to leave.")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.startswith("/"):
            if _command(text, agent) is False:
                return 0
            continue
        try:
            reply = agent.send(text)
        except llm.LlmError as exc:
            print(f"\n[model] {exc}")
            continue
        except KeyboardInterrupt:
            print("\n[stopped]")
            continue
        print(f"\nNexa: {reply.text}" if reply.text else "\nNexa: (no answer)")


def _command(text: str, agent: Agent) -> bool:
    cmd = text.split()[0].lower()
    ctx = agent.ctx
    if cmd in ("/quit", "/exit", "/bye"):
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/cart":
        print(dispatch("view_cart", {}, ctx))
    elif cmd == "/orders":
        print(dispatch("list_orders", {}, ctx))
    elif cmd == "/bookings":
        print(dispatch("list_bookings", {}, ctx))
    elif cmd in ("/budget", "/profile"):
        print(dispatch("get_profile", {}, ctx))
    else:
        print(f"unknown command {cmd}. {HELP}")
    return True


def doctor(args) -> int:
    print(f"host   {config.HOST}\nmodel  {config.MODEL}\nstate  {config.STATE_FILE}")
    ok = True
    for passed, line in llm.doctor(model=args.model):
        print(f"  {'ok  ' if passed else 'FAIL'} {line}")
        ok = ok and passed
    try:
        market = Market()
        print(f"  ok   fixtures: {len(market.products)} products, {len(market.restaurants)} "
              f"restaurants, {len(market.hotels)} hotels, {len(market.flights)} flights")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        print(f"  FAIL fixtures: {exc}")
        ok = False
    print(f"  ok   limits: {config.money(config.MAX_ORDER)} per purchase, "
          f"{config.money(config.DAILY_CAP)} per day")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    _utf8()
    parser = argparse.ArgumentParser(prog="nexa", description="A shopping and booking agent on a local model.")
    parser.add_argument("command", nargs="?", default="chat", choices=["chat", "doctor"])
    parser.add_argument("--model", default=config.MODEL, help=f"ollama model (default {config.MODEL})")
    parser.add_argument("--host", default=config.HOST, help=f"ollama host (default {config.HOST})")
    parser.add_argument("--ask", metavar="TEXT", help="run one request and exit")
    parser.add_argument("--yes", action="store_true",
                        help="approve every purchase without asking (demos only)")
    parser.add_argument("--state", help="path to the state file (default data/state.json)")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor(args)

    ctx = Context(Store(args.state) if args.state else Store(), Market())
    agent = Agent(ctx, confirm=always_yes if args.yes else ask_confirm,
                  on_tool=_on_tool, model=args.model, host=args.host)
    if args.ask:
        try:
            reply = agent.send(args.ask)
        except llm.LlmError as exc:
            print(f"[model] {exc}", file=sys.stderr)
            return 1
        print(f"\nNexa: {reply.text}")
        return 0
    return chat(agent)
