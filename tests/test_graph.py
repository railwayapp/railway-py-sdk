import pytest

from railway_sdk import (
    ScheduleWindow,
    ServiceCdn,
    bucket,
    create_railway_context,
    define_railway,
    fn,
    github,
    group,
    image,
    postgres,
    preserve,
    project,
    ref,
    service,
    volume,
)


def test_service_to_graph():
    web = service("web", build="pip install -r requirements.txt", start="gunicorn app:app")
    node = web.to_graph()
    assert node["type"] == "service"
    assert node["name"] == "web"
    assert node["address"] == "service.web"
    assert node["kind"] == "empty"
    assert node["build"] == {"buildCommand": "pip install -r requirements.txt"}
    assert node["deploy"] == {"startCommand": "gunicorn app:app"}


def test_github_source_and_env_refs():
    db = postgres("db")
    api = service(
        "api",
        source=github("org/api"),
        env={"DATABASE_URL": db.env.DATABASE_URL, "NAME": "api"},
        domains=["api.example.com"],
        replicas=2,
    )
    node = api.to_graph()
    assert node["kind"] == "github"
    assert node["source"] == {"type": "github", "repo": "org/api", "branch": "main"}
    assert node["variables"]["DATABASE_URL"] == {
        "type": "reference",
        "resource": "database.db",
        "output": "DATABASE_URL",
    }
    assert node["variables"]["NAME"] == {"type": "literal", "value": "api"}
    assert node["deploy"]["numReplicas"] == 2
    assert node["networking"]["customDomains"]["api.example.com"] == {"port": 8080}


def test_define_railway_project_graph():
    @define_railway
    def main(ctx=None):
        api = service("api", start="uvicorn app:app")
        return project("demo", resources=[api])

    graph = main().to_graph()
    assert graph["name"] == "demo"
    assert graph["resources"][0]["name"] == "api"


def test_group_flatten_and_volume_mount():
    data = volume("data")
    web = service("web", start="./app", volumeMounts={"/data": data})
    graph = project("demo", resources=group("app", [web, data])).to_graph()
    types = [item["type"] for item in graph["resources"]]
    assert types == ["group", "service", "volume"]
    assert graph["resources"][1]["groupId"] == "app"
    assert graph["resources"][1]["volumeAttachments"]["data"]["volume"] == "volume.data"


def test_context_helpers():
    ctx = create_railway_context(environment="prod")
    assert ctx.is_environment("prod")
    assert not ctx.is_environment("dev")
    assert ctx.shared.STRIPE_KEY == {"type": "sharedReference", "name": "STRIPE_KEY"}
    assert len(ctx.random_string("secret")) == 24


def test_tracing_block_passes_through():
    both = service("web", start="./app", tracing={"enabled": True, "autoInstrumentation": True})
    assert both.to_graph()["tracing"] == {"enabled": True, "autoInstrumentation": True}

    enabled_only = service("web", start="./app", tracing={"enabled": True})
    assert enabled_only.to_graph()["tracing"] == {"enabled": True}

    # A false switch is authored as-is; the CLI treats it the same as absent.
    off = service("web", start="./app", tracing={"enabled": False})
    assert off.to_graph()["tracing"] == {"enabled": False}

    worker = fn("worker", start="./worker", tracing={"enabled": True})
    assert worker.to_graph()["tracing"] == {"enabled": True}
    assert worker.to_graph()["kind"] == "function"


def test_tracing_none_switches_are_pruned():
    node = service("web", start="./app", tracing={"enabled": True, "autoInstrumentation": None}).to_graph()
    assert node["tracing"] == {"enabled": True}

    assert "tracing" not in service("web", start="./app", tracing={"enabled": None}).to_graph()
    assert "tracing" not in service("web", start="./app", tracing={}).to_graph()
    assert "tracing" not in service("web", start="./app", tracing=None).to_graph()
    assert "tracing" not in service("web", start="./app").to_graph()


def test_tracing_rejects_bad_input():
    with pytest.raises(ValueError, match="Unknown tracing field"):
        service("web", tracing={"enabled": True, "sampleRate": 0.5})
    with pytest.raises(ValueError, match="tracing.enabled must be a boolean"):
        service("web", tracing={"enabled": "yes"})
    with pytest.raises(ValueError, match="tracing must be a mapping"):
        service("web", tracing=True)


_FULL_CDN = {
    "enabled": True,
    "defaultTtlSeconds": 86400,
    "htmlCaching": "force",
    "staleWhileRevalidate": False,
    "purgeOnDeploy": "all",
}


