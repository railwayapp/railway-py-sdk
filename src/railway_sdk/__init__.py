"""Thin Railway Infrastructure as Code authoring helpers for Python.

Mirrors `railway/iac` (TypeScript). Compiles to the same RailwayGraph shape;
plan/apply stay in the CLI. No Config as Code knowledge here.

Prefer one file that owns the whole environment. Set module-level
``PARTIAL = "api"`` only when split repos cannot share a file.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "FUNCTION_IMAGES",
    "FUNCTION_SOURCE_MAX_BYTES",
    "Project",
    "Service",
    "bucket",
    "createRailwayContext",
    "create_railway_context",
    "database",
    "defineRailway",
    "define_railway",
    "empty",
    "fn",
    "function_image",
    "function_start_command",
    "github",
    "group",
    "image",
    "mongo",
    "mysql",
    "postgres",
    "preserve",
    "project",
    "redis",
    "ref",
    "service",
    "template",
    "volume",
]


class _EnvRefs:
    def __init__(self, address: str) -> None:
        self._address = address

    def __getattr__(self, name: str) -> dict[str, str]:
        if name.startswith("_"):
            raise AttributeError(name)
        return {"type": "reference", "resource": self._address, "output": name}

    def __getitem__(self, name: str) -> dict[str, str]:
        return {"type": "reference", "resource": self._address, "output": name}


class Service:
    """A service or database node with ``env`` variable refs."""

    def __init__(self, node: Mapping[str, Any]) -> None:
        self._node = dict(node)
        self.name = self._node["name"]
        self.address = self._node["address"]
        self.type = self._node.get("type", "service")
        self.env = _EnvRefs(self.address)

    def to_graph(self) -> dict[str, Any]:
        return dict(self._node)

    def with_fields(self, **fields: Any) -> Service:
        return Service({**self._node, **fields})


class Project:
    def __init__(self, name: str, resources: Sequence[Any], extra: Mapping[str, Any] | None = None) -> None:
        self.name = name
        self.resources = list(resources)
        self._extra = dict(extra or {})

    def to_graph(self) -> dict[str, Any]:
        return {
            "name": self.name,
            **self._extra,
            "resources": [_to_graph(item) for item in _flatten(self.resources)],
        }


def define_railway(program: Callable[..., Any]) -> Callable[..., Any]:
    return program


defineRailway = define_railway


class _SharedRefs:
    def __getattr__(self, name: str) -> dict[str, str]:
        if name.startswith("_"):
            raise AttributeError(name)
        return {"type": "sharedReference", "name": name}

    def __getitem__(self, name: str) -> dict[str, str]:
        return {"type": "sharedReference", "name": name}


class RailwayContext(dict):
    def __init__(self, payload: Mapping[str, Any]) -> None:
        super().__init__(payload)
        self.environment = payload.get("environment") or payload.get("environmentName")
        self.shared = _SharedRefs()

    def random_string(self, label: str = "random", bytes: int = 12) -> str:
        seed = f"railway-iac:{self.environment or 'default'}:{label}"
        return hashlib.sha256(seed.encode()).hexdigest()[: bytes * 2]

    def is_environment(self, name: str) -> bool:
        return self.environment == name


def create_railway_context(input: Mapping[str, Any] | None = None, **kwargs: Any) -> RailwayContext:
    payload = {**(input or {}), **kwargs}
    environment = payload.get("environment") or payload.get("environmentName")
    if environment:
        payload["environment"] = environment
        payload["environmentName"] = environment
    return RailwayContext(payload)


createRailwayContext = create_railway_context


def project(name: str, definition: Mapping[str, Any] | None = None, **kwargs: Any) -> Project:
    payload = {**(definition or {}), **kwargs}
    resources = payload.pop("resources", None)
    services = payload.pop("services", None)
    return Project(name, _flatten(resources if resources is not None else services or []), payload)


def github(repo: str, **options: Any) -> dict[str, Any]:
    if options.get("autoUpdates") is not None:
        raise ValueError("Image auto updates are only supported for Docker image sources.")
    return _prune({"type": "github", "repo": repo, "branch": options.pop("branch", "main"), **options})


def image(image_name: str, **options: Any) -> dict[str, Any]:
    if options.get("autoUpdates") is not None and not _supports_image_auto_updates(image_name):
        raise ValueError("Image auto updates are only supported for Docker Hub and GHCR images.")
    return _prune({"type": "image", "image": image_name, **options})


def template(template_name: str, **options: Any) -> dict[str, Any]:
    return _prune({"type": "template", "template": template_name, **options})


def empty(**options: Any) -> dict[str, Any]:
    return _prune({"type": "empty", **options})


def service(name: str, **config: Any) -> Service:
    return Service(_service_node(name, config))


FUNCTION_IMAGE_PREFIX = "ghcr.io/railwayapp/function-"

# Runtime name -> pinned image. Mirrors `FUNCTION_IMAGES` in `@railway/models`.
FUNCTION_IMAGES: Mapping[str, str] = {"bun": f"{FUNCTION_IMAGE_PREFIX}bun:1.4.0"}

# The source rides base64-encoded inside a single argv entry of the start
# command, and the kernel caps one argument at 128 KiB, so the decoded source
# tops out at 96 KB. The dashboard, the CLI and the API enforce the same limit.
FUNCTION_SOURCE_MAX_BYTES = 96 * 1024


def function_image(runtime: str | None = None) -> str:
    """Resolve a function runtime to its image.

    ``runtime`` is a runtime name (``"bun"``), a runtime with a version
    (``"bun:1.2.16"``) or a full ``ghcr.io/railwayapp/function-*`` image.
    ``None`` picks the default Bun runtime.
    """
    if runtime is None:
        return FUNCTION_IMAGES["bun"]
    if not isinstance(runtime, str) or not runtime.strip():
        raise ValueError("runtime must be a non-empty string.")
    runtime = runtime.strip()
    if runtime.lower().startswith(FUNCTION_IMAGE_PREFIX):
        return runtime
    if runtime in FUNCTION_IMAGES:
        return FUNCTION_IMAGES[runtime]
    if runtime.split(":", 1)[0] in FUNCTION_IMAGES:
        return f"{FUNCTION_IMAGE_PREFIX}{runtime}"
    known = ", ".join(sorted(FUNCTION_IMAGES))
    raise ValueError(f"Unknown function runtime {runtime!r}. Known runtimes: {known}.")


def function_start_command(code: str | bytes) -> str:
    """The ``./run.sh <base64>`` start command carrying a function's source.

    The function image's ``run.sh`` decodes the argument back into a single
    file and runs it. Raises ``ValueError`` when the source is over
    ``FUNCTION_SOURCE_MAX_BYTES``.
    """
    if isinstance(code, str):
        payload = code.encode("utf-8")
    elif isinstance(code, (bytes, bytearray)):
        payload = bytes(code)
    else:
        raise ValueError(f"code must be a string, got {type(code).__name__}.")
    if len(payload) > FUNCTION_SOURCE_MAX_BYTES:
        kb = -(-len(payload) // 1024)
        raise ValueError(
            f"Function source code is {kb} KB; the limit is {FUNCTION_SOURCE_MAX_BYTES // 1024} KB."
        )
    return f"./run.sh {base64.b64encode(payload).decode('ascii')}"


def fn(name: str, code: str | bytes | None = None, runtime: str | None = None, **config: Any) -> Service:
    """A Railway Function: a single file of code run by the function image.

    ``code`` is the file's source. It compiles to the runtime's image as the
    service source plus a base64 start command, the same shape the dashboard
    editor and ``railway functions push`` write. ``runtime`` picks the image
    (see :func:`function_image`); the default is Bun.

    Without ``code`` this is a plain service node tagged ``kind: function``.
    """
    config = dict(config)
    if code is not None:
        if config.get("source") is not None:
            raise ValueError("fn() takes either `code` or `source`, not both.")
        if _has_start_command(config):
            raise ValueError("fn() derives the start command from `code`; do not pass `start` as well.")
        config["source"] = image(function_image(runtime))
        config["start"] = function_start_command(code)
    elif runtime is not None:
        if config.get("source") is not None:
            raise ValueError("fn() takes either `runtime` or `source`, not both.")
        config["source"] = image(function_image(runtime))
    return Service({**_service_node(name, config), "kind": "function"})


def _has_start_command(config: Mapping[str, Any]) -> bool:
    if config.get("start") or config.get("startCommand"):
        return True
    if (config.get("run") or {}).get("command"):
        return True
    return bool((config.get("deploy") or {}).get("startCommand"))


def postgres(name: str, **config: Any) -> Service:
    return database(
        name,
        "postgres",
        image="ghcr.io/railwayapp-templates/postgres-ssl:18",
        output="DATABASE_URL",
        defaultMountPath="/var/lib/postgresql/data",
        **config,
    )


def mysql(name: str, **config: Any) -> Service:
    return database(
        name,
        "mysql",
        image="mysql:9",
        output="MYSQL_URL",
        defaultMountPath="/var/lib/mysql",
        **config,
    )


def redis(name: str, **config: Any) -> Service:
    return database(
        name,
        "redis",
        image="railwayapp/redis:8.2",
        output="REDIS_URL",
        defaultMountPath="/bitnami",
        **config,
    )


def mongo(name: str, **config: Any) -> Service:
    return database(
        name,
        "mongo",
        image="mongo:8",
        output="MONGO_URL",
        defaultMountPath="/data/db",
        **config,
    )


def database(name: str, engine: str, **options: Any) -> Service:
    image_name = options["image"]
    output = options.get("output") or "DATABASE_URL"
    node: dict[str, Any] = {
        "address": f"database.{name}",
        "type": "database",
        "kind": "database",
        "engine": engine,
        "name": name,
        "image": image_name,
        "output": output,
        "source": image(image_name),
    }
    if options.get("defaultMountPath"):
        node["defaultMountPath"] = options["defaultMountPath"]
    if options.get("region"):
        node["deploy"] = {"multiRegionConfig": {options["region"]: {"numReplicas": 1}}}
    return Service(node)


def volume(name: str, config: Mapping[str, Any] | None = None, **extra: Any) -> Service:
    return Service(
        {
            "address": f"volume.{name}",
            "type": "volume",
            "name": name,
            "config": {**(config or {}), **extra},
        }
    )


def bucket(name: str, config: Mapping[str, Any] | None = None, **extra: Any) -> Service:
    return Service(
        {
            "address": f"bucket.{name}",
            "type": "bucket",
            "name": name,
            "config": {**(config or {}), **extra},
        }
    )


def group(name: str, resources: Sequence[Any] | Mapping[str, Any] | None = None, options: Mapping[str, Any] | None = None) -> Service | list[Any]:
    if isinstance(resources, Mapping) and options is None:
        options = resources
        resources = None
    node = Service({"address": f"group.{name}", "type": "group", "name": name, **(options or {})})
    if resources is None:
        return node
    tagged = []
    for item in _flatten(resources):
        if isinstance(item, Service):
            tagged.append(item.with_fields(groupId=name))
        elif isinstance(item, Mapping):
            tagged.append({**item, "groupId": name})
        else:
            tagged.append(item)
    return [node, *tagged]


def ref(resource: Any, output: str) -> dict[str, str]:
    address = resource.address if hasattr(resource, "address") else resource["address"]
    return {"type": "reference", "resource": address, "output": output}


def preserve() -> dict[str, str]:
    return {"type": "preserve"}


def _service_node(name: str, config: Mapping[str, Any]) -> dict[str, Any]:
    source = _normalize_source(config.get("source"), config.get("root") or config.get("rootDirectory"))
    kind = "empty"
    if source:
        kind = {
            "github": "github",
            "image": "docker-image",
            "template": "template",
        }.get(source.get("type"), "empty")
    node: dict[str, Any] = {
        "address": f"service.{name}",
        "type": "service",
        "kind": kind,
        "name": name,
    }
    if source:
        node["source"] = source
    build = _normalize_build(config)
    if build is not None:
        node["build"] = build
    deploy = _normalize_deploy(config)
    if deploy is not None:
        node["deploy"] = deploy
    networking = _normalize_networking(config)
    if networking is not None:
        node["networking"] = networking
    tracing = _normalize_tracing(config.get("tracing"))
    if tracing is not None:
        node["tracing"] = tracing
    variables = config.get("env") or config.get("variables")
    if variables:
        merged = {**(config.get("variables") or {}), **(config.get("env") or {})}
        node["variables"] = _normalize_variables(merged)
    node.update(_normalize_volume_mounts(config.get("volumeMounts")))
    for key in ("configFile", "parentServiceId", "groupId", "clusterRole", "replicaConfig", "clusterDisplay"):
        if config.get(key) is not None:
            node[key] = config[key]
    return node


def _normalize_source(source: Any, root_directory: str | None) -> dict[str, Any] | None:
    if source is None:
        return {"type": "empty", "rootDirectory": root_directory} if root_directory else None
    if isinstance(source, Mapping) and source.get("type"):
        return _prune({**source, "rootDirectory": source.get("rootDirectory") or root_directory})
    if isinstance(source, Mapping) and source.get("repo"):
        return _prune({"type": "github", "repo": source["repo"], "branch": source.get("branch") or "main", "rootDirectory": root_directory})
    if isinstance(source, Mapping) and source.get("image"):
        return _prune({"type": "image", "image": source["image"], "rootDirectory": root_directory})
    return {"type": "empty", "rootDirectory": root_directory} if root_directory else None


def _normalize_build(config: Mapping[str, Any]) -> dict[str, Any] | None:
    build = config.get("build")
    if isinstance(build, str):
        return {"buildCommand": build}
    if isinstance(build, Mapping):
        return _prune(dict(build))
    return None


def _normalize_deploy(config: Mapping[str, Any]) -> dict[str, Any] | None:
    run = config.get("run") or {}
    deploy = dict(config.get("deploy") or {})
    pre_deploy = config.get("preDeploy", config.get("preDeployCommand", run.get("preDeploy")))
    if isinstance(pre_deploy, str):
        pre_deploy = [pre_deploy]
    replicas = _normalize_replicas(config.get("replicas"), config.get("regions"))
    payload = {
        **deploy,
        "startCommand": config.get("start")
        or config.get("startCommand")
        or run.get("command")
        or deploy.get("startCommand"),
        "preDeployCommand": pre_deploy if pre_deploy is not None else deploy.get("preDeployCommand"),
        "healthcheckPath": config.get("healthcheck")
        or config.get("healthcheckPath")
        or run.get("healthcheck")
        or deploy.get("healthcheckPath"),
        "healthcheckTimeout": config.get("healthcheckTimeout", run.get("healthcheckTimeout", deploy.get("healthcheckTimeout"))),
        **(replicas or {}),
    }
    if replicas and replicas.get("multiRegionConfig"):
        payload["multiRegionConfig"] = replicas["multiRegionConfig"]
    elif deploy.get("multiRegionConfig"):
        payload["multiRegionConfig"] = deploy["multiRegionConfig"]
    return _prune(payload)


def _normalize_replicas(replicas: Any, regions: Any) -> dict[str, Any] | None:
    if isinstance(replicas, int):
        return {"numReplicas": replicas}
    if isinstance(replicas, Mapping):
        return {"multiRegionConfig": _normalize_regions(replicas)}
    if isinstance(regions, Mapping):
        return {"multiRegionConfig": _normalize_regions(regions)}
    return None


def _normalize_regions(regions: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for region, value in regions.items():
        if isinstance(value, int):
            out[region] = {"numReplicas": value}
        else:
            out[region] = _prune(
                {
                    "numReplicas": value.get("count", value.get("replicas")),
                    "stackerAssignment": value.get("stacker"),
                }
            ) or {}
    return out


def _normalize_networking(config: Mapping[str, Any]) -> dict[str, Any] | None:
    domains = config.get("domains")
    custom_domains = None
    if domains:
        custom_domains = {}
        for domain in domains:
            if isinstance(domain, str):
                custom_domains[domain] = {"port": 8080}
            else:
                custom_domains[domain["domain"]] = {"port": domain.get("port") or 8080}
    tcp_proxies = None
    if config.get("tcp"):
        tcp_proxies = {str(port): {} for port in config["tcp"]}
    elif config.get("tcpProxies"):
        tcp_proxies = {str(port): {} for port in config["tcpProxies"]}
    return _prune({**(config.get("networking") or {}), "customDomains": custom_domains, "tcpProxies": tcp_proxies})


_TRACING_SWITCHES = ("enabled", "autoInstrumentation")


def _normalize_tracing(tracing: Any) -> dict[str, bool] | None:
    """Per-environment tracing switches, Railway's ``services[id].tracing``.

    Railway only serialises the switches that are on, so a ``None`` switch is
    dropped and an empty block is no block at all.
    """
    if tracing is None:
        return None
    if not isinstance(tracing, Mapping):
        raise ValueError("tracing must be a mapping with `enabled` and/or `autoInstrumentation` booleans.")
    unknown = sorted(str(key) for key in tracing if key not in _TRACING_SWITCHES)
    if unknown:
        raise ValueError(
            f"Unknown tracing field(s): {', '.join(unknown)}. Allowed: {', '.join(_TRACING_SWITCHES)}."
        )
    for key, value in tracing.items():
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"tracing.{key} must be a boolean, got {type(value).__name__}.")
    return _prune(dict(tracing))


def _normalize_variables(variables: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in variables.items():
        if isinstance(value, str):
            out[key] = {"type": "literal", "value": value}
        elif isinstance(value, Mapping) and "type" in value:
            out[key] = value
        else:
            out[key] = {"type": "raw", "value": value}
    return out


def _normalize_volume_mounts(volume_mounts: Mapping[str, Any] | None) -> dict[str, Any]:
    if not volume_mounts:
        return {}
    raw_mounts: dict[str, Any] = {}
    attachments: dict[str, Any] = {}
    for key, value in volume_mounts.items():
        node = value.to_graph() if hasattr(value, "to_graph") else value
        if isinstance(node, Mapping) and node.get("type") == "volume":
            attachments[node["name"]] = _prune(
                {
                    "volume": node["address"],
                    "mountPath": key,
                    "volumeConfig": node.get("config"),
                }
            )
            continue
        raw_mounts[key] = value
    return _prune({"volumeMounts": raw_mounts, "volumeAttachments": attachments}) or {}


def _supports_image_auto_updates(image_name: str) -> bool:
    normalized = image_name.strip().lower()
    if not normalized:
        return False
    if "/" not in normalized:
        return True
    registry = normalized.split("/", 1)[0]
    return (
        ("." not in registry and ":" not in registry and registry != "localhost")
        or registry in {"docker.io", "ghcr.io"}
    )


def _flatten(items: Iterable[Any] | None) -> list[Any]:
    out: list[Any] = []
    for item in items or []:
        if isinstance(item, (list, tuple)):
            out.extend(_flatten(item))
        else:
            out.append(item)
    return out


def _to_graph(item: Any) -> Any:
    if hasattr(item, "to_graph"):
        return item.to_graph()
    return item


def _prune(value: Any) -> Any:
    if value is None or not isinstance(value, Mapping):
        return value
    entries = [(key, child) for key, child in value.items() if child is not None]
    if not entries:
        return None
    return dict(entries)
