import pytest

from railway_sdk import (
    EdgeRule,
    EdgeRuleThen,
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


_EDGE_RULES = [
    {
        "description": "block the scanner",
        "priority": 10,
        "enabled": False,
        "mode": "enforce",
        "if": {
            "and": [
                {"attr": "http.path", "op": "matches", "value": "/admin/*"},
                {"not": {"attr": "ipv4.src", "op": "in", "value": ["10.0.0.0/8"]}},
            ]
        },
        "then": {"action": "block", "params": {"status": 403}},
    },
    {
        "priority": 0,
        "if": {"attr": "http.header", "key": "x-canary", "op": "eq", "value": "1"},
        "then": {"action": "cache_override", "params": {"bypass": True}},
    },
]


def test_edge_rules_pass_through():
    # The CLI compiles `edgeRules` into `services[id].edgeRules` and backboard
    # canonicalises the list (sorted by priority, defaults dropped), so the
    # SDK passes it through verbatim rather than normalising it.
    assert service("web", start="./app", edgeRules=_EDGE_RULES).to_graph()["edgeRules"] == _EDGE_RULES
    worker = fn("worker", start="./worker", edgeRules=_EDGE_RULES).to_graph()
    assert worker["edgeRules"] == _EDGE_RULES
    assert worker["kind"] == "function"

    # Absent, `[]` and `None` all mean no rules; `[]` is kept as written.
    assert service("web", start="./app", edgeRules=[]).to_graph()["edgeRules"] == []
    assert "edgeRules" not in service("web", start="./app", edgeRules=None).to_graph()
    assert "edgeRules" not in service("web", start="./app").to_graph()


def test_edge_rule_dataclass_matches_mapping_form():
    typed = [
        EdgeRule(
            description="block the scanner",
            priority=10,
            enabled=False,
            mode="enforce",
            if_=_EDGE_RULES[0]["if"],
            then=EdgeRuleThen(action="block", params={"status": 403}),
        ),
        # `then` may be a mapping on a typed rule, and vice versa.
        EdgeRule(0, _EDGE_RULES[1]["if"], {"action": "cache_override", "params": {"bypass": True}}),
    ]
    assert [rule.to_graph() for rule in typed] == _EDGE_RULES
    assert service("web", edgeRules=typed).to_graph()["edgeRules"] == _EDGE_RULES
    mixed = [typed[0], _EDGE_RULES[1]]
    assert service("web", edgeRules=mixed).to_graph()["edgeRules"] == _EDGE_RULES
    assert EdgeRuleThen("allow").to_graph() == {"action": "allow"}


def test_edge_rules_none_fields_are_pruned():
    rule = {
        "description": None,
        "priority": 5,
        "enabled": None,
        "mode": None,
        "if": {"attr": "http.host", "op": "eq", "value": "old.example.com", "key": None},
        "then": {"action": "redirect", "params": None},
    }
    assert service("web", edgeRules=[rule]).to_graph()["edgeRules"] == [
        {
            "priority": 5,
            "if": {"attr": "http.host", "op": "eq", "value": "old.example.com"},
            "then": {"action": "redirect"},
        }
    ]


def test_edge_rules_reject_bad_input():
    clause = {"attr": "http.path", "op": "eq", "value": "/"}
    ok = {"priority": 1, "if": clause, "then": {"action": "allow"}}

    with pytest.raises(ValueError, match="edgeRules must be a list"):
        service("web", edgeRules=ok)
    with pytest.raises(ValueError, match=r"edgeRules\[0\] must be an EdgeRule or a mapping"):
        service("web", edgeRules=[True])
    with pytest.raises(ValueError, match=r"Unknown edgeRules\[0\] field\(s\): id"):
        service("web", edgeRules=[{**ok, "id": "abc"}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].priority must be an integer, got missing"):
        service("web", edgeRules=[{"if": clause, "then": {"action": "allow"}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].priority must be an integer, got bool"):
        service("web", edgeRules=[{**ok, "priority": True}])
    with pytest.raises(ValueError, match=r"edgeRules\[1\].priority 1 is already used by edgeRules\[0\]"):
        service("web", edgeRules=[ok, dict(ok)])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].enabled must be a boolean"):
        service("web", edgeRules=[{**ok, "enabled": "no"}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].mode must be one of enforce"):
        service("web", edgeRules=[{**ok, "mode": "monitor"}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].description must be a string"):
        service("web", edgeRules=[{**ok, "description": 1}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if is required"):
        service("web", edgeRules=[{"priority": 1, "then": {"action": "allow"}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].then is required"):
        service("web", edgeRules=[{"priority": 1, "if": clause}])

    # Conditions: a clause or exactly one combinator.
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if must be a mapping"):
        service("web", edgeRules=[{**ok, "if": "http.path == /"}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if must be exactly one of and, or, not"):
        service("web", edgeRules=[{**ok, "if": {"and": [clause], "or": [clause]}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if must be exactly one of and, or, not"):
        service("web", edgeRules=[{**ok, "if": {"not": clause, "attr": "http.path"}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if.and must be a list"):
        service("web", edgeRules=[{**ok, "if": {"and": clause}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if.or\[1\].attr must be a non-empty string"):
        service("web", edgeRules=[{**ok, "if": {"or": [clause, {"op": "eq", "value": "/"}]}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if.not.op must be a non-empty string"):
        service("web", edgeRules=[{**ok, "if": {"not": {"attr": "http.path", "op": "", "value": "/"}}}])
    with pytest.raises(ValueError, match=r"Unknown edgeRules\[0\].if field\(s\): path"):
        service("web", edgeRules=[{**ok, "if": {"path": "/"}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if.value must be a string or a list of strings"):
        service("web", edgeRules=[{**ok, "if": {**clause, "value": 403}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if.value must be a string or a list of strings"):
        service("web", edgeRules=[{**ok, "if": {**clause, "value": ["/", 1]}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].if.key must be a string"):
        service("web", edgeRules=[{**ok, "if": {**clause, "key": 1}}])

    # Actions.
    with pytest.raises(ValueError, match=r"edgeRules\[0\].then must be an EdgeRuleThen or a mapping"):
        service("web", edgeRules=[{**ok, "then": "block"}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].then.action must be one of block, allow, challenge, redirect, cache_override"):
        service("web", edgeRules=[{**ok, "then": {"action": "drop"}}])
    with pytest.raises(ValueError, match=r"Unknown edgeRules\[0\].then field\(s\): status"):
        service("web", edgeRules=[{**ok, "then": {"action": "block", "status": 403}}])
    with pytest.raises(ValueError, match=r"edgeRules\[0\].then.params must be a mapping"):
        service("web", edgeRules=[{**ok, "then": {"action": "block", "params": 403}}])

    # The dataclasses run the same checks.
    with pytest.raises(ValueError, match="then.action must be one of"):
        EdgeRuleThen(action="drop")
    with pytest.raises(ValueError, match="priority must be an integer"):
        EdgeRule(priority="1", if_=clause, then=EdgeRuleThen("allow"))
    with pytest.raises(ValueError, match="mode must be one of enforce"):
        EdgeRule(1, clause, EdgeRuleThen("allow"), mode="monitor")


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