def test_cdn_block_passes_through():
    # The CLI compiles `cdn` into `services[id].cdn` and backboard serialises
    # only the knobs that differ from the defaults, so the SDK passes the
    # block through verbatim rather than filling defaults in.
    assert service("web", start="./app", cdn=_FULL_CDN).to_graph()["cdn"] == _FULL_CDN
    assert service("web", start="./app", cdn={"enabled": True}).to_graph()["cdn"] == {"enabled": True}

    # Off is authored as-is; the CLI treats it the same as absent.
    assert service("web", start="./app", cdn={"enabled": False}).to_graph()["cdn"] == {"enabled": False}

    worker = fn("worker", start="./worker", cdn={"enabled": True}).to_graph()
    assert worker["cdn"] == {"enabled": True}
    assert worker["kind"] == "function"


def test_cdn_dataclass_matches_mapping_form():
    typed = ServiceCdn(
        enabled=True,
        default_ttl_seconds=86400,
        html_caching="force",
        stale_while_revalidate=False,
        purge_on_deploy="all",
    )
    assert typed.to_graph() == _FULL_CDN
    assert service("web", cdn=typed).to_graph()["cdn"] == _FULL_CDN
    assert service("web", cdn=ServiceCdn(enabled=True)).to_graph()["cdn"] == {"enabled": True}
    assert "cdn" not in service("web", cdn=ServiceCdn()).to_graph()


def test_cdn_none_knobs_are_pruned():
    node = service("web", start="./app", cdn={"enabled": True, "defaultTtlSeconds": None}).to_graph()
    assert node["cdn"] == {"enabled": True}

    assert "cdn" not in service("web", start="./app", cdn={"enabled": None}).to_graph()
    assert "cdn" not in service("web", start="./app", cdn={}).to_graph()
    assert "cdn" not in service("web", start="./app", cdn=None).to_graph()
    assert "cdn" not in service("web", start="./app").to_graph()


def test_cdn_rejects_bad_input():
    with pytest.raises(ValueError, match="Unknown cdn field"):
        service("web", cdn={"enabled": True, "ttl": 60})
    with pytest.raises(ValueError, match="cdn.enabled must be a boolean"):
        service("web", cdn={"enabled": "yes"})
    with pytest.raises(ValueError, match="cdn.staleWhileRevalidate must be a boolean"):
        service("web", cdn={"staleWhileRevalidate": 1})
    with pytest.raises(ValueError, match="cdn.htmlCaching must be one of auto, force, never"):
        service("web", cdn={"htmlCaching": "always"})
    with pytest.raises(ValueError, match="cdn.purgeOnDeploy must be one of off, html, all"):
        service("web", cdn={"purgeOnDeploy": "none"})
    with pytest.raises(ValueError, match="cdn.defaultTtlSeconds must be an integer"):
        service("web", cdn={"defaultTtlSeconds": "3600"})
    with pytest.raises(ValueError, match="cdn.defaultTtlSeconds must be an integer"):
        service("web", cdn={"defaultTtlSeconds": True})
    with pytest.raises(ValueError, match="cdn.defaultTtlSeconds must not be negative"):
        service("web", cdn={"defaultTtlSeconds": -1})
    with pytest.raises(ValueError, match="cdn must be a ServiceCdn or a mapping"):
        service("web", cdn=True)
    # The dataclass runs the same checks.
    with pytest.raises(ValueError, match="cdn.htmlCaching must be one of"):
        ServiceCdn(html_caching="always")
    with pytest.raises(ValueError, match="cdn.defaultTtlSeconds must not be negative"):
        ServiceCdn(default_ttl_seconds=-5)


def test_ref_and_preserve():
    db = postgres("db")
    assert ref(db, "DATABASE_URL") == db.env.DATABASE_URL
    assert preserve() == {"type": "preserve"}
    assert image("nginx:latest")["type"] == "image"
    assert bucket("assets").to_graph()["address"] == "bucket.assets"


