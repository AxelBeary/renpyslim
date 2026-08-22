# RenPySlim

> Die All-in-One-Werkzeugkiste zum Abspecken und Verpacken von Ren'Py-Ressourcen · Ren'Py asset slimming & packaging toolkit

**Sprache / Language:** [简体中文（默认）](../README.md) | [English](README.en.md) | [Русский](README.ru.md) | [Español](README.es.md) | [Português (BR)](README.pt.md) | [Türkçe](README.tr.md) | **Deutsch** | [Français](README.fr.md)

**Lizenz: [AGPL-3.0](../LICENSE)** · Hinweise zu Drittanbietern findest du in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

> Dieses Projekt wurde mit Hilfe von KI zusammengehackt ("tief geschissen"). Wir empfehlen dir, den Code vor der Nutzung zu prüfen. Die Entwickler übernehmen keinerlei Verantwortung für Folgen einer falschen Verwendung. **Deine Daten sind unbezahlbar!**

---

## Was ist das?

RenPySlim hilft Ren'Py-Spieleentwicklern, ihre Werke **kleiner und aufgeräumter zu machen und zu verpacken** – alles in einem Rutsch:

- **Analysieren** – scannt nach zu großen Ressourcen und liefert einen Bericht über Größe/Probleme/Empfehlungen
- **Komprimieren** – umfassendes Abspecken von Bildern, Audio, Videos und Schriften;
  Verweise in den Skripten werden automatisch umgeschrieben;
  die Standardstufe setzt auf Qualität (q95, nahezu verlustfrei), und die parallele Optimierung nutzt automatisch alle CPU-Kerne aus
- **Verpacken** – ruft das offizielle SDK auf, um PC / Mac / Android-Veröffentlichungspakete zu erstellen
- **Fertige Spiele abspecken** – bereits verpackte Spiele (Ordner oder zip/7z/rar) sicher abspecken, rein und raus, ohne Umwege
- **APK abspecken** – auch Android-Pakete können abspecken: Bilder zu WebP, Audio zu OGG
  (Remapping zur Laufzeit, ohne Verweise anzufassen), automatisches Neusignieren
- **Dekompilieren freischalten** (experimentell) – fertige Spiele ohne Quellcode können mit dem eingebauten unrpyc ihren Quellcode zurückgewinnen;
  auch Bilder/Audio innerhalb des Pakets lassen sich konvertieren und danach originalgetreu zurück ins RPA-Archiv packen

Dazu gibt's einen vierfachen Gesundheitscheck fürs Projekt: Erkennung nutzloser Ressourcen,
Müllbeseitigung vor dem Verpacken, Erkennung doppelter Dateien und ein Bericht über
fehlende Schriftzeichen. Nach jeder Optimierung läuft automatisch das offizielle lint zur Überprüfung.

**Standardmäßig sicher**: Alle Operationen arbeiten zuerst auf einer Arbeitskopie, die Originale bleiben unangetastet;
"wenn's nicht kleiner wurde, wird nicht ersetzt";
Ressourcen ohne gefundene Verweise werden niemals umbenannt;
jeder Durchlauf erzeugt einen Analysebericht und eine Änderungsliste.

## Schnellstart

