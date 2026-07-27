from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.assignment_generation_preproduction import (
    LEGACY_PROJECT,
    build_config,
    validate_command,
    validate_config,
)


def test_stage6_names_ports_volumes_bucket_and_account_are_isolated() -> None:
    config = build_config("assignment-generation-v3-20260726-120000", 18443)
    assert validate_config(config, old_ports={443, 8443}) == []
    assert config.project_name != LEGACY_PROJECT
    assert "20260726120000" in config.project_name
    assert config.account_marker.endswith(".synthetic.invalid")


def test_landing_names_are_unique_and_keep_synthetic_identity() -> None:
    config = build_config("assignment-generation-landing-v1-20260726-220000", 21443)
    assert validate_config(config, old_ports={19443, 20443}) == []
    assert config.project_name == "ahamarkassignmentlandingv120260726220000"
    assert config.account_marker == (
        "assignment-landing-20260726220000@evaluation.synthetic.invalid"
    )


def test_stage4_project_old_port_bucket_and_account_are_rejected() -> None:
    config = build_config("assignment-generation-v3-20260726-120001", 18444)
    invalid = replace(
        config,
        project_name=LEGACY_PROJECT,
        postgres_volume="stage4_postgres",
        bucket="stage4-bucket",
        account_marker="hr0196@ahamark.local",
    )
    failures = validate_config(invalid, old_ports={18444})
    assert len(failures) >= 5


@pytest.mark.parametrize(
    "command", ["docker compose down -v", "docker volume rm x", "docker system prune"]
)
def test_destructive_cleanup_commands_are_forbidden(command: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        validate_command(command)
