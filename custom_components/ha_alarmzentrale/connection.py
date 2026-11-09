"""Passive reachability derived from cached entity availability or RSSI."""
import math


def parse_connection(sensor: dict) -> dict:
    raw = sensor.get("connection", {})
    if not isinstance(raw, dict) or set(raw) - {"method", "rssi_channel", "direction", "offline_threshold", "warning_threshold"}:
        raise ValueError("Invalid connection settings")
    result = {"method": raw.get("method", "states")}
    if result["method"] not in {"disabled", "states", "availability", "rssi"}:
        raise ValueError("Invalid reachability method")
    sources = sensor.get("diagnostic_entities", {})
    if not isinstance(sources, dict):
        raise ValueError("diagnostic_entities must be an object")
    if result["method"] == "availability" and (not sensor.get("device_id") or not sources.get("connectivity")):
        raise ValueError("Availability requires a device and its reference entity")
    if result["method"] == "rssi" or any(key in raw for key in ("rssi_channel", "direction", "offline_threshold", "warning_threshold")):
        channel = raw.get("rssi_channel")
        if channel not in {"rssi_device", "rssi_peer"} or not sources.get(channel):
            raise ValueError("Select an RSSI source")
        direction = raw.get("direction")
        if direction not in {"below", "above"}:
            raise ValueError("Invalid RSSI threshold direction")
        for key in ("offline_threshold", "warning_threshold"):
            value = raw.get(key)
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError("RSSI thresholds must be finite numbers")
        offline, warning = raw["offline_threshold"], raw["warning_threshold"]
        if (direction == "below" and warning <= offline) or (direction == "above" and warning >= offline):
            raise ValueError("RSSI warning threshold must precede the offline threshold")
        result.update(rssi_channel=channel, direction=direction,
                      offline_threshold=offline, warning_threshold=warning)
    return result


def signal_quality(value, rule: dict) -> str:
    try:
        value = float(value)
    except (ValueError, TypeError):
        return "unavailable"
    if not math.isfinite(value):
        return "unavailable"
    if rule["direction"] == "below":
        return "bad" if value < rule["offline_threshold"] else "warning" if value < rule["warning_threshold"] else "good"
    return "bad" if value > rule["offline_threshold"] else "warning" if value > rule["warning_threshold"] else "good"


def derived_connectivity(state, rule: dict) -> str:
    value = getattr(state, "state", None)
    if value is None or str(value).strip().lower() in {"unknown", "unavailable", "none", ""}:
        return "offline"
    if rule["method"] == "availability":
        return "online"
    return "offline" if signal_quality(value, rule) in {"bad", "unavailable"} else "online"
