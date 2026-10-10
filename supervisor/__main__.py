import argparse
import functools
import http.server
import json
from pathlib import Path

from .engine import Supervisor, initialize
from .reports import render
from .safety import Blocked, git


def main():
    parser = argparse.ArgumentParser(description="Independent improvement supervisor prototype")
    parser.add_argument("--runtime", type=Path, default=Path("runtime-demo"))
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--source", type=Path)
    init.add_argument("--executor", choices=["direct", "ralph"], default="direct")
    for name in ("once", "pause", "resume", "stop", "status", "report"):
        sub.add_parser(name)
    run = sub.add_parser("run")
    run.add_argument("--max-cycles", type=int)
    approve = sub.add_parser("approve")
    approve.add_argument("proposal_id")
    export = sub.add_parser("export")
    export.add_argument("cycle_id")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.runtime, args.source, args.executor)
        supervisor = Supervisor(args.runtime)
        try:
            if args.command in {"pause", "resume", "stop"}:
                supervisor.control({"pause": "paused", "resume": "running", "stop": "stopped"}[args.command])
            elif args.command == "approve":
                supervisor.approval(args.proposal_id)
            elif args.command == "once":
                print(json.dumps(supervisor.cycle(), ensure_ascii=False))
            elif args.command == "run":
                supervisor.run(args.max_cycles)
            elif args.command == "export":
                cycle = next((c for c in supervisor.store.history(10_000) if c["id"] == args.cycle_id), None)
                if cycle is None or cycle["status"] != "accepted_in_snapshot":
                    raise Blocked("only_validated_cycles_exportable")
                patch = git(supervisor.repo, "diff", "--binary", cycle["base"], cycle["commit"])
                target = supervisor.root / (args.cycle_id + ".patch")
                target.write_text(patch + "\n", encoding="utf-8")
                print(str(target))
            report = render(supervisor)
            if args.command == "serve":
                # Only reports are exposed, not state, Git, source files or configuration.
                handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(report.parent))
                with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
                    print(f"Read-only reports: http://127.0.0.1:{args.port}/ ; Ctrl+C to stop")
                    server.serve_forever()
            elif args.command in {"status", "report", "init", "pause", "resume", "stop", "approve"}:
                print(json.dumps({"control": supervisor.store.get("control"), "report": str(report)}, ensure_ascii=False))
        finally:
            supervisor.close()
    except (Blocked, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, Blocked) else "configuration_or_environment_error"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
