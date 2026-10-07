"""Fixed local examples offered by the experimental browser toolbar."""

from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
NOZZLE = REPOSITORY / "examples/nozzle-bayonette-simplified"
REPEATED_BOSS_OUTPUT = REPOSITORY / "local-inputs/repeated-boss-selection-v2"
EXAMPLES = (
    {"id": "nozzle", "label": "Nozzle"},
    {"id": "repeated-boss", "label": "Repeated bosses"},
)
DEFAULT_RECIPES = {
    "nozzle": NOZZLE / "recipes/nozzle-selection-and-fitting-demo.json",
    "repeated-boss": REPOSITORY
    / "examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json",
}


def example_recipe_path(identifier: str) -> Path:
    """Return the checked-in saved feature definitions for a built-in example."""
    try:
        return DEFAULT_RECIPES[identifier]
    except KeyError as error:
        raise ValueError("unknown example") from error


def example_identity(path: Path | None) -> str | None:
    """Custom CLI workspaces are not labelled as one of the built-in examples."""
    if path is None:
        return None
    resolved = path.resolve()
    if resolved == NOZZLE.resolve():
        return "nozzle"
    if resolved == (REPEATED_BOSS_OUTPUT / "scan-coarse").resolve():
        return "repeated-boss"
    return None


def example_directory(identifier: str) -> Path:
    """Prepare the coarse generated fixture once, without replacing local data."""
    if identifier == "nozzle":
        return NOZZLE
    if identifier != "repeated-boss":
        raise ValueError("unknown example")
    directory = REPEATED_BOSS_OUTPUT / "scan-coarse"
    if not (directory / "manifest.json").is_file():
        if REPEATED_BOSS_OUTPUT.exists():
            raise ValueError(
                "the local repeated-boss fixture is incomplete; use a complete scan-coarse fixture"
            )
        from experiments.repeated_boss_fixture import publish_fixture

        _ = publish_fixture(
            REPEATED_BOSS_OUTPUT,
            REPOSITORY / "examples/repeated-boss-selection/fixture.json",
            realization_ids=("scan-coarse",),
        )
    return directory
