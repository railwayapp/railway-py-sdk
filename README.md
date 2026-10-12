# Railway Infrastructure as Code (IaC) authoring helpers for Python.

Install: `pip install railway-sdk` (or `pip install -e .` from this repo) then
author `.railway/railway.py`. Prefer **one file per project** that owns the
whole environment. Named partials are a last resort for split repos that cannot
share a file.

```python
from railway_sdk import define_railway, github, postgres, project, service

@define_railway
def main(ctx=None):
    db = postgres("db")
    web = service(
        "web",
        source=github("org/app"),
        start="gunicorn app:app",
        env={"DATABASE_URL": db.env.DATABASE_URL},
        tracing={"enabled": True},
    )
    return project("my-app", resources=[db, web])
```

Functions are single-file services run by Railway's function image. Point
`fn` at the file with `code_file` (relative to the config file) and it reads it
when the config is evaluated, compiling it to the image plus the base64 start
command that the dashboard editor and `railway functions push` write — the same
thing you would get by hand with `source=image(...)` and
`start="./run.sh <base64>"`. Source is capped at `FUNCTION_SOURCE_MAX_BYTES`
(98295 bytes, just under 96 KB: the whole start command has to fit one argv
entry), checked at authoring time before any plan.

```python
from railway_sdk import fn

hook = fn(
    "hook",
    code_file="functions/hook.ts",
    env={"DATABASE_URL": db.env.DATABASE_URL},
    domains=["hook.example.com"],
)
cron = fn("nightly", code_file="functions/nightly.ts", deploy={"cronSchedule": "0 3 * * *"})
```

`runtime` pins the image: `fn("hook", "hook.ts", runtime="bun:1.2.16")`. The
default is the current Bun runtime (`FUNCTION_IMAGES["bun"]`), and an explicit
`source=image(...)` wins over `runtime`. The pieces are exported on their own
too: `function_image(runtime)` resolves a runtime to its image and
`function_start_command(code)` encodes source into the start command. Without
`code_file`, `fn` is a plain service node tagged `kind: function` for config
you manage elsewhere.

The CLI evaluates this file and diffs against the linked environment.
Config as Code migration lives in the CLI (`railway config migrate --lang py`).

Last resort only: set module-level `PARTIAL = "api"` (same role as
`export const partial` in TypeScript). Do not rename a partial after apply.

See https://docs.railway.com/infrastructure-as-code
