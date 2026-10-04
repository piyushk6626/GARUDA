import argparse
import asyncio
from pathlib import Path

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.storage.seed import write_all


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate and seed GARUDA scenario data")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    seed = config.seed if args.seed is None else args.seed
    scenario = build_scenario(config, seed)
    if args.dry_run:
        print(
            f"seed={scenario.seed} targets={len(scenario.targets)} "
            f"missions={len(scenario.missions)} threats={len(scenario.threats)} "
            f"tracks={len(scenario.tracks)} weapons={len(scenario.weapons)} "
            f"stocks={len(scenario.stocks)} loads={len(scenario.load_configs)} "
            f"cells={len(scenario.cells)} observations={len(scenario.observations)} "
            f"bases={len(scenario.bases)} features={len(scenario.features)} "
            f"borders={len(scenario.borders)} distances={len(scenario.distances)} "
            f"terrain={scenario.terrain.rows}x{scenario.terrain.cols}"
        )
        return

    result = asyncio.run(write_all(scenario))
    print(
        "seeded "
        f"targets={result.targets} missions={result.missions} "
        f"pending={result.pending} events={result.events} "
        f"threats={result.threats} tracks={result.tracks} "
        f"weapons={result.weapons} stocks={result.stocks} loads={result.load_configs} "
        f"cells={result.cells} observations={result.observations} "
        f"bases={result.bases} features={result.features} "
        f"borders={result.borders} distances={result.distances}"
    )


if __name__ == "__main__":
    main()
