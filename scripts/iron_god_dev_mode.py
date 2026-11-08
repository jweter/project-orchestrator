"""Dev Mode: free memory for development by closing a named list of memory hogs, reversibly.

    python scripts/iron_god_dev_mode.py plan             # what WOULD close, what is protected, what is unclassified (changes nothing)
    python scripts/iron_god_dev_mode.py enter --yes      # close the list politely and record what was closed
    python scripts/iron_god_dev_mode.py restore          # relaunch what was closed
    python scripts/iron_god_dev_mode.py status

Only your explicit command enters Dev Mode; nothing in the engine does. See src/project_orchestrator/iron_god_dev_mode.py
for the rules (a NEVER list that nothing can override, an allowlist rather than a blocklist, polite closes, restore).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from project_orchestrator import iron_god_dev_mode as dev  # noqa: E402
from project_orchestrator import iron_god_local_model as lm  # noqa: E402


def default_state_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "ExponentialEngine" / "flow-state"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "enter", "restore", "status"])
    parser.add_argument("--yes", action="store_true", help="confirm that Dev Mode may close the listed programs")
    parser.add_argument("--state-root", type=Path, default=default_state_root())
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    state_path = args.state_root / dev.STATE_NAME
    config = dev.load_config(args.state_root)

    if args.command == "status":
        out = dev.status(state_path)
    elif args.command == "restore":
        out = dev.restore(state_path, config=config)
    else:
        groups = dev.plan(config=config)
        out = dev.summary(groups)
        out["free_gib_now"] = round(lm.available_gib(), 2)
        out["needed_gib_for_7b"] = lm.gate_for("qwen2.5-coder:7b", available=lambda: 0.0).required_gib
        if args.command == "enter":
            if not args.yes:
                print("Dev Mode closes programs. Run `plan` to see which, then add --yes to proceed.", file=sys.stderr)
                return 2
            out = dev.enter(groups, state_path, confirmed=True, free_gib=lm.available_gib)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    if args.command == "plan":
        print(f"Free RAM now {out['free_gib_now']} GiB; a 7B run needs {out['needed_gib_for_7b']} GiB free.")
        print(f"Dev Mode would close {len(out['close'])} program(s) and free about {out['reclaimable_mib'] / 1024:.1f} GiB:")
        for row in out["close"]:
            print(f"  CLOSE  {row['name']:<22} x{row['count']:<3} {row['mib']:>5} MiB" + ("  (polite close only; may ask to save)" if row["guarded"] else ""))
        print("Protected (never touched), largest first:")
        for row in out["keep_top"]:
            print(f"  KEEP   {row['name']:<22} {row['mib']:>5} MiB  {row['reason']}")
        if out["unclassified"]:
            print("Not on any list (left running; add to close_extra in dev-mode.json to close):")
            for row in out["unclassified"]:
                print(f"  ?      {row['name']:<22} {row['mib']:>5} MiB")
    elif args.command == "enter":
        print(f"Dev Mode is ON. Free RAM {out['free_gib_before']:.2f} -> {out['free_gib_after']:.2f} GiB.")
        for row in out["closed"]:
            print(f"  closed {row['name']} ({row['mib']} MiB)")
        for row in out["refused"]:
            print(f"  NOT closed {row['name']}: {row['why']}")
        print("Run `restore` to relaunch what was closed.")
    elif args.command == "restore":
        for row in out["restored"]:
            print(f"  relaunched {row['name']}")
        for row in out["skipped"]:
            print(f"  skipped {row['name']}: {row['why']}")
        print(out["note"])
    else:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
