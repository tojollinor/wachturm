"""Built-in roles without implicit user assignment or Home Assistant elevation."""
from copy import deepcopy

ADMIN_ROLE = "system_admin"


def with_admin_role(raw: dict) -> dict:
    from .security import ALLOWED_OPERATIONS
    result = deepcopy(raw)
    roles = result.setdefault("roles", [])
    if not isinstance(roles, list):
        raise ValueError("roles must be a list")
    matches = [r for r in roles if isinstance(r, dict) and r.get("builtin") == "admin"]
    if len(matches) > 1:
        raise ValueError("Duplicate admin role")
    key = matches[0]["key"] if matches else ADMIN_ROLE
    existing = {r.get("key") for r in roles if isinstance(r, dict)}
    serial = 2
    while not matches and key in existing:
        key = f"{ADMIN_ROLE}_{serial}"
        serial += 1
    admin = {"key": key, "name": "Admin", "builtin": "admin", "grants": [
        {"area": area["key"], "operations": sorted(ALLOWED_OPERATIONS), "modes": []}
        for area in result.get("areas", [])
    ]}
    result["roles"] = [admin] + [r for r in roles if not isinstance(r, dict) or r.get("builtin") != "admin"]
    return result