**Normale Nutzer**: Geh auf [Releases](https://github.com/AxelBeary/renpyslim/releases),
lade `RenPySlim.exe` herunter, führe es per Doppelklick aus – der Browser öffnet die Bedienoberfläche automatisch.

**Entwickler**:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python main.py            # 启动图形界面
```

## Grafische Oberfläche (empfohlen)

Die Oberfläche nutzt ein Seitenleisten-Layout und unterstützt **中文 / English / Русский / Español / Português (BR) / Türkçe / Deutsch / Français** sowie
**helles/dunkles Doppeldesign** (umschaltbar oben rechts; ohne manuelle Auswahl folgt sie der Browsersprache und dem Systemdesign –
deine Auswahl wird gespeichert). Vier Einstiegspunkte: **Super-Packer / Fertiges Spiel abspecken / APK abspecken / Schriften abspecken**.

### Der Hauptablauf in vier Schritten

1. Pfad eintragen (oder auf "Archiv durchsuchen/Ordner durchsuchen" klicken, um einen Auswahldialog zu öffnen), dann auf "Scannen & Analysieren" klicken → den Analysebericht ansehen
2. Die gewünschten Optimierungen ankreuzen und eine Komprimierungsstufe wählen
3. Auf "Ausführen" klicken → Fortschrittsbalken und Protokoll live mitverfolgen
4. Nach Abschluss das Optimierungsergebnis / die offiziellen Veröffentlichungspakete abholen

### Bequeme Bedienung

- zip / 7z / rar / APK / Ordner **direkt auf das Werkzeug-Symbol ziehen** – wird automatisch eingetragen und öffnet die passende Funktion
- Ziehst du eine neue Datei herein, während das Werkzeug bereits läuft, wird automatisch ein neuer Tab geöffnet statt das Werkzeug erneut zu starten
- Bereits verwendete Pfade landen unter "Zuletzt verwendet" – ein Klick und es geht los

### Die vier Funktionseinstiege

- **Super-Packer**: Gib ein Projektverzeichnis an; nach der Optimierung wird automatisch das offizielle SDK zum Verpacken aufgerufen (PC/Mac/Android).
  Optional kannst du "Beim Verpacken Ressourcen in ein RPA-Archiv packen" ankreuzen (offizieller Weg)
- **Fertiges Spiel abspecken**: Gib einen fertigen Spielordner an oder wirf einfach eine zip / 7z / rar-Datei hinein
  (automatisches Entpacken, Abspecken und danach automatisches Neuverpacken zur Übergabe; passwortgeschützte Archive werden unterstützt);
  RPA-Archive werden automatisch geöffnet, optimiert und wieder aufgebaut;
  enthält das Archiv eine APK, wird automatisch in die sichere APK-Abspeckung übergeleitet;
  der experimentelle Schalter "Skripte dekompilieren, um Formatkonvertierung freizuschalten" in den erweiterten Optionen
  lässt auch Spiele ohne Quellcode von der Formatkonvertierung profitieren
- **APK abspecken**: .apk-Datei auswählen, drei Schritte und fertig (Komprimierungsstufe/Schalter für maximale Abspeckung/Signatur – drei Auswahlen,
  standardmäßig wird automatisch ein neuer Schlüssel erzeugt). Das Ergebnis ist ein direkt installierbares, abgespecktes Paket
- **Schriften abspecken** (eigenständiges Werkzeug): Kein Spielprojekt nötig – einfach Schriftart + Textquelle wählen und abspecken;
  ttc/otc-Sammlungen werden automatisch aufgetrennt und nach Schriftstärke separat ausgegeben; das Original wird nie überschrieben, und eine Liste der verwendeten Zeichen gibt's obendrauf

### Laufzeit-Absicherung

- Während der Ausführung kannst du jederzeit auf "Aufgabe stoppen" klicken (bereits abgeschlossene Teile bleiben erhalten); schlägt eine Aufgabe fehl, wird automatisch ein Crash-Dump gespeichert
- Gibt es eine neue Version, zeigt die Oberfläche einen Hinweis an (Vergleich mit GitHub Releases)
- Fehlen FFmpeg / 7-Zip, zeigt die Oberfläche konkrete Installationsanleitungen (winget-Befehl oder Download-Link)
- Beenden: Rechtsklick auf das Tray-Symbol unten rechts → Werkzeug beenden, oder der Button "Werkzeug beenden" unten links in der Seitenleiste
  (das Browserfenster zu schließen beendet das Werkzeug nicht)

## Headless-Modus (für Skripte/Automatisierung, durchgehend JSON-Ausgabe)

```
python cli.py env                                  # 环境体检
python cli.py analyze <路径> --mode project        # 分析
python cli.py optimize <路径> --preset balanced    # 优化
python cli.py full <工程路径> --platforms pc,mac   # 优化+打包一条龙
python cli.py slimfont <字体> <文本来源...>        # 独立字体瘦身
python cli.py slimapk <apk> --remap --gen-key      # APK 瘦身（图转WebP/音转OGG+重签名）
```

> KI-Assistenten / Automatisierungsskripte: Bitte lies vor dem Aufruf zuerst [AGENTS.md](../AGENTS.md) (enthält Sicherheitsregeln und Fehlerbehebung).

## Systemvoraussetzungen

| Abhängigkeit | Wofür | Hinweise |
|---|---|---|
| Ren'Py SDK | Verpacken, Kompilieren der APK-Remapping-Skripte | Wird normalerweise automatisch gefunden; falls nicht, in der Oberfläche unter "Einstellungen" angeben |
| FFmpeg | Audio/Video-Optimierung | Entweder im PATH oder im bin-Ordner neben dem Programm – beides geht |
| Java/JDK | Android-Verpackung, APK-Neusignierung | Fürs Android-Verpacken muss beim ersten Mal zuerst die Android-Einrichtung im Ren'Py-Launcher abgeschlossen werden |

Der Oberflächendienst lauscht standardmäßig auf 127.0.0.1:52786 (ein selten genutzter Port); ist er belegt,
wird automatisch ein vom System zugewiesener freier Port verwendet. Mit der Umgebungsvariable `RENPYTOOLS_PORT` kannst du einen anderen Port festlegen.

## Sicherheitsmechanismen im Überblick

| Mechanismus | Beschreibung |
|---|---|
| Arbeitskopie | Standardmäßig wird erst in eine Kopie kopiert und dann dort gearbeitet – kein einziges Byte des Originals wird angefasst |
| Erzwungene Sicherung | Ist "Originaldateien direkt ändern" angekreuzt, wird zuerst ein komplettes Backup-Archiv (inkl. Spielstände) erstellt |
| Nicht kleiner – nicht ersetzen | Jeder Optimierer schreibt zuerst eine temporäre Datei und ersetzt erst, wenn bestätigt ist, dass die Größe geschrumpft ist |
| Verweis-Gating | Ressourcen ohne wörtlichen Verweis in den Skripten werden nur an Ort und Stelle komprimiert und niemals umbenannt |
| Engine-Verzeichnisschutz | Im Modus für fertige Spiele/APK werden renpy/, lib/, assets/x-renpy/ niemals angefasst |
| Nur markieren, nicht löschen | Dateien, die vermutlich unverwiesen sind, landen standardmäßig nur im Bericht; ist die Option aktiviert, werden sie in einen Quarantänebereich verschoben |
| Müllbeseitigung löscht nur neu erzeugbare Dinge | Caches/Logs/Bytecode; im Modus "Original direkt ändern" werden sie automatisch übersprungen, um Spielstände zu schützen |
| Bilder werden nie für tot erklärt | Ren'Py lädt Bilder automatisch nach Dateinamen – kein gefundener Verweis heißt nicht automatisch unbenutzt |
| Schutz vor bösartigen Eingaben | Deserialisierung von Archiv-Indizes per Whitelist; Pfad-Bereinigung bei Archiveinträgen (Schutz vor zip-slip) |
| Nur für den lokalen Rechner | Der Dienst hört nur auf 127.0.0.1 und prüft die Herkunft der Anfragen – aus dem Netz ist er nicht erreichbar |
| Automatisches lint nach der Optimierung | Der offizielle statische Check ist in den Ablauf eingebaut und speichert validation.txt ins Archiv |
| Änderungsliste | Jeder Durchlauf schreibt eine changelog.json, die jede einzelne Änderung festhält |

## Sicherheitsgrenzen

- Der Dienst **lauscht nur auf 127.0.0.1** (eine Adresse, die "nur für diesen Rechner" gilt): Andere Geräte im lokalen Netz
  oder im Internet können schlicht keine Verbindung aufbauen. Eine Firewall-Konfiguration ist nicht nötig,
  und es wird davon abgeraten, den Dienst in irgendeiner Weise nach außen freizugeben;
- Das Werkzeug bietet keine Option an, "Netzwerkzugriff zu öffnen", und plant dies auch nicht. Falls du den Quellcode selbst änderst,
  **raten wir dringend davon ab**, die Lausch-Adresse auf 0.0.0.0 oder eine öffentliche Adresse zu ändern –
  die Oberfläche hat keine Anmeldung/Authentifizierung, und sie freizugeben heißt, die Lese- und Schreibrechte auf deine lokalen Dateien an jeden weiterzureichen, der sie erreichen kann;
- Das Werkzeug greift von sich aus nicht auf das Internet zu. Die einzige Ausnahme ist die "Suche nach neuen Versionen" (Vergleich mit GitHub Releases;
  schlägt sie fehl, wird sie still übersprungen und beeinflusst keine Funktion).

## Tests

```
.venv\Scripts\python -m pytest tests -q
```

Abgedeckt werden: Lesen/Schreiben von RPA-Archiven (inklusive beider Formate – alt und neu – und Abfangen bösartiger Archive),
Sicherheit des Verweis-Umschreibens, dass Schrift-/Bildoptimierung die Originaldateien nicht beschädigt, rpyc-Parsing,
APK-Abspeckung (Engine-Schutz/Signaturentfernung/x-Präfix-Pfadumsetzung/Schlüsselerzeugung), Abbruch und Crash-Dumps,
sichere Standardwerte, Regressionstests für Audit-Reparaturen, lokale Schutzmechanismen des Backends und die Vollständigkeit
der acht Sprachwörterbücher der Oberfläche – insgesamt 114 Tests.

## Entwicklung

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
python main.py            # 启动图形界面
build_exe.bat             # 重新打包 exe
```

**Bitte zuerst lesen, für Maintainer/Agenten:**

- [docs/ARCHITECTURE.md](ARCHITECTURE.md): Architektur-Blaupause, Sicherheits-Grundlinien, Erweiterungsleitfaden
- [docs/BACKLOG.md](BACKLOG.md): Archiv der Anforderungen und offene Aufgaben (neue Wünsche zuerst hier eintragen)
- [docs/STATUS.md](STATUS.md): Übergabestatus und Praxistest-Ergebnisse

## Mehrsprachigkeit / Localization

| Sprache | Oberfläche | Dokumentation | Status |
|---|---|---|---|
| 简体中文 | ✅ Standard | ✅ dieses Dokument | Veröffentlicht |
| English | ✅ | [README.en.md](README.en.md) | Veröffentlicht |
| Русский | ✅ | [README.ru.md](README.ru.md) | Veröffentlicht |
| Español | ✅ | [README.es.md](README.es.md) | Veröffentlicht |
| Português (BR) | ✅ | [README.pt.md](README.pt.md) | Veröffentlicht |
| Türkçe | ✅ | [README.tr.md](README.tr.md) | Veröffentlicht |
| Deutsch | ✅ | ✅ dieses Dokument | Veröffentlicht |
| Français | ✅ | [README.fr.md](README.fr.md) | Veröffentlicht |

Du möchtest eine neue Sprache hinzufügen? Siehe den Abschnitt "Übersetzungsleitfaden" in [CONTRIBUTING.md](CONTRIBUTING.md) –
der Oberfläche ein Wörterbuch hinzufügen und der Dokumentation eine README.<Sprachcode>.md-Datei, das reicht.

## Lizenz & Compliance

- Dieses Projekt wird unter **AGPL-3.0** veröffentlicht: Du darfst die Software frei nutzen, ändern und weitergeben,
  aber geänderte Versionen (auch wenn sie als Dienst über ein Netz angeboten werden) müssen unter derselben Lizenz quelloffen sein.
  Dein eigenes Spiel privat abzuspecken unterliegt keinerlei Einschränkungen; erst wenn du eine geänderte Version weitergibst, greift die Open-Source-Pflicht.
- Die vollständigen Hinweise zu Drittanbieter-Abhängigkeiten und Referenzimplementierungen von Dateiformaten:
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
  (enthält die LGPL-Compliance-Hinweise zu pystray, die Danksagungen zum Ren'Py-Format und die Abgrenzung zu externen Programmen)
- Wer mitwirken möchte, liest bitte zuerst [CONTRIBUTING.md](CONTRIBUTING.md);
  Sicherheitslücken bitte über den privaten Meldeweg in [SECURITY.md](SECURITY.md) melden.
- Ren'Py ist eine eingetragene Marke/ein Projekt von Tom Rothamel und anderen; dieses Projekt steht in keiner Verbindung dazu –
  es ist ein unabhängiges Drittanbieter-Werkzeug, das für die Ren'Py-Community entwickelt wurde.