def test_image_auto_updates_schedule_windows():
    source = image(
        "nginx:latest",
        autoUpdates={"type": "patch", "schedule": [ScheduleWindow(6, 2, 6), ScheduleWindow(day=0, start_hour=0, end_hour=24)]},
    )
    assert source == {
        "type": "image",
        "image": "nginx:latest",
        "autoUpdates": {
            "type": "patch",
            "schedule": [
                {"day": 6, "startHour": 2, "endHour": 6},
                {"day": 0, "startHour": 0, "endHour": 24},
            ],
        },
    }

    # The raw camelCase mapping form still works and is validated the same way.
    raw = image("ghcr.io/org/app:1", auto_updates={"type": "minor", "tagMode": "semver", "schedule": [{"day": 6, "startHour": 2, "endHour": 6}]})
    assert raw["autoUpdates"] == {"type": "minor", "tagMode": "semver", "schedule": [{"day": 6, "startHour": 2, "endHour": 6}]}

    # A policy without a window is fine; so is no autoUpdates at all.
    assert image("nginx:latest", autoUpdates={"type": "patch"})["autoUpdates"] == {"type": "patch"}
    assert "autoUpdates" not in image("nginx:latest")
    assert "autoUpdates" not in image("nginx:latest", autoUpdates=None)

    # The window lands on the service node as plain JSON.
    web = service("web", source=image("nginx:latest", autoUpdates={"type": "patch", "schedule": [ScheduleWindow(6, 2, 6)]}))
    node = web.to_graph()
    assert node["kind"] == "docker-image"
    assert node["source"]["autoUpdates"]["schedule"] == [{"day": 6, "startHour": 2, "endHour": 6}]


def test_schedule_window_rejects_bad_values():
    with pytest.raises(ValueError, match="day must be between 0 and 6"):
        ScheduleWindow(7, 2, 6)
    with pytest.raises(ValueError, match="start_hour must be between 0 and 23"):
        ScheduleWindow(6, 24, 24)
    with pytest.raises(ValueError, match="end_hour must be between 1 and 24"):
        ScheduleWindow(6, 0, 0)
    with pytest.raises(ValueError, match="end_hour \\(2\\) must be after start_hour \\(2\\)"):
        ScheduleWindow(6, 2, 2)
    with pytest.raises(ValueError, match="day must be an integer, got bool"):
        ScheduleWindow(True, 2, 6)
    with pytest.raises(ValueError, match="start_hour must be an integer, got float"):
        ScheduleWindow(6, 2.0, 6)


def test_image_auto_updates_rejects_bad_input():
    with pytest.raises(ValueError, match="autoUpdates must be a mapping"):
        image("nginx:latest", autoUpdates=True)
    with pytest.raises(ValueError, match="Unknown autoUpdates field"):
        image("nginx:latest", autoUpdates={"type": "patch", "snoozedUntil": "2026-01-01"})
    with pytest.raises(ValueError, match="autoUpdates.type must be one of"):
        image("nginx:latest", autoUpdates={"type": "weekly"})
    with pytest.raises(ValueError, match="autoUpdates.tagMode must be one of"):
        image("nginx:latest", autoUpdates={"type": "patch", "tagMode": "latest"})
    with pytest.raises(ValueError, match="schedule must be a list"):
        image("nginx:latest", autoUpdates={"type": "patch", "schedule": {"day": 6, "startHour": 2, "endHour": 6}})
    with pytest.raises(ValueError, match="needs at least one window"):
        image("nginx:latest", autoUpdates={"type": "patch", "schedule": []})
    with pytest.raises(ValueError, match="unknown: start_hour"):
        image("nginx:latest", autoUpdates={"type": "patch", "schedule": [{"day": 6, "start_hour": 2, "endHour": 6}]})
    with pytest.raises(ValueError, match="missing: endHour"):
        image("nginx:latest", autoUpdates={"type": "patch", "schedule": [{"day": 6, "startHour": 2}]})
    with pytest.raises(ValueError, match="end_hour \\(1\\) must be after start_hour \\(2\\)"):
        image("nginx:latest", autoUpdates={"type": "patch", "schedule": [{"day": 6, "startHour": 2, "endHour": 1}]})
    with pytest.raises(ValueError, match="entries must be ScheduleWindow"):
        image("nginx:latest", autoUpdates={"type": "patch", "schedule": [(6, 2, 6)]})


def test_auto_updates_only_for_docker_hub_and_ghcr():
    window = {"type": "patch", "schedule": [ScheduleWindow(6, 2, 6)]}
    assert image("ghcr.io/org/app:1", autoUpdates=window)["autoUpdates"]["type"] == "patch"
    assert image("docker.io/library/nginx:1", autoUpdates=window)["autoUpdates"]["type"] == "patch"
    with pytest.raises(ValueError, match="only supported for Docker Hub and GHCR"):
        image("quay.io/org/app:1", autoUpdates=window)
    with pytest.raises(ValueError, match="only supported for Docker Hub and GHCR"):
        image("registry.example.com:5000/app:1", auto_updates=window)
    with pytest.raises(ValueError, match="only supported for Docker image sources"):
        github("org/app", autoUpdates=window)
    with pytest.raises(ValueError, match="only supported for Docker image sources"):
        github("org/app", auto_updates=window)
