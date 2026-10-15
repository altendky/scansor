"""Native workspace evidence must observe the same lifecycle as the browser."""

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

DRIVER = (
    Path(__file__).resolve().parents[1]
    / "experiments/browser_static/validate-workspace.mjs"
)

WORKSPACE = """
import json
from types import SimpleNamespace
import numpy as np

class BrowserWorkspace:
    def __init__(self, root):
        local = np.zeros((1, 3), dtype='float64')
        local.flags.writeable = False
        self.workspace = SimpleNamespace(local=local)
        self.display_positions = np.zeros((1, 3), dtype='<f4')
        self.display_indices = np.zeros((1, 3), dtype='<u4')
        self.finished = False
        self.recipe = {}

    def envelope(self, value):
        return json.dumps({'ok': True, 'result': value})

    def call_json(self, operation, payload='{}'):
        if operation == 'replaceRecipe':
            self.recipe = json.loads(payload)['recipe']
        return self.envelope({
            'token': 'same-token', 'evaluation_running': False,
            'states': {'fit': 'ready'}, 'errors': {},
            'observed_after_finish': self.finished,
        })

    def prepare_evaluation_json(self, operation, payload):
        return self.envelope({'run': True})

    def finish_evaluation_json(self):
        self.finished = True
        result = {
            'required_failures': [], 'evaluation_running': False,
            'retained_diagnostic': {'trace': [1, 2, 3]},
        }
        result.update(self.recipe)
        return self.envelope(result)
"""


@pytest.mark.parametrize(
    "evaluation, succeeds",
    [
        ({}, True),
        ({"required_failures": ["fit"]}, False),
        ({"evaluation_running": True}, False),
        ({"evaluation_error": "failed despite a ready snapshot"}, False),
    ],
)
def test_native_workspace_keeps_evaluation_proof_and_observes_settled_get_graph(
    tmp_path: Path, evaluation: dict[str, object], succeeds: bool
) -> None:
    match = re.search(r"const nativeCode = `(.*?)`;", DRIVER.read_text(), re.S)
    assert match is not None
    with zipfile.ZipFile(tmp_path / "scansor.zip", "w") as archive:
        for path in (
            "experiments/__init__.py",
            "experiments/browser_runtime/__init__.py",
        ):
            archive.writestr(path, "")
        archive.writestr("experiments/browser_runtime/workspace.py", WORKSPACE)
    _ = (tmp_path / "recipes.json").write_text(json.dumps({"default": evaluation}))
    process = subprocess.run(
        [sys.executable, "-I", "-c", match[1], str(tmp_path), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (process.returncode == 0) is succeeds, process.stderr
    evidence = json.loads((tmp_path / "native-workspace.json").read_text())
    snapshot = evidence["snapshots"]["default"]
    assert snapshot["observed_after_finish"] is True
    assert "required_failures" not in snapshot
    response = evidence["evaluation_responses"]["default"]
    assert response["retained_diagnostic"] == {"trace": [1, 2, 3]}
    assert all(response[key] == value for key, value in evaluation.items())
    assert ("failure" not in evidence) is succeeds
