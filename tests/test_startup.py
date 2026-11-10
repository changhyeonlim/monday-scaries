"""--help and input errors must not load the big provider SDKs."""

import subprocess
import sys

CHECK = """
import sys
from monday_scaries import cli
try:
    cli.main(["--help"])
except SystemExit:
    pass
loaded = [m for m in ("anthropic", "openai") if m in sys.modules]
print(",".join(loaded) or "none")
"""


def test_help_does_not_import_provider_sdks():
    """The SDKs load only when a provider is actually built."""
    out = (
        subprocess.run(
            [sys.executable, "-c", CHECK],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    assert out == "none"
