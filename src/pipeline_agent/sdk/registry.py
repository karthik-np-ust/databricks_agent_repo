"""Plugin discovery through Python entry points (group ``pipeline_agent.plugins``).

Installing a plugin package registers it. A ``module.path:ClassName``
reference also works, which is handy for tests and local experiments.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points

from .. import PLUGIN_API_VERSION
from .plugin import PlatformPlugin

ENTRY_POINT_GROUP = "pipeline_agent.plugins"


class PluginError(RuntimeError):
    pass


def discover_plugins() -> dict[str, type[PlatformPlugin]]:
    found: dict[str, type[PlatformPlugin]] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        found[ep.name] = ep.load()
    return found


def check_api_version(plugin_cls: type[PlatformPlugin]) -> None:
    wanted = PLUGIN_API_VERSION.split(".")[0]
    got = plugin_cls.manifest.plugin_api_version.split(".")[0]
    if wanted != got:
        raise PluginError(
            f"Plugin {plugin_cls.manifest.name} targets plugin API {plugin_cls.manifest.plugin_api_version}, "
            f"core provides {PLUGIN_API_VERSION}"
        )


def load_plugin(name: str) -> PlatformPlugin:
    if ":" in name:
        module_name, attr = name.split(":", 1)
        plugin_cls = getattr(importlib.import_module(module_name), attr)
    else:
        plugins = discover_plugins()
        if name not in plugins:
            raise PluginError(f"Plugin {name!r} is not installed. Installed: {sorted(plugins) or 'none'}")
        plugin_cls = plugins[name]
    check_api_version(plugin_cls)
    return plugin_cls()
