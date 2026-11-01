"""test/unit/test_sync_env_to_deployments.py

Unit test suite for scripts/sync_env_to_deployments.py.
"""

from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

from scripts.sync_env_to_deployments import (
    check_drift,
    main,
    parse_env_file,
    sync_to_deployments,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> dict[str, Any]:
    """Fixture providing a mock repo directory structure."""
    env_file = tmp_path / ".env.prod"
    env_file.write_text(
        "ENVIRONMENT=production\n"
        "# Comment line\n"
        "\n"
        "PORT=8080\n"
        'TEMPORAL_HOST="temporal.default.svc.cluster.local:7233"\n'
        "OCR_CONFIDENCE_THRESHOLD='0.80'\n"
        "DATABASE_PASSWORD=SuperSecretPass\n"
        "HF_TOKEN=hf_token_secret\n"
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n",
        encoding="utf-8",
    )

    helm_dir = tmp_path / "deployments" / "helm"
    helm_dir.mkdir(parents=True)
    helm_values = helm_dir / "values.yaml"
    initial_values = {
        "replicaCount": 1,
        "image": {"repository": "rag-document-ai-worker", "tag": "0.1.0"},
        "env": {
            "ENVIRONMENT": "production",
            "PORT": "8080",
            "TEMPORAL_HOST": "temporal.default.svc.cluster.local:7233",
            "OCR_CONFIDENCE_THRESHOLD": "0.80",
            "secretName": "custom-secret",
        },
    }
    helm_values.write_text(yaml.dump(initial_values), encoding="utf-8")

    return {
        "root": tmp_path,
        "env_file": env_file,
        "helm_values": helm_values,
    }


def test_parse_env_file_basic(temp_repo: dict[str, Any]) -> None:
    """Verify parse_env_file parses key-value pairs and strips quotes."""
    parsed = parse_env_file(temp_repo["env_file"])

    assert parsed["ENVIRONMENT"] == "production"
    assert parsed["PORT"] == "8080"
    assert parsed["TEMPORAL_HOST"] == "temporal.default.svc.cluster.local:7233"
    assert parsed["OCR_CONFIDENCE_THRESHOLD"] == "0.80"


def test_parse_env_file_filters_secrets(temp_repo: dict[str, Any]) -> None:
    """Verify secrets containing PASSWORD, TOKEN, KEY, SECRET are excluded."""
    parsed = parse_env_file(temp_repo["env_file"])

    assert "DATABASE_PASSWORD" not in parsed
    assert "HF_TOKEN" not in parsed
    assert "AWS_ACCESS_KEY_ID" not in parsed
    assert "AWS_SECRET_ACCESS_KEY" not in parsed


def test_parse_env_file_nonexistent(tmp_path: Path) -> None:
    """Verify empty dict is returned for non-existent file."""
    assert parse_env_file(tmp_path / "does_not_exist.env") == {}


def test_check_drift_no_drift() -> None:
    """Verify check_drift returns empty list when values are in sync."""
    env_vars = {"PORT": "8080", "ENVIRONMENT": "production"}
    helm_env = {"PORT": "8080", "ENVIRONMENT": "production", "secretName": "sec"}
    assert check_drift(env_vars, helm_env) == []


def test_check_drift_missing_keys() -> None:
    """Verify check_drift detects missing keys in Helm environment."""
    env_vars = {"PORT": "8080", "NEW_VAR": "val"}
    helm_env = {"PORT": "8080"}

    drift = check_drift(env_vars, helm_env)
    assert len(drift) == 1
    assert "Missing key in values.yaml: 'NEW_VAR'" in drift[0]


def test_check_drift_mismatched_values() -> None:
    """Verify check_drift detects value differences."""
    env_vars = {"PORT": "8080", "ENVIRONMENT": "staging"}
    helm_env = {"PORT": "8080", "ENVIRONMENT": "production"}

    drift = check_drift(env_vars, helm_env)
    assert len(drift) == 1
    assert "Mismatch for 'ENVIRONMENT'" in drift[0]


def test_check_drift_extra_keys() -> None:
    """Verify check_drift detects extra keys in Helm environment."""
    env_vars = {"PORT": "8080"}
    helm_env = {"PORT": "8080", "OBSOLETE_VAR": "old", "secretName": "sec"}

    drift = check_drift(env_vars, helm_env)
    assert len(drift) == 1
    assert "Extra key in values.yaml not in env_file: 'OBSOLETE_VAR'" in drift[0]


def test_sync_to_deployments(temp_repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify sync_to_deployments updates values.yaml and preserves secretName."""
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.HELM_VALUES_FILE",
        temp_repo["helm_values"],
    )

    new_env = {
        "ENVIRONMENT": "production",
        "PORT": "9090",
        "OCR_CONFIDENCE_THRESHOLD": "0.85",
    }
    sync_to_deployments(new_env)

    with open(temp_repo["helm_values"], encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Check that new env values were written
    assert data["env"]["ENVIRONMENT"] == "production"
    assert data["env"]["PORT"] == "9090"
    assert data["env"]["OCR_CONFIDENCE_THRESHOLD"] == "0.85"
    # Check that secretName was preserved
    assert data["env"]["secretName"] == "custom-secret"
    # Check that non-env blocks remain intact
    assert data["replicaCount"] == 1
    assert data["image"]["repository"] == "rag-document-ai-worker"


def test_cli_check_only_success(temp_repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI --check-only exits 0 when in sync."""
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.DEFAULT_ENV_FILE",
        temp_repo["env_file"],
    )
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.HELM_VALUES_FILE",
        temp_repo["helm_values"],
    )

    with mock.patch("sys.argv", ["sync_env", "--check-only"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


def test_cli_check_only_drift_failure(
    temp_repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CLI --check-only exits 1 when drift is detected."""
    # Append a new variable to .env.prod that values.yaml does not have
    with open(temp_repo["env_file"], "a", encoding="utf-8") as f:
        f.write("UNSYNCED_VAR=true\n")

    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.DEFAULT_ENV_FILE",
        temp_repo["env_file"],
    )
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.HELM_VALUES_FILE",
        temp_repo["helm_values"],
    )

    with mock.patch("sys.argv", ["sync_env", "--check-only"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_cli_sync_success(temp_repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI --sync updates deployments."""
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.DEFAULT_ENV_FILE",
        temp_repo["env_file"],
    )
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.HELM_VALUES_FILE",
        temp_repo["helm_values"],
    )

    with mock.patch("sys.argv", ["sync_env", "--sync"]):
        main()

    with open(temp_repo["helm_values"], encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["env"]["ENVIRONMENT"] == "production"
    assert data["env"]["secretName"] == "custom-secret"


def test_cli_check_only_missing_file_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CLI --check-only exits 1 when env file does not exist."""
    missing_file = tmp_path / ".env.nonexistent"
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.DEFAULT_ENV_FILE",
        missing_file,
    )

    with mock.patch("sys.argv", ["sync_env", "--check-only"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_cli_check_only_empty_file_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI --check-only exits 1 when env file contains no variables."""
    empty_file = tmp_path / ".env.empty"
    empty_file.write_text("# Only comments\n\n", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.DEFAULT_ENV_FILE",
        empty_file,
    )

    with mock.patch("sys.argv", ["sync_env", "--check-only"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_cli_default_mode_missing_file_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CLI without --check-only exits 0 with skip warning when file is missing."""
    missing_file = tmp_path / ".env.nonexistent"
    monkeypatch.setattr(
        "scripts.sync_env_to_deployments.DEFAULT_ENV_FILE",
        missing_file,
    )

    with mock.patch("sys.argv", ["sync_env"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
