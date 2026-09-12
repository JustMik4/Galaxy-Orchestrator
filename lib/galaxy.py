"""Canonical Galaxy Orchestrator command-line interface.

The V1 policy helpers remain available during the V2 compatibility window.
V2 project, migration, doctor, lifecycle, and vault commands are registered in
this module so both entry points execute one implementation.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys


if not __package__:
    repository_root = str(Path(__file__).resolve().parents[1])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    __package__ = "lib"


def _load_snapshots(paths: list[str], *, require_authority: bool = False):
    if require_authority and not paths:
        raise ValueError("vault sync requires an authoritative snapshot envelope")
    decoded_values = []
    for value in paths:
        decoded_values.append(json.loads(Path(value).read_text(encoding="utf-8")))
    envelopes = [
        decoded for decoded in decoded_values
        if isinstance(decoded, dict)
        and set(decoded) == {"schema_version", "tasks", "authority"}
    ]
    if envelopes:
        if len(envelopes) != len(decoded_values):
            raise ValueError("cannot mix authoritative envelopes and legacy snapshots")
        tasks = []
        authority = []
        for decoded in envelopes:
            if decoded.get("schema_version") != 1:
                raise ValueError("unsupported authoritative snapshot schema_version")
            if not isinstance(decoded.get("tasks"), list) or not isinstance(decoded.get("authority"), list):
                raise ValueError("authoritative snapshot tasks and authority must be arrays")
            tasks.extend(decoded["tasks"])
            authority.extend(decoded["authority"])
        return {"schema_version": 1, "tasks": tasks, "authority": authority}
    if require_authority:
        raise ValueError("vault sync requires a versioned authoritative snapshot envelope")
    snapshots = []
    for decoded in decoded_values:
        items = decoded if isinstance(decoded, list) else [decoded]
        if any(not isinstance(item, dict) for item in items):
            raise ValueError("snapshot file must contain an object or array of objects")
        snapshots.extend(items)
    return snapshots


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _bootstrap_check_failed(result) -> bool:
    return bool(result.create or result.update or result.removed or result.drift)

try:
    from .legacy import (
        CLASSES,
        LADDER,
        check_dag,
        decide,
        gate,
        grant,
        normalized_path,
        overlap,
        reclaim,
        reputation,
        usage,
    )
except ImportError:  # Direct execution with ``lib`` on sys.path.
    from legacy import (  # type: ignore
        CLASSES,
        LADDER,
        check_dag,
        decide,
        gate,
        grant,
        normalized_path,
        overlap,
        reclaim,
        reputation,
        usage,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the canonical CLI.

    The compatibility command surface is delegated to the proven V1 parser
    until the V2 command groups are installed later in the integration phase.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments in (["--help"], ["-h"]):
        print(
            "Galaxy Orchestrator\n\n"
            "usage: galaxy COMMAND [OPTIONS]\n\n"
            "V2 commands:\n"
            "  install PROJECT [--mode SOLO|CO-OP] [--preset balanced|critical] [--context-economy off|balanced|aggressive] [--check]\n"
            "  init PROJECT [--mode SOLO|CO-OP] [--preset balanced|critical] [--context-economy off|balanced|aggressive] [--check]\n"
            "  bootstrap PROJECT [--context-economy off|balanced|aggressive] [--check]\n"
            "  migrate PROJECT [--preview | --rollback RECEIPT]\n"
            "  validate PROJECT [--gate]\n"
            "  doctor PROJECT [--json]\n"
            "  lock sync PROJECT [--check]\n"
            "  dispatch authorize PROJECT --request FILE --capabilities FILE --quota FILE\n"
            "  dispatch verify PROJECT --dispatch-id ID --spawn-id ID --effective-model MODEL --effective-effort EFFORT\n"
            "  dispatch review-check PROJECT --fingerprint FILE\n"
            "  dispatch review-record PROJECT --fingerprint FILE --evidence FILE\n"
            "  specialists list\n"
            "  specialists sync PROJECT [--check]\n"
            "  cleanup PROJECT [--preview | --apply]\n"
            "  vault status PROJECT [--snapshot FILE]\n"
            "  vault sync PROJECT [--snapshot FILE] [--check] [--force]\n\n"
            "Legacy policy commands:\n"
            "  decide usage reputation dag gate grant reclaim\n"
        )
        return 0
    if arguments and arguments[:2] == ["dispatch", "authorize"]:
        parser = argparse.ArgumentParser(prog="galaxy dispatch authorize")
        parser.add_argument("project")
        parser.add_argument("--request", required=True)
        parser.add_argument("--capabilities", required=True)
        parser.add_argument("--quota", required=True)
        parser.add_argument("--operator-config")
        options = parser.parse_args(arguments[2:])
        try:
            from .dispatch import DispatchCoordinator
            payload = {
                "command": "dispatch authorize",
                **DispatchCoordinator(
                    options.project, operator_config=options.operator_config,
                ).authorize(
                    _load_json(options.request), _load_json(options.capabilities),
                    _load_json(options.quota),
                ),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if payload["authorized"] else 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(json.dumps({"command": "dispatch authorize", "error": str(exc)},
                             ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[:2] == ["dispatch", "verify"]:
        parser = argparse.ArgumentParser(prog="galaxy dispatch verify")
        parser.add_argument("project")
        parser.add_argument("--dispatch-id", required=True)
        parser.add_argument("--spawn-id", required=True)
        parser.add_argument("--effective-model")
        parser.add_argument("--effective-effort")
        parser.add_argument("--parent-thread")
        options = parser.parse_args(arguments[2:])
        try:
            from .dispatch import DispatchCoordinator
            payload = {"command": "dispatch verify", **DispatchCoordinator(
                options.project,
            ).verify(
                options.dispatch_id, spawn_id=options.spawn_id,
                effective_model=options.effective_model,
                effective_effort=options.effective_effort,
                parent_thread=options.parent_thread,
            )}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if payload["status"] == "VERIFIED" else 1
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "dispatch verify", "error": str(exc)},
                             ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[:2] in (
        ["dispatch", "review-check"], ["dispatch", "review-record"],
    ):
        command = arguments[1]
        parser = argparse.ArgumentParser(prog="galaxy dispatch " + command)
        parser.add_argument("project")
        parser.add_argument("--fingerprint", required=True)
        if command == "review-record":
            parser.add_argument("--evidence", required=True)
        options = parser.parse_args(arguments[2:])
        try:
            from .dispatch import DispatchCoordinator
            coordinator = DispatchCoordinator(options.project)
            if command == "review-check":
                result = coordinator.review_lookup(_load_json(options.fingerprint))
            else:
                result = coordinator.review_record(
                    _load_json(options.fingerprint), _load_json(options.evidence),
                )
            payload = {"command": "dispatch " + command, **result}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if payload["reusable"] else 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(json.dumps({"command": "dispatch " + command, "error": str(exc)},
                             ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments in (["dispatch"], ["dispatch", "--help"], ["dispatch", "-h"]):
        print(
            "usage: galaxy dispatch {authorize,verify,review-check,review-record} ..."
        )
        return 0
    if arguments and arguments[:2] == ["lock", "sync"]:
        parser = argparse.ArgumentParser(prog="galaxy lock sync")
        parser.add_argument("project")
        parser.add_argument("--check", action="store_true")
        options = parser.parse_args(arguments[2:])
        try:
            from .declaration_lock import sync
            payload = {
                "command": "lock sync",
                **sync(options.project, check=options.check),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 1 if options.check and payload["stale"] else 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps(
                {"command": "lock sync", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ), file=sys.stderr)
            return 1
    if arguments in (["lock"], ["lock", "--help"], ["lock", "-h"]):
        print("usage: galaxy lock sync PROJECT [--check]")
        return 0
    if arguments and arguments[:2] == ["specialists", "sync"]:
        parser = argparse.ArgumentParser(prog="galaxy specialists sync")
        parser.add_argument("project")
        parser.add_argument("--check", action="store_true")
        options = parser.parse_args(arguments[2:])
        try:
            from .bootstrap import bootstrap
            result = bootstrap(options.project, check=options.check)
            payload = {"command": "specialists sync", **asdict(result)}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 1 if options.check and _bootstrap_check_failed(result) else 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "specialists sync", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments == ["specialists", "list"]:
        try:
            registry = Path(__file__).resolve().parents[1] / "registry/specialists.json"
            decoded = json.loads(registry.read_text(encoding="utf-8"))
            names = decoded.get("specialists")
            if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                raise ValueError("invalid specialist registry")
            payload = {
                "command": "specialists list",
                "schema_version": decoded.get("schema_version"),
                "specialists": sorted(names),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "specialists list", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[:2] == ["vault", "sync"]:
        parser = argparse.ArgumentParser(prog="galaxy vault sync")
        parser.add_argument("project")
        parser.add_argument("--snapshot", action="append", default=[])
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--force", action="store_true")
        options = parser.parse_args(arguments[2:])
        try:
            from .project import load_project
            from .vault import sync
            project = load_project(options.project)
            snapshots = _load_snapshots(
                options.snapshot,
                require_authority=project.config.vault.get("enabled", False),
            )
            payload = {
                "command": "vault sync",
                **sync(project.root, project.config.vault, snapshots,
                       check=options.check, force=options.force),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 1 if options.check and payload["drift"] else 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "vault sync", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[:2] == ["vault", "status"]:
        parser = argparse.ArgumentParser(prog="galaxy vault status")
        parser.add_argument("project")
        parser.add_argument("--snapshot", action="append", default=[])
        options = parser.parse_args(arguments[2:])
        try:
            from .project import load_project
            from .vault import status
            project = load_project(options.project)
            payload = {"command": "vault status", **status(
                project.root, project.config.vault, _load_snapshots(options.snapshot)
            )}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "vault status", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[0] == "cleanup":
        parser = argparse.ArgumentParser(prog="galaxy cleanup")
        parser.add_argument("project")
        operation = parser.add_mutually_exclusive_group()
        operation.add_argument("--preview", action="store_true")
        operation.add_argument("--apply", action="store_true")
        parser.add_argument("--retention-days", type=float, default=3.0)
        options = parser.parse_args(arguments[1:])
        try:
            from .lifecycle import apply_lifecycle, plan_lifecycle
            from .lifecycle.validation import validate_retention_days
            retention_days = validate_retention_days(options.retention_days)
            plan = plan_lifecycle(options.project, retention_days=retention_days)
            if options.apply:
                payload = apply_lifecycle(plan, apply=True)
                payload["targets"] = {key: list(value) for key, value in plan.targets.items()}
            else:
                payload = {"applied": False, **plan.to_dict()}
            payload = {"command": "cleanup", **payload}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            print(json.dumps({"command": "cleanup", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[0] == "migrate":
        parser = argparse.ArgumentParser(prog="galaxy migrate")
        parser.add_argument("project")
        operation = parser.add_mutually_exclusive_group()
        operation.add_argument("--preview", action="store_true")
        operation.add_argument("--rollback", metavar="RECEIPT")
        options = parser.parse_args(arguments[1:])
        master = Path(__file__).resolve().parents[1]
        try:
            from .migrations import apply, plan, rollback
            if options.preview:
                payload = plan(master, options.project).to_dict()
            elif options.rollback:
                payload = rollback(master, options.project, options.rollback)
            else:
                payload = apply(master, options.project)
            payload = {"command": "migrate", "preview": bool(options.preview), **payload}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "migrate", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[0] == "doctor":
        parser = argparse.ArgumentParser(prog="galaxy doctor")
        parser.add_argument("project")
        parser.add_argument("--json", action="store_true", dest="json_output")
        options = parser.parse_args(arguments[1:])
        try:
            from .doctor import run_doctor
            report = run_doctor(options.project)
            if options.json_output:
                payload = {"command": "doctor", **report.to_dict()}
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(report.render(), end="")
            return report.exit_code
        except (OSError, ValueError, KeyError) as exc:
            if options.json_output:
                print(json.dumps({"command": "doctor", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            else:
                print("Galaxy Doctor failed: " + str(exc), file=sys.stderr)
            return 1
    if arguments and arguments[0] == "validate":
        parser = argparse.ArgumentParser(prog="galaxy validate")
        parser.add_argument("project")
        parser.add_argument("--gate", action="store_true")
        options = parser.parse_args(arguments[1:])
        try:
            from .bootstrap import bootstrap
            from .project import load_project
            project = load_project(options.project)
            generated = bootstrap(project.root, check=True)
            unsettled = sorted(set(generated.create + generated.update + generated.removed + generated.drift))
            if unsettled:
                raise ValueError("bootstrap validation failed: " + ", ".join(unsettled))
            product_checks = "not_run"
            if options.gate:
                if not project.checks.commands:
                    raise ValueError("product gate blocked: configure real product commands")
                for command in project.checks.commands:
                    try:
                        completed = subprocess.run(
                            list(command), cwd=project.root, stdin=subprocess.DEVNULL,
                            check=False, timeout=600,
                        )
                    except subprocess.TimeoutExpired as exc:
                        raise ValueError("product check timed out") from exc
                    if completed.returncode:
                        raise ValueError("product check failed with exit " + str(completed.returncode))
                product_checks = "passed"
            payload = {
                "command": "validate",
                "configuration": "valid",
                "galaxy_version": project.lock.galaxy_version,
                "mode": project.team.mode,
                "product_checks": product_checks,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "validate", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[0] == "bootstrap":
        parser = argparse.ArgumentParser(prog="galaxy bootstrap")
        parser.add_argument("project")
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--context-economy", choices=("off", "balanced", "aggressive"))
        options = parser.parse_args(arguments[1:])
        try:
            from .bootstrap import bootstrap
            if options.context_economy is not None:
                from .project import load_project
                configured = load_project(options.project).config.context_economy.mode.value
                if configured != options.context_economy:
                    raise ValueError(
                        "--context-economy does not match authenticated project.yml "
                        f"({configured}); edit the declaration and run `galaxy lock sync PROJECT`"
                    )
            result = bootstrap(options.project, check=options.check)
            payload = {"command": "bootstrap", **asdict(result)}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 1 if options.check and _bootstrap_check_failed(result) else 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": "bootstrap", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if arguments and arguments[0] in ("install", "init"):
        command = arguments[0]
        parser = argparse.ArgumentParser(prog=f"galaxy {command}")
        parser.add_argument("project")
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--mode", choices=("SOLO", "CO-OP"), default="SOLO")
        parser.add_argument("--preset", choices=("balanced", "critical"), default="balanced")
        parser.add_argument("--context-economy", choices=("off", "balanced", "aggressive"), default="off")
        options = parser.parse_args(arguments[1:])
        try:
            from .installer import install_v2
            result = install_v2(
                Path(__file__).resolve().parents[1], options.project,
                check=options.check, mode=options.mode, preset=options.preset,
                context_economy=options.context_economy,
            )
            result["command"] = command
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"command": command, "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
    if argv is not None:
        previous = sys.argv
        try:
            sys.argv = [previous[0], *arguments]
            return _legacy_main()
        finally:
            sys.argv = previous
    return _legacy_main()


try:
    from .legacy import main as _legacy_main
except ImportError:
    from legacy import main as _legacy_main  # type: ignore


__all__ = [
    "CLASSES",
    "LADDER",
    "check_dag",
    "decide",
    "gate",
    "grant",
    "main",
    "normalized_path",
    "overlap",
    "reclaim",
    "reputation",
    "usage",
]


if __name__ == "__main__":
    raise SystemExit(main())
