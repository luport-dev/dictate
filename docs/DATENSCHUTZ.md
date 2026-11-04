# Datenschutz und DSGVO – Dokumentation zu Dictate

Stand: Version 0.2.3

Dieses Dokument beschreibt, welche Daten Dictate verarbeitet, wo sie liegen und welche Netzwerkzugriffe es gibt. Es soll Datenschutzbeauftragten, IT und Betriebsrat eine Bewertung ermöglichen.

**Wichtig:** Eine Software kann nicht „DSGVO-zertifiziert“ sein. Die DSGVO bewertet die Verarbeitung durch den Verantwortlichen (Firma oder Person), nicht das Programm. Dictate ist so gebaut, dass diese Bewertung einfach ausfällt (Abschnitt 1). Dies ist eine technische Dokumentation und keine Rechtsberatung. Die rechtliche Einordnung bleibt Sache des Verantwortlichen (Abschnitt 8).

## 1. Zusammenfassung

| Aspekt | Aussage |
|---|---|
| Spracherkennung | Vollständig lokal auf der CPU (Parakeet, sherpa-onnx). Keine Cloud, keine API. |
| Netzwerk im Betrieb | **Keine.** Dictate öffnet beim Diktieren und im Leerlauf keine Netzwerkverbindung. |
| Netzwerk insgesamt | Nur der **einmalige, manuelle Modell-Download** von HuggingFace (Abschnitt 3). |
| Audio | Nur im Arbeitsspeicher. Wird nach der Transkription verworfen. **Nie gespeichert.** |
| Transkribierter Text | Wird an der Cursorposition eingegeben. **Nie gespeichert, nie geloggt.** |
| Telemetrie | Keine eigene. Die von `huggingface_hub` ist abgeschaltet. |
| Auftragsverarbeiter, Drittlandtransfer | Keine, weil keine Diktatdaten das Gerät verlassen. |

## 2. Verarbeitete Daten und Speicherorte

### 2.1 Im Arbeitsspeicher (flüchtig)

- **Audioaufnahme** vom Mikrofon, während der Hotkey gedrückt ist. Sie wird nach der Transkription verworfen. Eine Aufnahme wird nie auf die Festplatte geschrieben. Die frühere Funktion, fehlgeschlagene Aufnahmen als WAV zu behalten, wurde in Version 0.2.2 entfernt.
- **Transkribierter Text**, nur bis zur Eingabe in das aktive Fenster.
- **Tastendrücke:** Für jede Taste wird nur im Moment des Drucks geprüft, ob es der konfigurierte Hotkey ist. Ist es eine andere Taste, wird lediglich ein Signal „andere Taste“ ausgelöst (um eine Tastenkombination wie Strg+C zu erkennen und die Aufnahme zu verwerfen). Welche Taste das war, wird nicht gemerkt.

### 2.2 Auf der Festplatte

