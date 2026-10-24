"""Tool registry: the @tool decorator, JSON-schema generation, plug-in loading, safe calls.

A tool is a plain Python function. Its docstring becomes the description Claude
reads, and its type hints become the input schema:

    @tool
    def set_page_zoom(ctx, zoom: Annotated[float, "1.0 is normal, 2.0 is double"]):
        "Zoom the current web page."

Supported hints: str, int, float, bool, list[...], dict, Literal[...],
Optional[...], and Annotated[type, "description", {extra schema}].
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import inspect
import json
import logging
import re
import threading
import time
import traceback
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("ally.tools")

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
DEFAULT_TIMEOUT = 15.0


class ToolError(Exception):
    """Raise inside a tool to report a clear, user-facing problem (no traceback)."""


@dataclass
class ImageResult:
    """Return this from a tool to show Claude an image (e.g. a screenshot)."""

    data: bytes
    media_type: str = "image/png"
    text: str = ""


@dataclass
class Tool:
    name: str
    description: str
    func: typing.Callable
    properties: dict
    required: list
    ai: bool = True
    timeout: float | None = None
    takes_ctx: bool = False
    choices: dict = field(default_factory=dict)
    source: str = "builtin"

    def definition(self, cfg: dict | None = None) -> dict:
        props = json.loads(json.dumps(self.properties))
        for pname, fn in self.choices.items():
            if pname not in props:
                continue
            try:
                values = [v for v in fn(cfg or {}) if isinstance(v, (str, int, float))]
            except Exception:  # a bad choices callback must never break the agent
                values = []
            if values:
                props[pname]["enum"] = values
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object", "properties": props, "required": list(self.required)},
        }

    def info(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "params": self.properties,
            "required": self.required,
            "ai": self.ai,
            "source": self.source,
        }


@dataclass
class Outcome:
    """Result of running a tool."""

    name: str
    value: typing.Any = None
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None

    def text(self) -> str:
        """Short human-readable result (spoken / shown in the dashboard)."""
        if self.error:
            return self.error
        v = self.value
        if v is None:
            return "Done."
        if isinstance(v, ImageResult):
            return v.text or "Got the image."
        if isinstance(v, dict) and isinstance(v.get("message"), str):
            return v["message"]
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)[:2000]
        return str(v)

    def to_content(self):
        """Content for a Claude tool_result block."""
        if self.error:
            return self.error
        v = self.value
        if isinstance(v, ImageResult):
            return [
                {"type": "text", "text": v.text or "Image attached."},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": v.media_type,
                        "data": base64.b64encode(v.data).decode("ascii"),
                    },
                },
            ]
        if v is None:
            return "Done."
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)


# ---------------------------------------------------------------- decorator

def tool(fn=None, *, name: str | None = None, description: str | None = None, ai: bool = True,
         timeout: float | None = None, choices: dict | None = None):
    """Mark a function as an Ally tool.

    Usage: @tool, @tool("Description"), or @tool(name=..., ai=False, timeout=..., choices=...).
    ai=False hides the tool from Claude (hotkeys, commands and the dashboard can still use it).
    choices maps a parameter to a function cfg -> list of allowed values.
    """
    if isinstance(fn, str):
        description, fn = fn, None

    def wrap(f):
        f.__ally_tool__ = build_tool(f, name=name, description=description, ai=ai,
                                     timeout=timeout, choices=choices or {})
        return f

    return wrap(fn) if callable(fn) else wrap


def _parse_doc(doc: str) -> tuple[str, dict]:
    summary_lines, args, section, current = [], {}, None, None
    for raw in doc.splitlines():
        line = raw.strip()
        if re.fullmatch(r"(Args|Arguments|Parameters|Params)\s*:", line):
            section = "args"
            continue
        if re.fullmatch(r"(Returns|Return|Raises|Yields|Examples?|Notes?)\s*:", line):
            section = "other"
            continue
        if section is None:
            summary_lines.append(raw.rstrip())
        elif section == "args":
            m = re.match(r"^(\w+)\s*(\([^)]*\))?\s*:\s*(.*)$", line)
            if m:
                current = m.group(1)
                args[current] = m.group(3).strip()
            elif line and current:
                args[current] = (args[current] + " " + line).strip()
    summary = "\n".join(summary_lines).strip()
    return summary, args


_NONE = type(None)


def _schema_for(ann) -> tuple[dict, str]:
    """Return (json_schema, description) for a type annotation."""
    desc = ""
    origin = typing.get_origin(ann)
    if origin is typing.Annotated:
        base, *meta = typing.get_args(ann)
        schema, inner_desc = _schema_for(base)
        for m in meta:
            if isinstance(m, str) and not desc:
                desc = m
            elif isinstance(m, dict):
                schema.update(m)
        return schema, desc or inner_desc
    if origin is typing.Literal:
        values = list(typing.get_args(ann))
        first = values[0] if values else ""
        t = "boolean" if isinstance(first, bool) else "integer" if isinstance(first, int) else \
            "number" if isinstance(first, float) else "string"
        return {"type": t, "enum": values}, desc
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(ann) if a is not _NONE]
        if len(args) == 1:
            return _schema_for(args[0])
        return {}, desc
    if origin in (list, tuple, set, frozenset) or ann in (list, tuple, set):
        args = typing.get_args(ann)
        item = _schema_for(args[0])[0] if args else {"type": "string"}
        return {"type": "array", "items": item or {"type": "string"}}, desc
    if origin is dict or ann is dict:
        return {"type": "object"}, desc
    if ann is bool:
        return {"type": "boolean"}, desc
    if ann is int:
        return {"type": "integer"}, desc
    if ann is float:
        return {"type": "number"}, desc
    if ann is str or ann is inspect.Parameter.empty:
        return {"type": "string"}, desc
    return {}, desc


def _json_safe(value) -> bool:
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def build_tool(f, *, name=None, description=None, ai=True, timeout=None, choices=None,
               source="builtin") -> Tool:
    tname = name or f.__name__
    if not NAME_RE.match(tname):
        raise ValueError(f"tool name {tname!r} must be 1-64 letters, digits, _ or -")
    try:
        hints = typing.get_type_hints(f, include_extras=True)
    except Exception:
        hints = {}
    summary, arg_docs = _parse_doc(inspect.getdoc(f) or "")
    params = list(inspect.signature(f).parameters.values())
    takes_ctx = bool(params) and params[0].name == "ctx"
    if takes_ctx:
        params = params[1:]
    props, required = {}, []
    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        schema, desc = _schema_for(hints.get(p.name, p.annotation))
        desc = desc or arg_docs.get(p.name, "")
        if desc:
            schema["description"] = desc
        if p.default is inspect.Parameter.empty:
            required.append(p.name)
        elif p.default is not None and _json_safe(p.default):
            schema["default"] = p.default
        props[p.name] = schema
    text = (description or summary or f"Run {tname}.").strip()
    return Tool(name=tname, description=text, func=f, properties=props, required=required,
                ai=ai, timeout=timeout, takes_ctx=takes_ctx, choices=dict(choices or {}), source=source)


# ----------------------------------------------------------------- registry

def _coerce(value, schema: dict, pname: str):
    t = schema.get("type")
    try:
        if t == "number" and not isinstance(value, bool):
            value = float(value)
        elif t == "integer" and not isinstance(value, bool):
            if isinstance(value, str):
                value = float(value)
            if isinstance(value, float):
                value = int(round(value))
        elif t == "boolean" and isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        elif t == "array" and isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        elif t == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{pname}' should be a {t}, got {value!r}")
    enum = schema.get("enum")
    if enum and value not in enum:
        raise ToolError(f"'{pname}' must be one of: {', '.join(map(str, enum))}")
    return value


class Registry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._lock = threading.RLock()
        self.disabled: set[str] = set()
        self.default_timeout = DEFAULT_TIMEOUT

    # -- bookkeeping
    def add(self, t: Tool) -> None:
        with self._lock:
            self._tools[t.name] = t

    def replace_source(self, source: str, tools: list[Tool]) -> list[str]:
        """Swap every tool from one source (a plugin file, 'config', ...). Returns errors."""
        errors = []
        with self._lock:
            for name in [n for n, t in self._tools.items() if t.source == source]:
                del self._tools[name]
            for t in tools:
                other = self._tools.get(t.name)
                if other is not None:
                    errors.append(f"tool '{t.name}' from {source} clashes with one from {other.source}; skipped")
                    continue
                t.source = source
                self._tools[t.name] = t
        return errors

    def remove_source(self, source: str) -> None:
        self.replace_source(source, [])

    def get(self, name: str) -> Tool | None:
        with self._lock:
            return self._tools.get(name)

    def names(self) -> set[str]:
        with self._lock:
            return set(self._tools)

    def all(self) -> list[Tool]:
        with self._lock:
            return sorted(self._tools.values(), key=lambda t: (t.source != "builtin", t.name))

    def ai_definitions(self, cfg: dict | None = None) -> list[dict]:
        return [t.definition(cfg) for t in self.all() if t.ai and t.name not in self.disabled]

    def load_module(self, module_name: str) -> int:
        module = importlib.import_module(module_name)
        found = collect_tools(module)
        for t in found:
            self.add(t)
        return len(found)

    # -- calling
    def prepare_args(self, t: Tool, args: dict | None) -> dict:
        args = dict(args or {})
        clean = {}
        for pname, schema in t.properties.items():
            if pname in args:
                clean[pname] = _coerce(args.pop(pname), schema, pname)
        if args:
            log.info("tool %s: ignoring unknown args %s", t.name, sorted(args))
        missing = [p for p in t.required if p not in clean]
        if missing:
            raise ToolError(f"{t.name} needs: {', '.join(missing)}")
        return clean

    def call(self, name: str, args: dict | None = None, ctx=None, timeout: float | None = None) -> Outcome:
        start = time.monotonic()
        t = self.get(name)
        if t is None:
            return Outcome(name, error=f"There is no tool called '{name}'.")
        if name in self.disabled:
            return Outcome(name, error=f"The tool '{name}' is turned off in config.toml.")
        try:
            clean = self.prepare_args(t, args)
        except ToolError as e:
            return Outcome(name, error=str(e))

        box: dict = {}

        def run():
            try:
                box["value"] = t.func(ctx, **clean) if t.takes_ctx else t.func(**clean)
            except ToolError as e:
                box["error"] = str(e) or f"{name} failed."
            except Exception as e:  # never let a tool crash the agent
                log.error("tool %s crashed:\n%s", name, traceback.format_exc())
                box["error"] = f"{name} failed: {type(e).__name__}: {e}"

        limit = timeout or t.timeout or self.default_timeout
        th = threading.Thread(target=run, name=f"tool-{name}", daemon=True)
        th.start()
        th.join(limit)
        secs = time.monotonic() - start
        if th.is_alive():
            log.warning("tool %s timed out after %.0fs", name, limit)
            return Outcome(name, error=f"{name} took too long (over {limit:.0f} seconds).", seconds=secs)
        if "error" in box:
            return Outcome(name, error=box["error"], seconds=secs)
        return Outcome(name, value=box.get("value"), seconds=secs)


def collect_tools(module) -> list[Tool]:
    found = []
    for obj in vars(module).values():
        t = getattr(obj, "__ally_tool__", None)
        if isinstance(t, Tool) and getattr(obj, "__module__", None) == module.__name__:
            found.append(t)
    return found


# ----------------------------------------------------------------- plug-ins

class PluginLoader:
    """Loads every *.py file in the plugins folder and reloads files that change."""

    def __init__(self, registry: Registry, folder: Path):
        self.registry = registry
        self.folder = folder
        self.mtimes: dict[str, float] = {}
        self.errors: dict[str, str] = {}
        self._counter = 0

    def set_folder(self, folder: Path) -> None:
        """Switch plug-in folders: unload everything from the old one."""
        if folder == self.folder:
            return
        for src in list(self.mtimes):
            self.registry.remove_source(src)
        self.mtimes.clear()
        self.errors.clear()
        self.folder = folder

    def files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.glob("*.py") if not p.name.startswith(("_", ".")))

    def scan(self) -> list[str]:
        """Load new/changed files, drop deleted ones. Returns a list of change messages."""
        changes = []
        current = {str(p): p for p in self.files()}
        for src in list(self.mtimes):
            if src not in current:
                self.registry.remove_source(src)
                self.mtimes.pop(src, None)
                self.errors.pop(src, None)
                changes.append(f"removed plugin {Path(src).name}")
        for src, path in current.items():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self.mtimes.get(src) == mtime:
                continue
            self.mtimes[src] = mtime
            changes.append(self._load(path))
        return changes

    def _load(self, path: Path) -> str:
        src = str(path)
        self._counter += 1
        modname = f"ally_plugin_{re.sub(r'[^0-9a-zA-Z_]', '_', path.stem)}_{self._counter}"
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tools = collect_tools(module)
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            self.errors[src] = f"{type(e).__name__}: {e}"
            log.error("plugin %s failed to load (keeping the old version):\n%s", path.name, tb)
            return f"plugin {path.name} has an error: {e}"
        errs = self.registry.replace_source(src, tools)
        if errs:
            self.errors[src] = "; ".join(errs)
            for e in errs:
                log.warning(e)
        else:
            self.errors.pop(src, None)
        names = ", ".join(t.name for t in tools) or "no tools"
        log.info("loaded plugin %s: %s", path.name, names)
        return f"loaded plugin {path.name} ({names})"
