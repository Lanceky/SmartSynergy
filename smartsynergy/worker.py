"""SmartSynergy worker CLI.

The agent is a cron job, not an app you open. Everything a scheduler needs is here:

    python -m smartsynergy.worker init-watch fixtures/panel.json
    python -m smartsynergy.worker run-watch kras-nsclc
    python -m smartsynergy.worker run-due          # what cron actually calls
    python -m smartsynergy.worker inbox
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from smartsynergy.config import get_settings
from smartsynergy.schemas import Watch
from smartsynergy.storage import RunInProgress, Store

_STATE_ICON = {"open": "[ ]", "acknowledged": "[ack]", "dismissed": "[x]", "acted_on": "[done]"}


def _store() -> Store:
    return Store(get_settings().db_path)


def cmd_init_watch(args: argparse.Namespace) -> int:
    watch = Watch.model_validate(json.loads(Path(args.path).read_text()))
    with _store() as store:
        store.save_watch(watch)
    print(f"watch '{watch.id}' saved: {watch.gene} {watch.variant} in {watch.disease}")
    for h in watch.hypotheses:
        print(f"  - {h.id}: {h.compound_a} + {h.compound_b}")
    print(f"schedule: {watch.schedule_cron}")
    return 0


def cmd_run_watch(args: argparse.Namespace) -> int:
    from smartsynergy.controller import run_watch

    settings = get_settings()
    with _store() as store:
        if store.runs_today(args.watch_id) >= settings.max_runs_per_day and not args.force:
            print(f"daily run cap reached ({settings.max_runs_per_day}); use --force to override")
            return 2
        try:
            summary = run_watch(
                args.watch_id,
                trigger=args.trigger,
                settings=settings,
                store=store,
                hypothesis_ids=args.hypothesis or None,
            )
        except RunInProgress as exc:
            print(f"refusing to start: {exc}")
            return 2

    print()
    print(f"run {summary.run_id}: {summary.status} in {summary.duration_s}s")
    print(f"  hypotheses checked : {summary.hypotheses_checked}")
    print(f"  new sources        : {summary.papers_new}")
    print(f"  coverage           : {summary.coverage}")
    print(f"  needs your decision: {summary.review_items_created}")
    if summary.error:
        print(f"  error              : {summary.error}")
    return 0 if summary.status == "completed" else 1


def cmd_run_due(args: argparse.Namespace) -> int:
    """Entry point for cron: run every enabled watch, never fail the whole sweep on one error."""
    from smartsynergy.controller import run_watch

    settings = get_settings()
    failures = 0
    with _store() as store:
        watches = [w for w in store.list_watches() if w.enabled]
        if not watches:
            print("no enabled watches")
            return 0
        for watch in watches:
            if store.runs_today(watch.id) >= settings.max_runs_per_day:
                print(f"{watch.id}: daily cap reached, skipping")
                continue
            try:
                summary = run_watch(watch.id, trigger="scheduled", settings=settings, store=store, verbose=False)
                print(
                    f"{watch.id}: {summary.status} | +{summary.papers_new} sources | "
                    f"{summary.review_items_created} need review | {summary.duration_s}s"
                )
                failures += summary.status != "completed"
            except RunInProgress:
                print(f"{watch.id}: already running, skipping")
            except Exception as exc:  # noqa: BLE001
                print(f"{watch.id}: FAILED {type(exc).__name__}: {exc}")
                failures += 1
    return 1 if failures else 0


def cmd_inbox(args: argparse.Namespace) -> int:
    with _store() as store:
        items = store.list_inbox(args.watch_id, state=None if args.all else "open")
        if not items:
            print("Inbox zero. The watch ran and found nothing worth your attention.")
            return 0
        print(f"{len(items)} item(s) awaiting a decision\n")
        for it in items:
            icon = _STATE_ICON.get(it.state, "[?]")
            ver = f"v{it.before_version}->v{it.after_version}" if it.before_version else f"v{it.after_version}"
            print(f"{icon} {it.id}  {it.hypothesis_id}  ({it.reason}, urgency {it.urgency})")
            print(f"     {it.headline}")
            print(f"     why: {it.explanation}")
            print(f"     {ver} | new sources: {', '.join(it.new_source_ids) or 'none'}")
            print()
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    with _store() as store:
        store.record_decision(args.item_id, args.state, args.note or "")
    print(f"{args.item_id} -> {args.state}")
    return 0


def cmd_list_runs(args: argparse.Namespace) -> int:
    with _store() as store:
        runs = store.list_runs(args.watch_id, limit=args.limit)
    if not runs:
        print("no runs yet")
        return 0

    rows = [
        [
            r.id,
            r.watch_id,
            r.status,
            r.trigger,
            str(r.papers_new),
            r.coverage,
            f"{r.started_at:%Y-%m-%d %H:%M}",
        ]
        for r in runs
    ]
    header = ["RUN", "WATCH", "STATUS", "TRIGGER", "NEW", "COVERAGE", "STARTED"]
    # Size each column to its widest value; a fixed width silently runs the
    # watch id into the status column for any id longer than the guess.
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(header)]

    def line(cells: list[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    print(line(header))
    for r, row in zip(runs, rows):
        print(line(row))
        if r.error:
            print(f"  {'':<{widths[0]}}error: {r.error}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from smartsynergy.export import dossier_json, dossier_markdown

    with _store() as store:
        dossier = store.latest_dossier(args.hypothesis_id)
        if dossier is None:
            print(f"no dossier for {args.hypothesis_id}")
            return 1
        claims = store.get_claims(dossier.claim_ids)
        sources = store.get_sources(c.source_id for c in claims)
        watch = store.get_watch(dossier.watch_id)
    question = ""
    if watch:
        question = next((h.question for h in watch.hypotheses if h.id == args.hypothesis_id), "")

    text = dossier_json(dossier, claims, sources) if args.format == "json" else dossier_markdown(
        dossier, claims, sources, question
    )
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from smartsynergy import doctor

    return doctor.run(get_settings(), skip_aws=args.offline)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smartsynergy", description="Background evidence watch for combination-therapy hypotheses.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("doctor", help="check dependencies, database, data sources and Bedrock access")
    s.add_argument("--offline", action="store_true", help="skip the AWS checks")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("init-watch", help="create or replace a watch from a JSON panel file")
    s.add_argument("path")
    s.set_defaults(func=cmd_init_watch)

    s = sub.add_parser("run-watch", help="run one watch now")
    s.add_argument("watch_id")
    s.add_argument("--trigger", default="manual", choices=["manual", "scheduled", "backfill"])
    s.add_argument("--hypothesis", action="append", help="limit to specific hypothesis ids")
    s.add_argument("--force", action="store_true", help="ignore the daily run cap")
    s.set_defaults(func=cmd_run_watch)

    s = sub.add_parser("run-due", help="run every enabled watch (cron entry point)")
    s.set_defaults(func=cmd_run_due)

    s = sub.add_parser("inbox", help="show decisions waiting for a human")
    s.add_argument("watch_id", nargs="?")
    s.add_argument("--all", action="store_true", help="include resolved items")
    s.set_defaults(func=cmd_inbox)

    s = sub.add_parser("decide", help="record a human decision on a review item")
    s.add_argument("item_id")
    s.add_argument("state", choices=["acknowledged", "dismissed", "acted_on"])
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("list-runs", help="run history with coverage and errors")
    s.add_argument("watch_id", nargs="?")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_list_runs)

    s = sub.add_parser("export", help="export the latest dossier with full evidence receipts")
    s.add_argument("hypothesis_id")
    s.add_argument("--format", default="markdown", choices=["markdown", "json"])
    s.add_argument("--out")
    s.set_defaults(func=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
