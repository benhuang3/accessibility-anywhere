"""Local face profile storage with optional, owner-scoped SpacetimeDB sync."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .state import atomic_write

log = logging.getLogger("ally.profile")
MAX_BYTES = 64 * 1024


class ProfileStore:
    def __init__(self, paths, cfg):
        self.paths = paths
        self.cfg = cfg.get("profile", cfg)
        self.path = paths.state_dir / "face_profile.json"
        self.identity_path = paths.state_dir / "stdb_identity.json"
        self.sync_status_path = paths.state_dir / "face_profile_sync.json"
        try:
            saved_status = json.loads(self.sync_status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved_status = {}
        self.last_error = saved_status.get("last_error")
        self.synced = bool(saved_status.get("synced", False))

    def _set_sync(self, synced, error=None):
        self.synced, self.last_error = bool(synced), error
        try:
            atomic_write(self.sync_status_path, json.dumps({"synced": self.synced, "last_error": error}))
        except OSError:
            pass

    @property
    def enabled(self):
        return self.cfg.get("sync", "off") == "spacetimedb"

    def _request(self, method, path, body=None, token=""):
        if isinstance(body, str) and path.endswith("/sql"):
            data, content_type = body.encode("utf-8"), "text/plain; charset=utf-8"
        else:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            content_type = "application/json"
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.cfg.get("host", "https://maincloud.spacetimedb.com").rstrip("/") + path,
                                     data=data, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=3) as response:
            payload = response.read(65_536)
            if len(payload) >= 65_536:
                raise ValueError("SpacetimeDB response exceeded the size limit")
            return json.loads(payload.decode("utf-8") or "{}")

    def _identity(self):
        try:
            token = json.loads(self.identity_path.read_text(encoding="utf-8"))["token"]
            if isinstance(token, str) and len(token) >= 20:
                return token
        except (OSError, ValueError, KeyError, TypeError):
            pass
        result = self._request("POST", "/v1/identity", [])
        token = result.get("token") if isinstance(result, dict) else None
        if not isinstance(token, str) or len(token) < 20:
            raise ValueError("SpacetimeDB did not issue an identity token")
        atomic_write(self.identity_path, json.dumps({"token": token}))
        try:
            self.identity_path.chmod(0o600)
        except OSError:
            pass
        return token

    def _cloud(self, endpoint, args):
        import urllib.parse
        db = urllib.parse.quote(self.cfg.get("database", "ally-call"), safe="")
        token = self._identity()
        return self._request("POST", f"/v1/database/{db}/{endpoint}", args, token)

    def _error(self, action, exc):
        # Exception text can contain URLs or response data; keep logs and doctor output secret-free.
        self._set_sync(False, type(exc).__name__)
        log.warning("face profile cloud %s failed (%s)", action, self.last_error)

    def save(self, profile) -> dict:
        raw = json.dumps(profile, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) >= MAX_BYTES:
            raise ValueError("face profile must be smaller than 64 KB")
        atomic_write(self.path, json.dumps(profile, indent=2, allow_nan=False))
        self._set_sync(False)
        if self.enabled:
            try:
                self._cloud("call/save_face_profile", [raw])
                self._set_sync(True)
            except Exception as exc:
                self._error("sync", exc)
        return {"saved": True, "synced": self.synced}

    @staticmethod
    def _profile_json(result):
        if isinstance(result, dict):
            rows = result.get("rows") or result.get("own_face_profile") or []
            if not rows and isinstance(result.get("tables"), list):
                for table_result in result["tables"]:
                    rows = table_result.get("rows", [])
                    if rows:
                        break
        elif isinstance(result, list):
            if result and isinstance(result[0], dict) and "rows" in result[0]:
                rows = result[0].get("rows") or []
                schema = result[0].get("schema") or {}
                cols = schema.get("elements") or schema.get("columns") or []
                names = [col.get("name") for col in cols if isinstance(col, dict)]
                if rows and isinstance(rows[0], (list, tuple)) and "profile_json" in names:
                    return rows[0][names.index("profile_json")]
            else:
                rows = result
        else:
            rows = []
        if rows and isinstance(rows[0], dict):
            return rows[0].get("profile_json")
        if rows and isinstance(rows[0], (list, tuple)):
            return rows[0][0] if rows[0] else None
        return None

    def load(self) -> dict | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            if len(raw.encode("utf-8")) < MAX_BYTES:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        if not self.enabled:
            return None
        try:
            # own_face_profile is a server view filtered to ctx.sender; no identity value enters SQL.
            result = self._cloud("sql", "SELECT profile_json FROM own_face_profile")
            raw = self._profile_json(result)
            if isinstance(raw, str) and len(raw.encode("utf-8")) < MAX_BYTES:
                data = json.loads(raw)
                if isinstance(data, dict):
                    atomic_write(self.path, json.dumps(data, indent=2, allow_nan=False))
                    self._set_sync(True)
                    return data
        except Exception as exc:
            self._error("load", exc)
        return None

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        for name in ("gaze_calibration.json", "senses_baseline.json", "au_baseline.json"):
            (self.paths.state_dir / name).unlink(missing_ok=True)
        self._set_sync(False)
        if self.enabled:
            try:
                self._cloud("call/delete_face_profile", [])
                self._set_sync(True)
            except Exception as exc:
                self._error("delete", exc)
        if not self.enabled or self.synced:
            self.identity_path.unlink(missing_ok=True)
            self.sync_status_path.unlink(missing_ok=True)

    def status(self) -> dict:
        try:
            updated = json.loads(self.path.read_text(encoding="utf-8")).get("updated")
        except (OSError, ValueError, AttributeError):
            updated = None
        return {"saved": updated, "synced": self.synced, "sync_enabled": self.enabled,
                "last_error": self.last_error}
