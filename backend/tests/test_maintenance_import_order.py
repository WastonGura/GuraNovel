import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "statement",
    [
        "import app.agents; import app.models",
        "import app.models; import app.agents",
        (
            "from app.agents import ApplyChangeRequest; "
            "from app.workflows.project_maintenance import AffectedItemType"
        ),
    ],
)
def test_maintenance_contracts_import_in_a_fresh_process_regardless_of_order(
    statement: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", statement],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
