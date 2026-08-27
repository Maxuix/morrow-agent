"""Single source of truth for recognized project validation commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidatorCommandSpec:
    executable: str
    actions: tuple[tuple[tuple[str, ...], str], ...]
    allowed_flags: frozenset[str] = frozenset()
    option_prefixes: tuple[str, ...] = ()
    forwards_args_after_separator: bool = False


VALIDATOR_COMMAND_SPECS: tuple[ValidatorCommandSpec, ...] = (
    ValidatorCommandSpec(
        executable="pytest",
        actions=(((), "pytest"),),
        allowed_flags=frozenset(
            {"-q", "-v", "-x", "--quiet", "--verbose", "--lf", "--last-failed"}
        ),
        option_prefixes=("--maxfail=", "-k="),
    ),
    ValidatorCommandSpec(
        executable="ruff",
        actions=((("check",), "ruff_check"), (("format", "--check"), "ruff_format_check")),
        allowed_flags=frozenset({"--quiet", "--output-format=concise", "--output-format=full"}),
        option_prefixes=("--select=", "--ignore=", "--line-length="),
    ),
    ValidatorCommandSpec(
        executable="compileall",
        actions=(((), "compileall"),),
        allowed_flags=frozenset({"-q"}),
    ),
    ValidatorCommandSpec(
        executable="mypy",
        actions=(((), "mypy"),),
        allowed_flags=frozenset({"--strict", "--show-error-codes", "-q", "--quiet"}),
        option_prefixes=("--config-file=",),
    ),
    ValidatorCommandSpec(
        executable="pyright",
        actions=(((), "pyright"),),
        allowed_flags=frozenset({"--stats", "-q"}),
    ),
    ValidatorCommandSpec(
        executable="npm",
        actions=((("test",), "npm_test"),),
        forwards_args_after_separator=True,
    ),
    ValidatorCommandSpec(
        executable="pnpm",
        actions=((("test",), "pnpm_test"),),
        forwards_args_after_separator=True,
    ),
    ValidatorCommandSpec(
        executable="yarn",
        actions=((("test",), "yarn_test"),),
        forwards_args_after_separator=True,
    ),
    ValidatorCommandSpec(
        executable="cargo",
        actions=((("test",), "cargo_test"), (("check",), "cargo_check")),
        allowed_flags=frozenset({"--quiet", "-q"}),
    ),
    ValidatorCommandSpec(
        executable="make",
        actions=((("test",), "make_test"), (("check",), "make_check")),
    ),
)

VALIDATOR_SPECS_BY_EXECUTABLE = {item.executable: item for item in VALIDATOR_COMMAND_SPECS}
VALIDATOR_KINDS = frozenset(
    kind for item in VALIDATOR_COMMAND_SPECS for _action, kind in item.actions
)
VALIDATION_FLAGS = {
    item.executable: item.allowed_flags for item in VALIDATOR_COMMAND_SPECS if item.allowed_flags
}
VALIDATION_OPTION_PREFIXES = {
    item.executable: item.option_prefixes
    for item in VALIDATOR_COMMAND_SPECS
    if item.option_prefixes
}
VALIDATION_FORWARDED_ARG_FAMILIES = frozenset(
    item.executable for item in VALIDATOR_COMMAND_SPECS if item.forwards_args_after_separator
)


def match_validator_action(
    executable: str, tokens: tuple[str, ...] | list[str]
) -> tuple[str, list[str], str] | None:
    """Match one executable/action prefix and return kind, operands and option family."""
    spec = VALIDATOR_SPECS_BY_EXECUTABLE.get(executable)
    if spec is None:
        return None
    remaining = list(tokens)
    for action, kind in sorted(spec.actions, key=lambda item: len(item[0]), reverse=True):
        if tuple(remaining[: len(action)]) == action:
            return kind, remaining[len(action) :], spec.executable
    return None


__all__ = [
    "VALIDATION_FLAGS",
    "VALIDATION_FORWARDED_ARG_FAMILIES",
    "VALIDATION_OPTION_PREFIXES",
    "VALIDATOR_COMMAND_SPECS",
    "VALIDATOR_KINDS",
    "VALIDATOR_SPECS_BY_EXECUTABLE",
    "ValidatorCommandSpec",
    "match_validator_action",
]
