import base64

import pytest

from railway_sdk import (
    FUNCTION_IMAGES,
    FUNCTION_SOURCE_MAX_BYTES,
    fn,
    function_image,
    function_start_command,
    github,
    image,
    postgres,
)

CODE = 'export default { fetch() { return new Response("ok"); } };\n'


def _decode(start_command: str) -> bytes:
    cmd, payload = start_command.split(" ")
    assert cmd == "./run.sh"
    return base64.b64decode(payload, validate=True)


def test_fn_with_code_compiles_to_image_and_start_command():
    node = fn("hook", CODE).to_graph()
    assert node["address"] == "service.hook"
    assert node["type"] == "service"
    assert node["kind"] == "function"
    assert node["source"] == {"type": "image", "image": FUNCTION_IMAGES["bun"]}
    assert node["source"]["image"].startswith("ghcr.io/railwayapp/function-bun:")
    assert _decode(node["deploy"]["startCommand"]) == CODE.encode("utf-8")
    assert "build" not in node


def test_fn_code_keyword_and_other_service_config():
    db = postgres("db")
    node = fn(
        "hook",
        code=CODE,
        env={"DATABASE_URL": db.env.DATABASE_URL},
        domains=["hook.example.com"],
        deploy={"cronSchedule": "0 * * * *"},
        tracing={"enabled": True},
    ).to_graph()
    assert node["kind"] == "function"
    assert node["variables"]["DATABASE_URL"]["resource"] == "database.db"
    assert node["networking"]["customDomains"]["hook.example.com"] == {"port": 8080}
    assert node["deploy"]["cronSchedule"] == "0 * * * *"
    assert _decode(node["deploy"]["startCommand"]) == CODE.encode("utf-8")
    assert node["tracing"] == {"enabled": True}


def test_fn_round_trips_unicode_and_bytes():
    unicode = "export default () => new Response('héllo 🚂');\n"
    assert _decode(fn("u", unicode).to_graph()["deploy"]["startCommand"]).decode("utf-8") == unicode
    raw = b"console.log(1)\n"
    assert _decode(fn("b", raw).to_graph()["deploy"]["startCommand"]) == raw


def test_fn_runtime_selection():
    assert function_image() == FUNCTION_IMAGES["bun"]
    assert function_image("bun") == FUNCTION_IMAGES["bun"]
    assert function_image("bun:1.2.16") == "ghcr.io/railwayapp/function-bun:1.2.16"
    assert function_image("ghcr.io/railwayapp/function-bun:1.3.0") == "ghcr.io/railwayapp/function-bun:1.3.0"

    pinned = fn("hook", CODE, runtime="bun:1.2.16").to_graph()
    assert pinned["source"]["image"] == "ghcr.io/railwayapp/function-bun:1.2.16"

    # runtime alone still pins the image; the code may live elsewhere.
    no_code = fn("hook", runtime="bun", start="./run.sh abc").to_graph()
    assert no_code["source"]["image"] == FUNCTION_IMAGES["bun"]
    assert no_code["deploy"]["startCommand"] == "./run.sh abc"

    with pytest.raises(ValueError, match="Unknown function runtime 'deno'"):
        function_image("deno")
    with pytest.raises(ValueError, match="non-empty string"):
        function_image("  ")


def test_fn_enforces_96kb_source_limit():
    assert FUNCTION_SOURCE_MAX_BYTES == 96 * 1024

    at_limit = "x" * FUNCTION_SOURCE_MAX_BYTES
    assert len(_decode(fn("big", at_limit).to_graph()["deploy"]["startCommand"])) == FUNCTION_SOURCE_MAX_BYTES

    with pytest.raises(ValueError, match="Function source code is 97 KB; the limit is 96 KB"):
        fn("big", at_limit + "x")

    # The limit is on encoded UTF-8 bytes, not characters.
    multibyte = "🚂" * (FUNCTION_SOURCE_MAX_BYTES // 4 + 1)
    with pytest.raises(ValueError, match="the limit is 96 KB"):
        function_start_command(multibyte)


def test_fn_rejects_conflicting_config():
    with pytest.raises(ValueError, match="either `code` or `source`"):
        fn("hook", CODE, source=github("org/app"))
    with pytest.raises(ValueError, match="either `code` or `source`"):
        fn("hook", CODE, source=image("nginx:latest"))
    with pytest.raises(ValueError, match="do not pass `start`"):
        fn("hook", CODE, start="./other")
    with pytest.raises(ValueError, match="do not pass `start`"):
        fn("hook", CODE, deploy={"startCommand": "./other"})
    with pytest.raises(ValueError, match="either `runtime` or `source`"):
        fn("hook", runtime="bun", source=image("nginx:latest"))
    with pytest.raises(ValueError, match="code must be a string"):
        function_start_command(42)  # type: ignore[arg-type]


def test_fn_without_code_is_unchanged():
    node = fn("worker", start="./worker").to_graph()
    assert node["kind"] == "function"
    assert "source" not in node
    assert node["deploy"] == {"startCommand": "./worker"}
