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
        cdn={"enabled": True},
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

## Image auto updates

Docker image services can update themselves inside weekly UTC maintenance
windows. Pass `autoUpdates` to `image()` with a policy (`"patch"`, `"minor"` or
`"disabled"`) and a `schedule` of `ScheduleWindow` entries. Each window names a
UTC weekday (`0` is Sunday through `6` is Saturday), the hour it opens
(`0`-`23`, inclusive) and the hour it closes (`1`-`24`, exclusive).

```python
from railway_sdk import ScheduleWindow, image, service

web = service(
    "web",
    source=image(
        "nginx:latest",
        autoUpdates={
            "type": "patch",
            # Saturday 02:00-06:00 UTC and all of Sunday.
            "schedule": [ScheduleWindow(6, 2, 6), ScheduleWindow(day=0, start_hour=0, end_hour=24)],
        },
    ),
)
```

The windows serialise to Railway's `source.autoUpdates.schedule` shape
(`{"day": 6, "startHour": 2, "endHour": 6}`) and are validated when the file
is evaluated, so an out-of-range day or a window that ends before it starts
fails locally rather than at apply. Updates only ever run inside a window, so
a policy with no `schedule` relies on one already set in the dashboard and
never fires otherwise. Auto updates only apply to Docker Hub and GHCR images;
`image()` raises for any other registry, and `github()` rejects the option.

## CDN

CDN caching is set per service and per environment, for the service's public
domains. The settings live on the edge and apply without a deploy. Pass `cdn`
to `service()` or `fn()` as a camelCase mapping or a `ServiceCdn`.

```python
from railway_sdk import ServiceCdn, service

web = service("web", cdn={"enabled": True})

docs = service(
    "docs",
    cdn=ServiceCdn(
        enabled=True,
        default_ttl_seconds=86400,
        html_caching="force",
        stale_while_revalidate=False,
        purge_on_deploy="all",
    ),
)
```

`enabled` caches responses from the service's public domains at Railway's
edge. It needs a public domain and cannot be combined with Railway
Authentication. The other knobs tune the cache and each has a default:

- `defaultTtlSeconds` (`default_ttl_seconds`): how long the edge keeps a
  cacheable response without `Cache-Control` headers of its own, in seconds.
  Defaults to `7200`.
- `htmlCaching` (`html_caching`): whether HTML documents are cached. `"auto"`
  follows the response headers, `"force"` caches them regardless, `"never"`
  leaves them uncached. Defaults to `"auto"`.
- `staleWhileRevalidate` (`stale_while_revalidate`): honour
  `stale-while-revalidate` `Cache-Control` directives, serving a stale copy
  while the edge refreshes it. Defaults to `True`.
- `purgeOnDeploy` (`purge_on_deploy`): what a successful deploy purges from the
  cache. `"html"` keeps immutable assets warm, `"all"` clears everything,
  `"off"` purges nothing. Defaults to `"html"`.

Railway serialises the block only while caching is on, and only the knobs that
differ from the defaults, so the SDK passes it through as written. Leaving the
block out, `{"enabled": False}` and `None` all mean the same thing: no caching,
and no drift in `railway config plan`. `{"enabled": True}` is a service caching
with the defaults, so a knob set to its default does not plan as drift either.
Unknown keys, wrong types and unknown `htmlCaching`/`purgeOnDeploy` values
fail when the file is evaluated.

Requires a Railway CLI that compiles `cdn`. Older CLIs drop the block when
compiling the config.

## Edge rules

Edge rules are set per service and per environment. The edge evaluates them
before a request reaches the service, so they apply without a deploy. Pass
`edgeRules` to `service()` or `fn()` as a list of camelCase mappings or
`EdgeRule`s.

```python
from railway_sdk import EdgeRule, EdgeRuleThen, service

web = service(
    "web",
    edgeRules=[
        {
            "description": "Keep the admin panel internal",
            "priority": 0,
            "if": {
                "and": [
                    {"attr": "http.path", "op": "matches", "value": "/admin/*"},
                    {"not": {"attr": "ipv4.src", "op": "in", "value": ["10.0.0.0/8"]}},
                ]
            },
            "then": {"action": "block", "params": {"status": 403}},
        },
        EdgeRule(
            priority=10,
            if_={"attr": "http.host", "op": "eq", "value": "old.example.com"},
            then=EdgeRuleThen(
                action="redirect",
                params={"status": 301, "host": "example.com", "preserve_path": True},
            ),
        ),
    ],
)
```

Each rule has a `priority` (evaluation order, lowest first, unique within the
service), a condition in `if` (`if_` on `EdgeRule`, since `if` is a keyword)
and an action in `then`. `description` is free text, `enabled: False` keeps a
rule without evaluating it, and `mode` is `"enforce"` (the only mode today).
Rules carry no id: the edge assigns one, and an update keeps a live rule's id
when the priority matches.

`if` is one clause or a combinator of clauses:

- A clause is `{"attr", "op", "value", "key"?}`. `attr` is `ipv4.src`,
  `http.host`, `http.path` or `http.header` (with `key` naming the header).
  `op` is `eq`, `neq`, `in`, `not_in`, `contains` or `matches` (glob), as the
  attribute allows. `value` is a string, or a list of strings for `in` and
  `not_in`.
- `{"and": [...]}`, `{"or": [...]}` and `{"not": ...}` combine them.

`then.action` is `block`, `allow`, `challenge`, `redirect` or
`cache_override`, with `params` per action: `status` and `body` for `block`;
`status` and either `location` or `host` (plus `preserve_path` and
`preserve_query`) for `redirect`; `ttl_seconds` or `bypass` for
`cache_override`. The SDK types `params` loosely; the edge validates the whole
ruleset when the change is committed and rejects an invalid one with its
diagnostics. The shape of each rule (its keys, `priority`, `if` and `then`),
the action name and duplicate priorities fail when the file is evaluated.

Railway serialises the list sorted by `priority`, without `enabled` when true
or `mode` when `enforce`, so a reordered or default-laden copy of the live
rules does not plan as drift. Leaving the key out, `[]` and `None` all mean
the same thing: no rules. The list is replaced as a whole, and rules need a
plan with an edge-rules budget and a service with a domain.

Requires a Railway CLI that compiles `edgeRules`. Older CLIs drop the list
when compiling the config.

Last resort only: set module-level `PARTIAL = "api"` (same role as
`export const partial` in TypeScript). Do not rename a partial after apply.

See https://docs.railway.com/infrastructure-as-code
