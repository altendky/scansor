"""Read the exact interpreter matrix from the repository's mise configuration."""

import argparse
import json
import re
import tomllib
from pathlib import Path

MISE_CONFIG = Path(__file__).resolve().parents[1] / "mise.toml"


def python_versions(config: Path | None = None) -> tuple[str, ...]:
    data = tomllib.loads((config or MISE_CONFIG).read_text(encoding="utf-8"))
    versions = data.get("tools", {}).get("python")
    if (
        not isinstance(versions, list)
        or not versions
        or any(
            not isinstance(version, str)
            or re.fullmatch(r"3\.\d+\.\d+", version) is None
            for version in versions
        )
        or len(set(versions)) != len(versions)
    ):
        raise ValueError(
            "mise Python pins must be a nonempty list of unique exact versions"
        )
    return tuple(versions)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--config", type=Path, default=MISE_CONFIG)
    versions = python_versions(parser.parse_args().config)
    print(f"versions={json.dumps(versions)}")
    print(f"primary={versions[0]}")


if __name__ == "__main__":
    main()
