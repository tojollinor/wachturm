"""Bounded, credential-free audit trail separate from sensor/alarm events.

Attribution records the evidence source, never claims a remote keypress proves
which human was physically holding a device. Entries are not tamper-proof.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .event_context import current_event

MAX_AUDIT = 1000
_ALLOWED_OUTCOMES = frozenset({'success', 'denied', 'failed'})
_ALLOWED_SOURCES = frozenset({'ha_account', 'panel_pin', 'homematic', 'configuration', 'system', 'entity', 'event'})


@dataclass(slots=True)
class AuditTrail:
    events: list[dict] = field(default_factory=list)
    limit: int = MAX_AUDIT
    changed: Callable[[], None] | None = None

    def record(self, *, action: str, outcome: str, source: str, area: str | None = None,
               mode: str | None = None, user_key: str | None = None,
               user_name: str | None = None, device: str | None = None,
               identity_confidence: str = 'unverified', detail: str | None = None) -> None:
        if outcome not in _ALLOWED_OUTCOMES or source not in _ALLOWED_SOURCES:
            raise ValueError('Invalid audit outcome/source')
        if not isinstance(action, str) or not action or self.limit < 1:
            raise ValueError('Invalid audit event')
        # Never accept arbitrary event data or service payloads: they can include secrets.
        event = {'at': datetime.now(timezone.utc).isoformat(), 'action': action,
                 'outcome': outcome, 'source': source, 'area': area, 'mode': mode,
                 'user_key': user_key, 'user_name': user_name, 'device': device,
                 'identity_confidence': identity_confidence, 'detail': detail, 'event_id': current_event.get()}
        self.events.append(event)
        del self.events[:-self.limit]
        if self.changed is not None:
            self.changed()

    def snapshot(self) -> dict:
        return {'schema': 1, 'events': [dict(e) for e in self.events]}

    def restore(self, raw: Any) -> None:
        if not isinstance(raw, dict) or raw.get('schema') != 1 or not isinstance(raw.get('events'), list):
            raise ValueError('Invalid audit snapshot')
        if len(raw['events']) > self.limit:
            raise ValueError('Audit snapshot exceeds limit')
        expected = {'at','action','outcome','source','area','mode','user_key','user_name','device','identity_confidence','detail'}
        checked = []
        for event in raw['events']:
            if not isinstance(event, dict) or set(event) not in (expected, expected | {"event_id"}):
                raise ValueError('Malformed audit event')
            if any(value is not None and not isinstance(value, str) for value in event.values()):
                raise ValueError('Malformed audit field')
            if event['source'] not in _ALLOWED_SOURCES or event['outcome'] not in _ALLOWED_OUTCOMES:
                raise ValueError('Malformed audit outcome')
            checked.append(dict(event))
        self.events = checked

    def query(self, *, user_key: str | None = None, source: str | None = None,
              area: str | None = None, action: str | None = None,
              device: str | None = None, since: str | None = None,
              until: str | None = None, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 250:
            raise ValueError('Invalid query limit')
        filters = {'user_key': user_key, 'source': source, 'area': area,
                   'action': action, 'device': device}
        return [dict(x) for x in reversed(self.events)
                if all(v is None or x.get(k) == v for k,v in filters.items())
                and (since is None or x['at'] >= since)
                and (until is None or x['at'] <= until)][:limit]
