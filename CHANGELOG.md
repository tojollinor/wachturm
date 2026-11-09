# Änderungen

## 0.8.6

- Sensorliste und Überwachungsschalter vollständig aus den Bereichseinstellungen entfernt; Änderungen bleiben direkt in der Bereichsübersicht.
- Card mit automatischer Rasterhöhe und Aktualisierung bereits registrierter älterer Card-Versionen.
- Ereigniskennungen bleiben über Hintergrundaktionen und Verzögerungen hinweg erhalten. Sensor-Baselines beim Start gehören zu einem gemeinsamen Vorgang.
- Aktionen für Quittieren und Stummschalten, passend zum jeweiligen Zeitpunkt.
- Ein-/Ausschalter für Aktionen und persistente Anzeige der letzten Ausführung samt Ergebnis.
- Kontextabhängige Optionen und eigene Ausschaltzeiten für unterstützte Ausgänge auch außerhalb eines Alarms.
- Identische modusweite Aktionen werden pro auslösendem Ereignis nur einmal ausgeführt.

## 0.8.5

- Deutlich kompaktere Dashboard-Card mit natürlicher Höhe und näher am Text platziertem Statussymbol.
- Eine suchbare, alphabetische HA-Aktionsauswahl ersetzt die bisherigen Kategorien und die zweite Dienstliste.
- Einheitlich umrahmte Historieneinträge mit konsistenter Ausrichtung, Zeitstempeln und passenden Statusfarben.
- Verfügbarkeitswarnungen werden nicht mehr als erfolgreich dargestellt; doppelte Systemangaben entfallen.
- Sabotageüberwachung direkt am Sensor schalten, mit sicherem Zurücksetzen bei Speicherfehlern.
- Versionierte Updates als GitHub-Release nach erfolgreichen Prüfungen auf main.

## 0.8.4 (noch nicht veröffentlicht)

- Feste Standardrolle Admin mit allen Bedienrechten für bestehende und zukünftige Bereiche einschließlich Sabotage.
- Admin wird nur ausdrücklich zugewiesen; Konto-, PIN- und Home-Assistant-Administratorschutz bleiben erhalten.
- Hinweise zur Update-Bereitstellung ergänzt.

## 0.8.3 (noch nicht veröffentlicht)

- Bereichsauswahl bei Aktionen und Aktionstests entfernt; Zuordnung erfolgt über Modi.
- Modusweite Aktionen werden beim Anlegen neuer Bereiche berücksichtigt.
- „Andere Home-Assistant-Aktion“ öffnet den Editor und bietet eine Auswahl der verfügbaren HA-Aktionen mit Zielentitäten und zusätzlichen Daten.

## 0.8.2 (noch nicht veröffentlicht)

- Bereiche und Sensoren in einer gemeinsamen Verwaltung zusammengeführt; der separate Sensor-Tab entfällt.
- Sensoren direkt im Bereich hinzufügen, bearbeiten, löschen und in einen anderen Bereich verschieben.
- Nur eine reguläre Bereichszuordnung pro Sensor; Sabotage bleibt zusätzlich zentral überwacht.
- Vorhandene Mehrfachzuordnungen bleiben bis zur ausdrücklichen Neuzuordnung erhalten.
- Beim Löschen eines Bereichs bleiben die Sensoren zur Neuzuordnung erhalten.

## 0.8.1 (noch nicht veröffentlicht)

- Kompaktere Statusanzeige und Dashboard-Card mit zustandsabhängigen Symbolen; das Logo bleibt im Verwaltungskopf und bei der Integration.
- Aktionen mit getrennter Auswahl von Ereignis und Zeitpunkt (Vor / Während / Nach).
- Scharfschaltaktionen nach erfolgreicher Bereitschaftsprüfung, zum Beginn der Ausgangszeit oder nach abgeschlossener Scharfschaltung.
- Aktionen bei Beginn einer Eingangszeit und nach Beendigung eines Alarms.
- Batterie- und Verbindungsaktionen bei Störung oder Wiederherstellung; kein vorheriger Zeitpunkt für unvorhersehbare Störungen.
- Vorhandene Aktionen behalten ihren bisherigen Ausführungszeitpunkt.

## 0.8.0

- Alarmsteuerung mit Sensoren, Bereichen, eigenen Modi und konfigurierbaren Aktionen.
- Unterschiedliche Sensorregeln je Modus, einschließlich Standby-Zeit und Umgang mit offenen Kontakten.
- Sabotageüberwachung, Batterie- und Verbindungsdiagnose sowie RSSI- und Homematic-Funkverläufe.
- Benutzer, Rollen, PIN-Pad und Zuordnung externer Bediengeräte.
- Zentrales Home-Assistant-Alarm-Control-Panel und eigene Dashboard-Card.
- Pulsierende Vollbildansicht, Sensorkacheln, Bereitschaftsanzeige und laufende Alarmdauer.
- Android-/iOS-Benachrichtigungen, eigener TTS-Text und wiederholte Sprachausgabe.
- Gezielte Sensor- und Aktionstests mit getrennten Empfängern.
- Lokales Wachturm-Logo und direkte Projektverlinkung in der Oberfläche.
