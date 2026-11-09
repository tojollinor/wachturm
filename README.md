<p align="center">
  <img src="docs/images/wachturm.png" width="120" alt="Wachturm: ein Wachturm in einem Schild">
</p>

<h1 align="center">Wachturm</h1>

<p align="center">Deine Alarmzentrale für Home Assistant.<br>Geräte verbinden. Modi festlegen. Alarme im Blick behalten.</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/marketplace_repository/?owner=tojollinor&amp;repository=wachturm&amp;category=integration"><img src="docs/images/add-to-home-assistant.svg" alt="Wachturm in Home Assistant hinzufügen"></a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=ha_alarmzentrale"><img src="docs/images/setup-in-home-assistant.svg" alt="Wachturm in Home Assistant einrichten"></a>
</p>

Wachturm verbindet vorhandene Home-Assistant-Geräte und Entitäten zu einer übersichtlichen Alarmsteuerung. Du entscheidest, welche Kontakte in welchem Modus überwacht werden, wann ein Alarm startet und welche Aktionen darauf reagieren.

Die Bedienung erfolgt über eine eigene Oberfläche und eine passende Dashboard-Card. Ein zentrales Alarm-Control-Panel verbindet Wachturm mit Home Assistant.

[Installation](#installation) · [Erste Einrichtung](#in-wenigen-schritten-zur-eigenen-alarmsteuerung) · [Dashboard-Card](#dashboard-card) · [Benachrichtigungen](#benachrichtigungen-und-sprachausgabe) · [Hilfe](#hilfe-und-fehlerberichte)

## Was Wachturm kann

| Funktion | Was du damit machst |
| --- | --- |
| **Sensoren** | Geräte und Entitäten auswählen; Tür, Fenster, Bewegung und weitere Sensortypen mit passenden Statussymbolen anzeigen. |
| **Bereiche** | Sensoren zum Beispiel als Außenhaut, Erdgeschoss oder Garage zusammenfassen. |
| **Modi** | Abwesend, Zuhause, Nacht und eigene Modi mit unterschiedlichem Sensorverhalten anlegen. |
| **Sabotage** | Sabotagekontakte getrennt überwachen und je Sensor sowie Modus berücksichtigen. |
| **Aktionen** | Sirenen, Licht, Schalter, Skripte und Benachrichtigungen nach Bereich, Modus und Ereignis ausführen. |
| **Bediengeräte** | Fernbedienungen, Tasterereignisse, Entitäten und Schalter mit Bedienhandlungen verbinden. |
| **Benutzer und PIN** | Persönliche Rechte, Rollen und PIN-Regeln für Aktivieren, Deaktivieren, Stumm und Quittieren festlegen. |
| **Diagnose** | Erreichbarkeit, Batterie, RSSI sowie Homematic Duty Cycle und Carrier Sense verfolgen. |
| **Historie** | Bedienhandlungen, Auslösungen, Normalisierungen und zusammengehörige Ereignisse nachvollziehen. |
| **Vollbildalarm** | Pulsierende Alarmansicht mit Auslösern, Sensorkacheln und laufender Alarmdauer öffnen. |

### Beispiel: dieselbe Haustür, unterschiedliche Reaktionen

| Modus | Bei geöffneter Haustür |
| --- | --- |
| Nacht | Sofort Alarm auslösen. |
| Abwesend | Zunächst die eingestellte Standby-/Eingangszeit abwarten. |
| Zuhause | Den Kontakt nach deiner gewählten Regel berücksichtigen oder ausnehmen. |

Pro Sensor und Modus lässt sich zusätzlich festlegen, wie ein bereits offener Kontakt beim Aktivieren behandelt wird: blockieren, vorübergehend ignorieren oder nach dem Schließen überwachen. Eine passende Schließregel kann die Scharfschaltung beim Schließen abschließen.

## Installation

### Voraussetzungen

- Home Assistant **2026.3 oder neuer**.
- Für die Installation per Button: der Marketplace oder eine passende HACS-Installation.
- Die gewünschten Sensoren und Geräte sind bereits in Home Assistant verfügbar.
- Smartphone-Benachrichtigungen benötigen die Home Assistant Companion App und den zugehörigen `notify.mobile_app_…`-Dienst.

### Über den Marketplace oder HACS

1. Klicke oben auf **Wachturm in Home Assistant hinzufügen**.
2. Bestätige das Repository und lade die Integration herunter.
3. Starte Home Assistant neu.
4. Öffne **Einstellungen → Geräte & Dienste → Integration hinzufügen → Wachturm**. Alternativ verwende den zweiten Button oben.

Der Button öffnet die Repository-Seite deiner Home-Assistant-Instanz. Hinzufügen und Installieren bestätigst du dort selbst.

Für ältere HACS-Oberflächen gibt es auch den [HACS-Direktlink](https://my.home-assistant.io/redirect/hacs_repository/?owner=tojollinor&repository=wachturm&category=integration). Falls das Repository manuell ergänzt werden muss: unter **Benutzerdefinierte Repositories** `https://github.com/tojollinor/wachturm` mit dem Typ **Integration** hinzufügen.

### Manuell

1. Lade das Repository herunter.
2. Kopiere `custom_components/ha_alarmzentrale` in das gleichnamige Verzeichnis deiner Home-Assistant-Konfiguration.
3. Starte Home Assistant neu und füge **Wachturm** unter Geräte & Dienste hinzu.

Der technische Ordnername bleibt `ha_alarmzentrale`. Das erhält auch bei bestehenden Installationen die Zuordnung zu Geräten, Entitäten und gespeicherten Einstellungen.

## In wenigen Schritten zur eigenen Alarmsteuerung

1. **Benutzer freigeben:** Öffne Wachturm mit einem Administratorkonto. Ordne unter **Benutzer** ein Home-Assistant-Konto zu und vergib die gewünschten Rollen oder Rechte.
2. **Sensoren hinzufügen:** Wähle das Gerät und die überwachten Entitäten. Ergänze bei Bedarf Sabotagekontakt, Batterie- und Verbindungsquellen.
3. **Bereiche anlegen:** Fasse die passenden Sensoren zusammen. Der feste Sabotagebereich übernimmt die zugeordneten Sabotagekontakte.
4. **Modi einrichten:** Ordne Bereiche zu, stelle Zeiten ein und wähle das Verhalten je Sensor. Modi können zunächst auch ohne Bereich angelegt werden.
5. **Aktionen festlegen:** Wähle Empfänger oder Ausgänge, Ereignis, Zeitpunkt, Bereiche und Modi.
6. **Funktion prüfen:** Verwende den Sensortest unter Diagnose und den Aktionstest direkt im jeweiligen Aktionsdialog.

**Speichern** im jeweiligen Dialog übernimmt die Änderung direkt. Konfigurationsänderungen sind möglich, wenn die Anlage deaktiviert, Alarme quittiert und laufende Aktionen abgeschlossen sind.

### Wann Aktionen ausgeführt werden

Wähle zuerst ein **Ereignis**, danach den **Zeitpunkt**:

| Ereignis | Vor | Während | Nach |
| --- | --- | --- | --- |
| Scharfschalten | Angenommener Schaltauftrag nach erfolgreicher Bereitschaftsprüfung | Beginn der Ausgangszeit | Überwachung ist scharf |
| Alarm | Beginn der Eingangszeit | Alarmauslösung | Alarm quittiert oder Anlage deaktiviert |
| Unscharfschalten | – | – | Anlage deaktiviert |
| Batteriewarnung | – | Warnung erkannt | Batterie wieder normal |
| Verbindungsverlust | – | Verlust erkannt | Gerät wieder erreichbar |
| Instabile Verbindung | – | Instabilität erkannt | Verbindung über die Erholungszeit stabil |

Ohne Ausgangs- oder Eingangszeit entfällt der jeweilige Verzögerungszeitpunkt. Aktionen laufen asynchron und halten die Scharfschaltung nicht auf. Störungen sind nicht vorhersehbar; deshalb ist dort **Vor** gesperrt. Bestehende Aktionen behalten ihr bisheriges Verhalten. Mobile Alarmprofile und automatisch beendete Alarm-Ausgänge stehen für **Alarm → Während** zur Verfügung.

## Dashboard-Card

Die Card wird zusammen mit der Integration bereitgestellt. Wähle beim Bearbeiten eines Dashboards **Wachturm – Alarmsteuerung** und anschließend das zentrale Wachturm-Panel.

Die Entitätsauswahl zeigt ausschließlich Panels dieser Integration. Vorhandene Panels anderer Integrationen bleiben getrennt.

Alternativ per YAML:

```yaml
type: custom:ha-alarmzentrale-card
entity: alarm_control_panel.wachturm
```

Ersetze die Beispiel-Entität durch dein tatsächlich vorhandenes zentrales Panel. Wenn die automatische Bereitstellung in deinem Dashboard nicht greift, ergänze `/ha_alarmzentrale_assets/alarmzentrale-card.js?v=0.8.1` als JavaScript-Modul unter den Dashboard-Ressourcen.

Bei konfiguriertem PIN-Schutz öffnet sich das integrierte PIN-Pad. Blockierte Modi sind nicht aktivierbar. Im Alarmfall pulsieren Symbol und Kartenrahmen; Stummschalten lässt Auslöser und Alarmdauer sichtbar.

## Benachrichtigungen und Sprachausgabe

Im Aktionsdialog stehen getrennte Profile für **Android** und **iOS** bereit. Titel und Nachricht lassen sich mit anklickbaren Bausteinen ergänzen:

`{alarm_message}` · `{sensor}` · `{area}` · `{mode}` · `{time}`

Beispiel:

```text
{alarm_message}
Bereich: {area} · Modus: {mode}
Seit: {time}
```

Android unterstützt optional TTS mit eigenem Ansagetext, wiederholten Ansagen, dem Alarm-Audiokanal und vorübergehend maximaler Alarmlautstärke. Ein leeres TTS-Textfeld verwendet den Benachrichtigungstext. Die Wiederholung endet beim Stummschalten, Quittieren, Deaktivieren oder Ablauf der Alarmaktionsdauer.

Für iOS sind kritische Hinweise vorgesehen. Ton, Sperrbildschirmanzeige und das Verhalten im Nicht-stören-Modus hängen von den Freigaben am jeweiligen Smartphone ab. Der Aktionsdialog enthält dazu Einrichtungshinweise.

Ein Tipp auf die Alarmmeldung öffnet die Vollbildansicht. Aktionsbuttons führen zu den passenden Bedienhandlungen; Berechtigungen und konfigurierte PIN-Regeln werden weiterhin geprüft. Die Option zum automatischen Öffnen und Einschalten des Displays ist unter Android geräteabhängig und entsperrt keinen geschützten Sperrbildschirm.

### Eine Aktion gezielt prüfen

**Testen / Simulieren** im Aktionsdialog verwendet die aktuellen Eingaben, ohne sie zu speichern. Benachrichtigungen und TTS sind als **TEST** gekennzeichnet. Der Test endet nach spätestens 60 Sekunden und kann vorher stummgeschaltet oder beendet werden. Beim Start einer anderen Aktion wird der vorherige Test vollständig beendet.

Die echte Anlage bleibt dabei deaktiviert. Ein gewählter Sirenen-, Licht- oder Schalterausgang wird tatsächlich bedient. Selbst eingeschaltete unterstützte Ausgänge werden nach Testende wieder beendet; Skripte und beliebige andere Serviceaktionen haben ihre konfigurierten Wirkungen.

## Homematic und Homematic IP

Homematic-Geräte werden über ihre vorhandene Home-Assistant-Anbindung eingebunden. Das gilt beispielsweise für CCU-/RaspberryMatic-Systeme mit entsprechenden Home-Assistant-Integrationen.

Wachturm kann Öffnungskontakte, Sabotage, `LOW_BAT`, Spannung, Erreichbarkeit und RSSI verwenden, soweit diese Werte als Entitäten oder Attribute vorliegen. Duty Cycle und Carrier Sense lassen sich mit Verlaufsgrafiken anzeigen. Fernbedienungen und Taster werden über ihre konfigurierten Ereignisse zugeordnet.

Für binäre Diagnosewerte wird die Bedeutung ausdrücklich zugeordnet. Unbekannte oder nicht verfügbare Werte werden entsprechend angezeigt. Eine Erreichbarkeitsprüfung kann die Verfügbarkeit einer Referenzentität oder einen RSSI-Schwellwert verwenden.

## Rechte und Daten

Die Verwaltung ist Home-Assistant-Administratoren vorbehalten. Die Bedienung prüft zusätzlich die in Wachturm eingerichteten Benutzer, Rollen und Modusrechte. Ein Home-Assistant-Konto allein ersetzt diese Zuordnung nicht.

PINs werden ausschließlich auf dem Server geprüft und als Hash gespeichert. Die Alarmkonfiguration und Historie liegen lokal in Home Assistant. Smartphone-Nachrichten verwenden den von dir gewählten Home-Assistant-Dienst.

Vor Updates empfiehlt sich eine Sicherung der Home-Assistant-Konfiguration. Ein Konfigurationsexport enthält auch sensible Einstellungen und gehört nicht in öffentliche Fehlerberichte.

## Hilfe und Fehlerberichte

Fragen und Fehler können über [GitHub Issues](https://github.com/tojollinor/wachturm/issues) gemeldet werden. Hilfreich sind Home-Assistant-Version, Wachturm-Version, erwartetes Verhalten und kurze Schritte zur Reproduktion. Bitte Gerätekennungen, Namen und Logauszüge vor dem Teilen anonymisieren und keine PINs oder Zugangsdaten veröffentlichen.

Änderungen stehen im [Changelog](CHANGELOG.md). Beiträge sind willkommen; Hinweise dazu findest du unter [Contributing](CONTRIBUTING.md).

## Lizenz

[MIT](LICENSE) · Wachturm contributors
