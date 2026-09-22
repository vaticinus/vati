"""forecast-stack command line.

    forecast-stack providers
    forecast-stack cutoff [--provider P] [--model M]
    forecast-stack forecast --question "..." [--asof DATE] [--n 3]
    forecast-stack score --forecast FILE --resolutions FILE
    forecast-stack seal --question "..." --p 0.3 [--resolution-date DATE]
    forecast-stack verify --ledger PATH
    forecast-stack feeds list
    forecast-stack feeds run NAME
"""
from __future__ import annotations

import argparse
import runpy
import json
import sys
from pathlib import Path

from . import __version__, config, llm
from .eval import cutoff as cutoff_mod
from .eval import score as score_mod
from .harness import ledger as ledger_mod
from .model import llm_forecaster


def _default_ledger() -> Path:
    config.ensure_dirs()
    return config.LEDGER_DIR / "ledger.jsonl"


def cmd_providers(_args: argparse.Namespace) -> int:
    available = llm.available_providers()
    if not available:
        print("No provider configured. Set one of:")
        for provider in llm.PROVIDERS.values():
            if provider.key_env:
                print(f"  {provider.key_env:22s}  -> {provider.name} (default model: {provider.default_model})")
        print("\nOr run offline: FORECAST_STACK_MOCK=1 forecast-stack providers")
        return 1
    print("Available providers (first is used by default):")
    for name in available:
        spec = llm.PROVIDERS.get(name)
        model = spec.default_model if spec else "custom endpoint"
        print(f"  {name:12s}  model={model}")
    return 0


def cmd_cutoff(args: argparse.Namespace) -> int:
    measurement = cutoff_mod.measure_cutoff(provider=args.provider, model=args.model)
    for probe in measurement.probes:
        mark = "KNOWS" if probe.knows else "blind"
        print(f"  {probe.year}  {mark:5s}  {probe.question[:60]}")
    print(measurement.summary())
    if measurement.is_blind:
        print("  -> treat every dated outcome as potentially contaminated")
    return 0


def cmd_forecast(args: argparse.Namespace) -> int:
    context = Path(args.context).read_text() if args.context else None
    result = llm_forecaster.forecast(
        args.question, asof=args.asof, resolution_criteria=args.resolution_criteria,
        context=context, provider=args.provider, model=args.model, n=args.n,
    )
    if args.json:
        print(json.dumps({"probability": result.probability, "samples": result.samples,
                          "spread": result.spread, "provider": result.provider,
                          "model": result.model, "failed": result.failed}, indent=2))
    else:
        print(f"Probability: {result.probability}")
        print(f"  samples:  {result.samples}  (spread {result.spread:.3f})")
        print(f"  model:    {result.provider}/{result.model}")
        if result.reasoning:
            print("\n" + result.reasoning)
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    forecast = json.loads(Path(args.forecast).read_text())
    resolutions = json.loads(Path(args.resolutions).read_text())
    rows = resolutions.get("resolutions", resolutions) if isinstance(resolutions, dict) else resolutions
    rows = [r for r in rows if r.get("resolved")]
    result = (score_mod.score_submission(forecast, rows)
              if isinstance(forecast, dict) and "forecasts" in forecast
              else score_mod.brier(forecast, rows))
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


def cmd_seal(args: argparse.Namespace) -> int:
    path = Path(args.ledger) if args.ledger else _default_ledger()
    event = ledger_mod.append_forecast_sealed(
        path,
        question_id=args.question,
        run_id=args.run_id,
        member_id=args.member_id,
        probability=args.p,
        variant="independent",
        forecast_payload={
            "resolution_date": args.resolution_date,
            "resolution_criteria": args.resolution_criteria,
        },
    )
    print(f"sealed  {path}")
    print(f"  event_id    {event['event_id']}")
    print(f"  record_hash {event['record_hash']}")
    print("\nPublish the head hash to an independent timestamp witness. Local timestamps and git dates are editable.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.ledger) if args.ledger else _default_ledger()
    try:
        summary = ledger_mod.verify_event_chain(path)
    except ledger_mod.LedgerIntegrityError as exc:
        print(f"INVALID: {exc}")
        return 1
    print(json.dumps(summary, indent=2))
    return 0


def cmd_feeds_list(_args: argparse.Namespace) -> int:
    from . import feeds

    names = sorted(p.stem for p in Path(feeds.__file__).parent.glob("*.py")
                   if not p.stem.startswith("_") and p.stem != "__init__")
    print(f"{len(names)} keyless collectors:")
    for name in names:
        print(f"  {name}")
    return 0


def cmd_feeds_run(args: argparse.Namespace) -> int:
    from . import feeds

    # Collectors expose module entry points, not a uniform main() function.
    source = Path(feeds.__file__).parent / f"{args.name}.py"
    if not args.name.isidentifier() or args.name.startswith("_") or not source.is_file():
        print(f"unknown feed {args.name!r} — run `forecast-stack feeds list`")
        return 1
    previous_argv = sys.argv
    try:
        sys.argv = [str(source)]
        runpy.run_module(f"forecast_stack.feeds.{args.name}", run_name="__main__")
    finally:
        sys.argv = previous_argv
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forecast-stack", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"forecast-stack {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("providers", help="list configured LLM providers").set_defaults(func=cmd_providers)

    p_cutoff = sub.add_parser("cutoff", help="measure a model's effective knowledge cutoff")
    p_cutoff.add_argument("--provider")
    p_cutoff.add_argument("--model")
    p_cutoff.set_defaults(func=cmd_cutoff)

    p_forecast = sub.add_parser("forecast", help="forecast one question")
    p_forecast.add_argument("--question", required=True)
    p_forecast.add_argument("--asof", help="information cutoff, YYYY-MM-DD")
    p_forecast.add_argument("--resolution-criteria")
    p_forecast.add_argument("--context", help="path to a file of as-of information")
    p_forecast.add_argument("--n", type=int, default=1, help="samples to blend (self-consistency)")
    p_forecast.add_argument("--provider")
    p_forecast.add_argument("--model")
    p_forecast.add_argument("--json", action="store_true")
    p_forecast.set_defaults(func=cmd_forecast)

    p_score = sub.add_parser("score", help="Brier-score a forecast file against resolutions")
    p_score.add_argument("--forecast", required=True)
    p_score.add_argument("--resolutions", required=True)
    p_score.set_defaults(func=cmd_score)

    p_seal = sub.add_parser("seal", help="seal a forecast into the hash-chained ledger")
    p_seal.add_argument("--question", required=True)
    p_seal.add_argument("--p", type=float, required=True, help="probability in [0,1]")
    p_seal.add_argument("--resolution-date")
    p_seal.add_argument("--resolution-criteria")
    p_seal.add_argument("--run-id", default="run")
    p_seal.add_argument("--member-id", default="me")
    p_seal.add_argument("--ledger")
    p_seal.set_defaults(func=cmd_seal)

    p_verify = sub.add_parser("verify", help="verify the sealed ledger's hash chain")
    p_verify.add_argument("--ledger")
    p_verify.set_defaults(func=cmd_verify)

    p_feeds = sub.add_parser("feeds", help="list or run keyless data collectors")
    feeds_sub = p_feeds.add_subparsers(dest="feeds_command", required=True)
    feeds_sub.add_parser("list").set_defaults(func=cmd_feeds_list)
    p_run = feeds_sub.add_parser("run")
    p_run.add_argument("name")
    p_run.set_defaults(func=cmd_feeds_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
