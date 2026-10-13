import base64
from pathlib import Path

import pytest

from railway_sdk import (
    FUNCTION_IMAGES,
    FUNCTION_SOURCE_MAX_BYTES,
    fn,
    function_image,
    function_start_command,
    image,
    postgres,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hook.ts"
CODE = FIXTURE.read_bytes()


def _decode(start_command: str) -> bytes:
    cmd, payload = start_command.split(" ")
    assert cmd == "./run.sh"
    return base64.b64decode(payload, validate=True)


def test_fn_code_file_compiles_to_image_and_start_command(tmp_path: Path):
    node = fn("hook", str(FIXTURE)).to_graph()
    assert node["address"] == "service.hook"
    assert node["type"] == "service"
    assert node["kind"] == "function"
    assert node["source"] == {"type": "image", "image": FUNCTION_IMAGES["bun"]}
    assert node["source"]["image"].startswith("ghcr.io/railwayapp/function-bun:")
    assert _decode(node["deploy"]["startCommand"]) == CODE
    assert "build" not in node

    # PathLike works too.
    assert fn("hook", FIXTURE).to_graph() == node


def test_fn_code_file_resolves_relative_to_calling_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The working directory is somewhere else entirely; the path must resolve
    # against this test module, which is the "config file" calling fn().
    monkeypatch.chdir(tmp_path)
    node = fn("hook", "fixtures/hook.ts").to_graph()
    assert _decode(node["deploy"]["startCommand"]) == CODE

    with pytest.raises(FileNotFoundError, match="code_file 'fixtures/missing.ts' not found"):
        fn("hook", "fixtures/missing.ts")


def test_fn_code_file_keyword_and_other_service_config():
    db = postgres("db")
    node = fn(
        "hook",
        code_file=FIXTURE,
        env={"DATABASE_URL": db.env.DATABASE_URL},
        domains=["hook.example.com"],
        deploy={"cronSchedule": "0 * * * *"},
        tracing={"enabled": True},
    ).to_graph()
    assert node["kind"] == "function"
    assert node["variables"]["DATABASE_URL"]["resource"] == "database.db"
    assert node["networking"]["customDomains"]["hook.example.com"] == {"port": 8080}
    assert node["deploy"]["cronSchedule"] == "0 * * * *"
    assert _decode(node["deploy"]["startCommand"]) == CODE
    assert node["tracing"] == {"enabled": True}


def test_fn_round_trips_unicode(tmp_path: Path):
    unicode = "export default () => new Response('héllo 🚂');\n"
    path = tmp_path / "hook.ts"
    path.write_text(unicode, encoding="utf-8")
    assert _decode(fn("u", path).to_graph()["deploy"]["startCommand"]).decode("utf-8") == unicode


def test_fn_runtime_selection():
    assert function_image() == FUNCTION_IMAGES["bun"]
    assert function_image("bun") == FUNCTION_IMAGES["bun"]
    assert function_image("bun:1.2.16") == "ghcr.io/railwayapp/function-bun:1.2.16"
    assert function_image("ghcr.io/railwayapp/function-bun:1.3.0") == "ghcr.io/railwayapp/function-bun:1.3.0"

    pinned = fn("hook", FIXTURE, runtime="bun:1.2.16").to_graph()
    assert pinned["source"]["image"] == "ghcr.io/railwayapp/function-bun:1.2.16"

    # runtime alone still pins the image; the code may live elsewhere.
    no_code = fn("hook", runtime="bun", start="./run.sh abc").to_graph()
    assert no_code["source"]["image"] == FUNCTION_IMAGES["bun"]
    assert no_code["deploy"]["startCommand"] == "./run.sh abc"

    # An explicit source wins over the runtime image, as in the TypeScript SDK.
    pinned_source = fn("hook", FIXTURE, source=image("ghcr.io/railwayapp/function-bun:1.3.0")).to_graph()
    assert pinned_source["source"] == {"type": "image", "image": "ghcr.io/railwayapp/function-bun:1.3.0"}
    assert _decode(pinned_source["deploy"]["startCommand"]) == CODE

    with pytest.raises(ValueError, match="Unknown function runtime 'deno'"):
        function_image("deno")
    with pytest.raises(ValueError, match="non-empty string"):
        function_image("  ")
    with pytest.raises(ValueError, match="empty version"):
        function_image("bun:")


def test_fn_enforces_source_limit(tmp_path: Path):
    # The whole start command must fit one 128 KiB argv entry (NUL included).
    assert FUNCTION_SOURCE_MAX_BYTES == 98295
    assert FUNCTION_SOURCE_MAX_BYTES < 96 * 1024

    at_limit = tmp_path / "at_limit.ts"
    at_limit.write_bytes(b"x" * FUNCTION_SOURCE_MAX_BYTES)
    start_command = fn("big", at_limit).to_graph()["deploy"]["startCommand"]
    assert len(_decode(start_command)) == FUNCTION_SOURCE_MAX_BYTES
    assert len(start_command.encode("ascii")) + 1 <= 128 * 1024

    over = tmp_path / "over.ts"
    over.write_bytes(b"x" * (FUNCTION_SOURCE_MAX_BYTES + 1))
    with pytest.raises(ValueError, match="Function source code is 98296 bytes; the limit is 98295 bytes"):
        fn("big", over)

    # 96 KB exactly used to pass here and then fail at apply.
    ninety_six = tmp_path / "96k.ts"
    ninety_six.write_bytes(b"x" * (96 * 1024))
    with pytest.raises(ValueError, match="Function source code is 98304 bytes; the limit is 98295 bytes"):
        fn("big", ninety_six)

    # The limit is on encoded UTF-8 bytes, not characters.
    multibyte = "🚂" * (FUNCTION_SOURCE_MAX_BYTES // 4 + 1)
    with pytest.raises(ValueError, match="the limit is 98295 bytes"):
        function_start_command(multibyte)


def test_fn_rejects_conflicting_config():
    with pytest.raises(ValueError, match="either `runtime` or `source`"):
        fn("hook", FIXTURE, runtime="bun", source=image("ghcr.io/railwayapp/function-bun:1.3.0"))
    with pytest.raises(ValueError, match="do not pass `start`"):
        fn("hook", FIXTURE, start="./other")
    with pytest.raises(ValueError, match="do not pass `start`"):
        fn("hook", FIXTURE, deploy={"startCommand": "./other"})
    with pytest.raises(ValueError, match="either `runtime` or `source`"):
        fn("hook", runtime="bun", source=image("nginx:latest"))
    with pytest.raises(ValueError, match="code must be a string"):
        function_start_command(42)  # type: ignore[arg-type]


def test_fn_without_code_file_is_unchanged():
    node = fn("worker", start="./worker").to_graph()
    assert node["kind"] == "function"
    assert "source" not in node
    assert node["deploy"] == {"startCommand": "./worker"}
