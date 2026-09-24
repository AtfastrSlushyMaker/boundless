"""Command-line maintenance: ``python -m app.cli repair-campaign <campaign-id> [--apply] [--include ID ...]``."""

import argparse
import asyncio
import json

from app.services.repair_service import repair_campaign_cli


def main() -> None:
    parser = argparse.ArgumentParser(prog="boundless")
    commands = parser.add_subparsers(dest="command", required=True)
    repair = commands.add_parser("repair-campaign", help="Report (and optionally repair) bookkeeping damage.")
    repair.add_argument("campaign_id")
    repair.add_argument("--apply", action="store_true", help="Apply HIGH-confidence findings plus any --include ids.")
    repair.add_argument("--include", nargs="*", default=[], help="Finding ids to apply even if not HIGH confidence.")
    args = parser.parse_args()
    if args.command == "repair-campaign":
        result = asyncio.run(repair_campaign_cli(args.campaign_id, apply=args.apply, include=args.include))
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
