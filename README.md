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

Last resort only: set module-level `PARTIAL = "api"` (same role as
`export const partial` in TypeScript). Do not rename a partial after apply.

See https://docs.railway.com/infrastructure-as-code