| Datei | Inhalt | Personenbezug |
|---|---|---|
| `config.json` (Windows: `%APPDATA%\dictate\`, Linux: `~/.config/dictate/`) | Modus, Hotkey, Mindest-Haltedauer, CPU-Threads, Name des Mikrofons, Einfüge-Kürzel. Unter Linux mit Rechten 600. | Gering. Der Mikrofonname kann ein Gerät identifizieren, enthält aber keine Diktatdaten. |
| Modell (`models/parakeet-primeline-onnx/`, im lokalen Datenverzeichnis, ca. 640 MB) | Gewichte des Spracherkennungsmodells | Keiner |
| `dictate.log` (Windows: `%LOCALAPPDATA%\dictate\`, Linux: `~/.local/state/dictate/`) | **Nur Warnungen und Fehler** (z. B. Mikrofon nicht verfügbar, Tippen fehlgeschlagen). Maximal ca. 1 MB je Datei, 3 Dateien. | Gering. Fehlerzeilen tragen einen Zeitstempel. **Kein** Eintrag für erfolgreiche Diktate, **kein** Text, keine Audiodaten. Es lässt sich daraus nicht ablesen, wann oder wie oft diktiert wurde. |
| `instance.lock` | Sperrdatei gegen Doppelstart | Keiner |

Seit Version 0.2.3 schreibt Dictate keine Informationszeilen mehr ins Log. Sie wurden entfernt, weil sie ein Nutzungsprotokoll (Zeitpunkt, Dauer, Pegel, Zeichenzahl je Aufnahme) bildeten.

### 2.3 Zwischenablage

- **Windows und Linux/X11:** Der Text wird direkt per Tastatureingabe-Schnittstelle getippt. Die Zwischenablage wird **nicht** benutzt.
- **Linux/Wayland:** Der Text liegt für ca. 0,3 Sekunden in der Zwischenablage, um ihn per Strg+V bzw. Strg+Umschalt+V einzufügen. Danach wird der vorherige Inhalt wiederhergestellt. In diesem Moment könnte ein auf dem System laufender Zwischenablage-Verlauf (Clipboard-Manager) den Text mitprotokollieren. Das liegt außerhalb von Dictate und ist vom Verantwortlichen zu bewerten (Abschnitt 7).

## 3. Netzwerkzugriffe

### 3.1 Einmaliger Modell-Download (manuell)

- Wird nur ausgelöst, wenn der Nutzer in den Einstellungen auf „Download model“ klickt oder den Erststart-Dialog bestätigt.
- Quelle: HuggingFace-Repository `flozen1981/parakeet-primeline-onnx`, festgelegte Revision `d548e25b9bfe559aa274f361892dc4ed5d64743a`.
- Dabei erfährt HuggingFace (bzw. dessen CDN) die **IP-Adresse** und technische Verbindungsdaten des Rechners. **Diktatdaten werden nicht übertragen.**
- Die anonyme Nutzungsstatistik der Bibliothek `huggingface_hub` ist abgeschaltet (`HF_HUB_DISABLE_TELEMETRY=1`, gesetzt am Anfang von `src/core.py`, bevor die Bibliothek geladen wird).
- Danach läuft Dictate ohne Netzwerk. Alternativ kann das Modell vorab in das Modellverzeichnis kopiert werden, dann ist kein Download nötig. Ein Windows-Installer mit eingebautem Modell ist als spätere Option vorgesehen und würde den Download ganz entfallen lassen.

### 3.2 Alle anderen Funktionen

Im eigenen Code existiert kein weiterer Netzwerkzugriff (keine HTTP-Clients, keine Sockets, keine Update-Prüfung, keine Fehlerberichte, keine Telemetrie). Dictate prüft nicht auf neue Versionen.

### 3.3 Prüfung (Stand Version 0.2.3, Windows 11)

Die echte App wurde ca. 40 Sekunden mit Tray-Icon, Hotkey-Listener, Modell-Preload und einer Mikrofonaufnahme samt Transkription ausgeführt. Mitgeschnitten wurden:

1. **Python-Ebene:** `connect`, `connect_ex`, `sendto`, `bind`, `listen` und DNS-Abfragen (`getaddrinfo`) wurden abgefangen.
2. **Betriebssystem-Ebene:** `netstat -ano` wurde alle 0,25 Sekunden abgefragt (143 Abfragen) und auf den Prozess gefiltert.

**Ergebnis: 0 Netzwerkaufrufe, 0 Einträge.**

Grenzen dieser Prüfung:
- `netstat` ist eine Momentaufnahme. Eine extrem kurze Verbindung von nativem C-Code zwischen zwei Abfragen wäre theoretisch möglich.
- Der Test lief mit den dort installierten Paketversionen (PyQt6 6.11.0, sounddevice 0.5.5, pynput 1.8.2, numpy 2.4.4, sherpa-onnx 1.13.8, soundfile 0.14.0, huggingface_hub 1.11.0). Andere Versionen wurden nicht geprüft.
- Ein Paketmitschnitt mit Administratorrechten wurde nicht durchgeführt. Wer einen formalen Nachweis braucht, kann ihn in einer Admin-PowerShell erstellen:

  ```
  pktmon start --capture --pkt-size 0 -f dictate.etl
  # Dictate 1–2 Minuten benutzen
  pktmon stop
  pktmon etl2txt dictate.etl
  ```

  Es sollten keine Pakete des Dictate-Prozesses (`pythonw.exe`) auftauchen.

## 4. Globale Tastatur- und Mausüberwachung

Damit der Hotkey in jeder Anwendung funktioniert, registriert Dictate einen systemweiten Eingabe-Listener:

- **Windows und Linux/X11:** über `pynput`.
- **Linux/Wayland:** über `evdev` (liest `/dev/input/event*`). Dafür muss der Nutzer in der Gruppe `input` sein. Diese Gruppe erlaubt das Lesen **aller** Eingabegeräte des Rechners. Das ist ein reales Sicherheitsthema und sollte bei der Freigabe bewusst entschieden werden.

Dictate wertet nur aus, ob der konfigurierte Hotkey gedrückt oder losgelassen wird. Bei jeder anderen Taste wird nur ein Signal „andere Taste gedrückt“ ausgelöst, ohne zu merken, welche. Tastatureingaben werden **nicht** gespeichert, protokolliert oder übertragen, und es werden keine Zeichen oder Texte aus ihnen gebildet (nur Tastencodes, kein Text).

Unter Linux/Wayland öffnet der Listener nur Tastaturen sowie Mäuse mit Seitentasten und liest deren Tastenereignisse passiv, ohne sie zu verändern oder abzufangen (`evdev_listener.py`). Die virtuelle Tastatur von `ydotool` wird absichtlich ausgelassen.

Für den Betriebsrat ist dennoch relevant, dass technisch ein systemweiter Listener läuft. Dass er nichts aufzeichnet, ergibt sich aus dem Quellcode (`src/gui.py`, `src/evdev_listener.py`) und lässt sich dort prüfen.

## 5. Datensparsamkeit und Technik

- **Privacy by Design:** Lokale Verarbeitung, keine Speicherung, kein Netzwerk im Betrieb.
- **Quellcode offen** und damit prüfbar. Das Modell ist in fester Revision festgelegt, sodass immer dieselben geprüften Dateien geladen werden.
- **Lizenz des Modells:** CC-BY-4.0 (primeline, NVIDIA, k2-fsa/sherpa-onnx). Die Namensnennung steht in der README.

## 6. Betroffenenrechte

Da Dictate keine personenbezogenen Diktatdaten speichert, gibt es für Auskunft, Berichtigung, Löschung und Datenübertragbarkeit nichts herauszugeben oder zu löschen. Die wenigen vorhandenen Dateien (Abschnitt 2.2) enthalten keine Diktatinhalte und können vom Nutzer jederzeit gelöscht werden (Ordner `dictate` unter `%APPDATA%` und `%LOCALAPPDATA%` bzw. `~/.config/dictate` und `~/.local/state/dictate`).

## 7. Restrisiken

1. **Zwischenablage-Verlauf (nur Wayland):** Ein Clipboard-Manager kann den Text im Moment des Einfügens mitprotokollieren. Abhilfe: solche Dienste auf dem Arbeitsplatz nicht zulassen oder auf X11 bzw. Windows ausweichen.
2. **Diktierte Inhalte:** Was diktiert wird, landet in der Zielanwendung (Mail, Dokument, Chat) und unterliegt dort den Regeln dieser Anwendung. Dictate hat darauf keinen Einfluss.
3. **Besondere Datenkategorien (Art. 9 DSGVO):** Dictate speichert nichts davon, aber der Verantwortliche sollte festlegen, ob solche Inhalte diktiert werden dürfen.
4. **Gruppe `input` unter Linux/Wayland** (Abschnitt 4).
5. **Drittpakete:** Die Netzwerkfreiheit wurde für die genannten Versionen geprüft (Abschnitt 3.3). Nach Paket-Updates sollte die Prüfung wiederholt werden.
6. **Modell-Download:** Die IP-Adresse wird an HuggingFace übermittelt (Abschnitt 3.1). Vermeidbar durch Bereitstellung des Modells im Firmennetz.

## 8. Vom Verantwortlichen zu klären

Diese Punkte kann kein Code erledigen:

- Aufnahme in das **Verzeichnis von Verarbeitungstätigkeiten** (Art. 30 DSGVO), falls die Verarbeitung dort erfasst werden muss.
- Prüfung durch den **Datenschutzbeauftragten**, bei Bedarf **Betriebsrat** bzw. Betriebsvereinbarung (systemweiter Eingabe-Listener, Abschnitt 4).
- **Rechtsgrundlage und Information der Beschäftigten**, falls Dictate dienstlich eingesetzt wird.
- Regeln, **welche Inhalte** diktiert werden dürfen (Art. 9).
- **Technisch-organisatorische Maßnahmen** auf dem Gerät: Festplattenverschlüsselung, Zugriffsschutz, keine Zwischenablage-Verlaufsdienste.
- Entscheidung, ob eine **Datenschutz-Folgenabschätzung** nötig ist. Aufgrund der rein lokalen Verarbeitung ohne Speicherung ist das nach technischer Einschätzung eher unwahrscheinlich, aber vom Verantwortlichen zu beurteilen.

## 9. Änderungshistorie (datenschutzrelevant)

| Version | Änderung |
|---|---|
| 0.2.2 | Speicherung fehlgeschlagener Aufnahmen als WAV entfernt. `huggingface_hub`-Telemetrie abgeschaltet. |
| 0.2.3 | Log auf Warnungen und Fehler beschränkt. Informationszeilen (Nutzungsprotokoll) entfernt. |
