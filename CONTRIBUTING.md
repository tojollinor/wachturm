# Beitragen

Fehlerberichte und konkrete Verbesserungsvorschläge sind über GitHub Issues willkommen. Bitte vor dem Melden nach vorhandenen Einträgen suchen und vertrauliche Daten entfernen.

Änderungen bitte in einem eigenen Branch bearbeiten und als Pull Request einreichen. Beschreibe das Problem, das neue Verhalten und die durchgeführten Prüfungen.

## Prüfungen ausführen

Python 3.13 und Node.js 22 werden für die automatisierten Prüfungen verwendet.

```sh
python -m unittest discover -s tests
node --test tests/sensor-picker.test.cjs
python -m compileall -q custom_components/ha_alarmzentrale
```

Für die Browserprüfungen:

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python tools/check_ui.py
python tools/check_revision9_ui.py
```

Unter Windows verwenden die Browserprüfungen Microsoft Edge, unter Linux Chromium. Für Headless-Linux kann Playwright zusätzliche Systempakete benötigen: `python -m playwright install --with-deps chromium`.

Die Prüfungen verwenden isolierte Beispieldaten und senden keine echten Benachrichtigungen. Änderungen an Alarmablauf, Berechtigungen und PIN-Prüfung benötigen passende automatisierte Prüfungen; geräteabhängiges Verhalten wird zusätzlich in Home Assistant überprüft.

Technische Domain, Entitätskennungen, Speicherformat und URLs sind bestehende Schnittstellen. Änderungen daran müssen die Übernahme vorhandener Konfiguration berücksichtigen.
