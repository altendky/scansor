from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import override

from cyclopts.config import Toml
from pydantic import ValidationError

from scansor.errors import ScansorError
from scansor.files import read_regular
from scansor.models import (
    LogLevel,
    ResolvedLogLevel,
    ResolvedMaxHeaderBytes,
    ResolvedMaxInputBytes,
    ResolvedMaxVertices,
    ResolvedSettings,
    Settings,
    SettingSource,
    TomlConfigValues,
)

JOB_NAMES = (
    "frame",
    "input_path",
    "output_path",
    "output_root",
    "unit",
)
PIPELINE_NAMES = (
    "activation_policy",
    "active_factor_ids",
    "callback_limit",
    "callback_trace_byte_limit",
    "canonical_unit",
    "execution_run",
    "generation_run",
    "held_out_row_indices",
    "initial_parameter_units",
    "initial_values",
    "inspection_run",
    "mapping_run",
    "max_support_distance_m",
    "minimum_geometric_clearance_m",
    "minimum_region_samples",
    "model_frame",
    "noise_sigma_m",
    "observation_frame",
    "problem",
    "rank_relative_threshold",
    "rotation_row_1",
    "rotation_row_2",
    "rotation_row_3",
    "sampling_profile",
    "seed",
    "source_unit",
    "transform_direction",
    "transform_scale",
    "transform_tolerance",
    "transition_guard_m",
    "translation_m",
    "translation_unit",
    "variant",
)
SETTING_NAMES = (
    "log_level",
    "max_header_bytes",
    "max_input_bytes",
    "max_vertices",
)
CONFIG_NAMES = JOB_NAMES + SETTING_NAMES + PIPELINE_NAMES
ENV_NAMES = {f"SCANSOR_{name.upper()}": name for name in CONFIG_NAMES}
COLLECTION_NAMES = frozenset(
    {
        "active_factor_ids",
        "held_out_row_indices",
        "initial_values",
        "rotation_row_1",
        "rotation_row_2",
        "rotation_row_3",
        "translation_m",
    }
)


@dataclass(frozen=True)
class ResolutionContext:
    cli_tokens: tuple[str, ...] = ()
    toml_keys: frozenset[str] = frozenset()


class RestrictedToml(Toml):
    """Cyclopts TOML source restricted to this fixture's settings table."""

    allowed_names: frozenset[str] = frozenset(CONFIG_NAMES)
    observed_keys: frozenset[str] = frozenset()
    reject_irrelevant: bool = False
    _validated_config: dict[str, object] | None = None

    @property
    @override
    def config(self) -> dict[str, object]:
        if self._validated_config is not None:
            return self._validated_config
        path = Path(self.path).expanduser()
        data = read_regular(path, "TOML config", 65_536)
        try:
            config = tomllib.loads(data.decode("utf-8"))
        except (RecursionError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ScansorError(f"invalid TOML config: {error}") from error
        if set(config) != {"scansor"} or not isinstance(config["scansor"], dict):
            raise ScansorError("TOML must contain exactly one [scansor] table")
        keys = set(config["scansor"])
        unknown = keys - set(CONFIG_NAMES)
        if unknown:
            raise ScansorError(f"unknown TOML setting(s): {', '.join(sorted(unknown))}")
        irrelevant = keys - self.allowed_names
        if self.reject_irrelevant and irrelevant:
            raise ScansorError(
                "TOML setting(s) not valid for this command: "
                + ", ".join(sorted(irrelevant))
            )
        selected = {
            key: value
            for key, value in config["scansor"].items()
            if key in self.allowed_names
        }
        try:
            values = TomlConfigValues.model_validate(selected)
        except ValidationError as error:
            raise ScansorError(f"invalid TOML config value(s): {error}") from error
        self.observed_keys = frozenset(keys)
        self._validated_config = {
            "scansor": values.model_dump(mode="python", exclude_none=True)
        }
        return self._validated_config


def reject_unknown_environment() -> None:
    unknown = sorted(
        name
        for name in os.environ
        if name.startswith("SCANSOR_") and name not in ENV_NAMES
    )
    if unknown:
        raise ScansorError(f"unknown SCANSOR_* variable(s): {', '.join(unknown)}")


def environment_values(
    names: frozenset[str], *, reject_irrelevant: bool = False
) -> dict[str, object]:
    irrelevant = sorted(
        environment_name
        for environment_name, name in ENV_NAMES.items()
        if environment_name in os.environ and name not in names
    )
    if reject_irrelevant and irrelevant:
        raise ScansorError(
            "SCANSOR_* variable(s) not valid for this command: " + ", ".join(irrelevant)
        )
    values: dict[str, object] = {}
    for name in names:
        environment_name = f"SCANSOR_{name.upper()}"
        if environment_name not in os.environ:
            continue
        raw = os.environ[environment_name]
        if name in COLLECTION_NAMES:
            try:
                value = json.loads(
                    raw,
                    parse_constant=lambda token: (_ for _ in ()).throw(
                        ValueError(f"nonfinite token {token}")
                    ),
                )
            except (json.JSONDecodeError, ValueError) as error:
                raise ScansorError(
                    f"invalid JSON array in {environment_name}: {error}"
                ) from error
            if not isinstance(value, list):
                raise ScansorError(f"{environment_name} must be a JSON array")
            values[name] = value
        else:
            values[name] = raw
    return values


def _was_cli_set(tokens: tuple[str, ...], name: str) -> bool:
    options = (f"--{name.replace('_', '-')}", f"--{name}")
    for token in tokens:
        if token == "--":
            return False
        if any(token == option or token.startswith(option + "=") for option in options):
            return True
    return False


def resolve_settings(
    context: ResolutionContext,
    *,
    log_level: LogLevel,
    max_header_bytes: int,
    max_input_bytes: int,
    max_vertices: int,
) -> ResolvedSettings:
    settings = Settings(
        log_level=log_level,
        max_header_bytes=max_header_bytes,
        max_input_bytes=max_input_bytes,
        max_vertices=max_vertices,
    )
    sources: dict[str, SettingSource] = {}
    for name in SETTING_NAMES:
        source: SettingSource
        if _was_cli_set(context.cli_tokens, name):
            source = "command-line"
        elif f"SCANSOR_{name.upper()}" in os.environ:
            source = "environment"
        elif name in context.toml_keys:
            source = "toml"
        else:
            source = "default"
        sources[name] = source
    return ResolvedSettings(
        log_level=ResolvedLogLevel(
            value=settings.log_level.value, source=sources["log_level"]
        ),
        max_header_bytes=ResolvedMaxHeaderBytes(
            value=settings.max_header_bytes, source=sources["max_header_bytes"]
        ),
        max_input_bytes=ResolvedMaxInputBytes(
            value=settings.max_input_bytes, source=sources["max_input_bytes"]
        ),
        max_vertices=ResolvedMaxVertices(
            value=settings.max_vertices, source=sources["max_vertices"]
        ),
    )
