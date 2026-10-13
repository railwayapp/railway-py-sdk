"""Replicate the CLI's python3 -c eval wrapper against this package."""

import base64
import json
import subprocess
import sys
import textwrap
from pathlib import Path

CLI_EVAL = r"""
import importlib.util, json, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("railway_sdk_user", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
partial = getattr(mod, "PARTIAL", None) or getattr(mod, "Partial", None) or getattr(mod, "partial", None)
candidate = getattr(mod, "main", None) or getattr(mod, "Railway", None) or getattr(mod, "default", None)
project = candidate() if callable(candidate) else candidate
if hasattr(project, "to_graph"):
    project = project.to_graph()
print(json.dumps({"partial": partial, "project": project}, default=str))
"""


def test_cli_eval_wrapper_loads_sdk(tmp_path: Path):
    config_dir = tmp_path / ".railway"
    config_dir.mkdir()
    (config_dir / "hook.ts").write_text("export default { fetch: () => new Response('ok') };")
    source = config_dir / "railway.py"
    source.write_text(
        textwrap.dedent(
            """
            from railway_sdk import define_railway, fn, project, service

            PARTIAL = "api"

            @define_railway
            def main(ctx=None):
                web = service(
                    "api",
                    start="echo api",
                    tracing={"enabled": True, "autoInstrumentation": True},
                    cdn={"enabled": True, "purgeOnDeploy": "all"},
                )
                hook = fn("hook", code_file="hook.ts")
                return project("app", resources=[web, hook])
            """
        )
    )
    # Run from the project root, not the config directory: `code_file` must
    # resolve against the config file, not the working directory.
    result = subprocess.run(
        [sys.executable, "-c", CLI_EVAL, str(source)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    payload = json.loads(result.stdout)
    assert payload["partial"] == "api"
    resources = payload["project"]["resources"]
    assert resources[0]["address"] == "service.api"
    assert resources[0]["deploy"]["startCommand"] == "echo api"
    assert resources[0]["tracing"] == {"enabled": True, "autoInstrumentation": True}
    assert resources[0]["cdn"] == {"enabled": True, "purgeOnDeploy": "all"}
    assert resources[1]["kind"] == "function"
    assert resources[1]["source"]["image"].startswith("ghcr.io/railwayapp/function-bun:")
    assert resources[1]["deploy"]["startCommand"] == "./run.sh " + base64.b64encode(
        b"export default { fetch: () => new Response('ok') };"
    ).decode("ascii")
