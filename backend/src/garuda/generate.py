import argparse
import asyncio
from pathlib import Path

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.storage.seed import scenario_counts, write_all


def format_counts(counts: dict[str, int]) -> str:
    return " ".join(f"{name}={value}" for name, value in counts.items())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate and seed GARUDA scenario data")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="generate and count, write nothing")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    seed = config.seed if args.seed is None else args.seed
    scenario = build_scenario(config, seed)
    if args.dry_run:
        print(f"seed={seed} {format_counts(scenario_counts(scenario))}")
        return
    print("seeded " + format_counts(asyncio.run(write_all(scenario))))


if __name__ == "__main__":
    main()
