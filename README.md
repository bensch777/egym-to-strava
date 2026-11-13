# 🏋️ EGYM to Strava Sync

Dieses Skript bringt deine EGYM-Workouts nach Strava. Hast du das Training schon mit der Uhr aufgezeichnet, ergänzt es Übungen, Gewichte und Wiederholungen in der Beschreibung. Gibt es noch keine Aktivität, lädt es das Workout als echtes Krafttraining mit strukturierten Sätzen hoch.

<img width="579" height="308" alt="screenshot" src="https://github.com/user-attachments/assets/310f301f-837b-4011-a5c8-8ec4065747c1" />

## 💡 Das Prinzip
Pro EGYM-Workout prüft das Skript, ob es am selben Tag schon ein **Krafttraining** in Strava gibt:

1. **Aktivität vorhanden** (z.B. von Apple Watch oder Garmin): Die Aktivität wird umbenannt (z.B. "EGYM Zirkel") und alle Sätze und Gewichte landen in der Beschreibung. Puls und Kalorien der Uhr bleiben erhalten.
2. **Keine Aktivität vorhanden:** Das Workout wird über Stravas [JSON-Format für Krafttraining](https://developers.strava.com/docs/uploads/) als neue Aktivität hochgeladen. Strava zeigt dann Übungen, Sätze, Wiederholungen und Gewichte strukturiert an.
3. **Bereits synchronisiert** ("EGYM" im Titel): Es passiert nichts.

## 🚀 Features
* **Automatischer Sync:** Läuft täglich via GitHub Actions.
* **Strukturierte Sätze:** Neue Aktivitäten enthalten echte Satzdaten statt nur Text.
* **Keine Dopplungen:** Vorhandene Uhr-Aktivitäten werden ergänzt statt dupliziert.
* **Garmin-Export:** Für jedes neu hochgeladene Workout entsteht eine FIT-Datei mit Sätzen und Pausen zum Import in Garmin Connect.
* **Testlauf:** Der Workflow lässt sich manuell mit `dry_run` starten und zeigt dann nur an, was er tun würde.
* **Cloud-Native:** Keine lokale Installation nötig, nutzt GitHub Secrets für maximale Sicherheit.

## ⌚ Training in Garmin Connect importieren
Garmin bietet keine offizielle Upload-Schnittstelle, der Import läuft deshalb von Hand:

1. Öffne in GitHub unter **Actions** den letzten Lauf von "EGYM to Strava Sync".
2. Lade unten unter **Artifacts** die Datei `garmin-fit` herunter und entpacke sie.
3. Importiere die `.fit`-Datei in [Garmin Connect](https://connect.garmin.com/modern/import-data) über "Daten importieren".

Die Dateien bleiben 30 Tage abrufbar. Ein Testlauf mit `dry_run` erzeugt die FIT-Dateien für alle Workouts im Zeitraum erneut. Die Zuordnung der Geräte zu Garmins Übungsliste steht in `GARMIN_MAP` in `main.py`.

## ⚠️ Gut zu wissen
* **Reihenfolge:** Lädt deine Uhr das Training erst nach dem Sync zu Strava hoch, entstehen zwei Aktivitäten. Der Sync läuft deshalb erst am nächsten Morgen.
* **Zeiten und Kalorien:** Start, Dauer und Kalorien werden aus den EGYM-Daten der einzelnen Geräte berechnet. Pulsdaten liefert EGYM nicht.
* **Übungs-Mapping:** Strava kennt nur eine feste Liste von Übungen. Die Zuordnung der EGYM-Geräte steht in `EXERCISE_MAP` in `main.py`. Nicht zugeordnete Geräte erscheinen nur in der Beschreibung und werden im Log gemeldet.
* **Zeitraum:** Es werden nur Workouts der letzten 7 Tage synchronisiert (`LOOKBACK_DAYS`). Die Zeitzone lässt sich über `TIMEZONE` setzen (Standard: `Europe/Berlin`).

---

## 🛠 Setup-Anleitung

### 1. Strava API Zugang einrichten
1. Gehe zu [strava.com/settings/api](https://www.strava.com/settings/api) und erstelle eine App.
2. Notiere dir deine **Client ID** und dein **Client Secret**.
3. **Initialen Code generieren:** Ersetze `DEINE_CLIENT_ID` im folgenden Link und öffne ihn im Browser:
   `https://www.strava.com/oauth/authorize?client_id=DEINE_CLIENT_ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all,activity:write`
4. Autorisiere die App und kopiere den `code` aus der URL der Folgeseite (z. B. `http://localhost/?state=&code=abc12345...`).
5. **Initialen Refresh-Token generieren:** Tausche den Code im Terminal gegen den ersten Token-Satz ein:
   ```bash
   curl -X POST https://www.strava.com/oauth/token \
     -F client_id=DEINE_CLIENT_ID \
     -F client_secret=DEIN_CLIENT_SECRET \
     -F code=DEIN_CODE_AUS_DER_URL \
     -F grant_type=authorization_code
     
6. Kopiere den `refresh_token` aus der JSON-Antwort für die GitHub Secrets.

### 2. GitHub Personal Access Token (PAT)
Damit das Skript den rotierenden Refresh-Token selbstständig aktualisieren kann, benötigt es Schreibrechte auf die Repository-Secrets:
1. Gehe zu **Settings -> Developer Settings -> Personal access tokens -> Tokens (classic)**.
2. Generiere einen neuen Token mit dem Scope **`repo`**.
3. Kopiere diesen Token als `GH_PAT`.

### 3. GitHub Secrets konfigurieren
Gehe in deinem Repository zu **Settings -> Secrets and variables -> Actions** und lege folgende Secrets an:

| Secret Name | Wert / Beschreibung |
| :--- | :--- |
| `EGYM_TENANT` | Dein eGym Anbieter Subdomain (meist `benefit` oder `mcfit`) |
| `EGYM_EMAIL` | Deine EGYM Login-E-Mail |
| `EGYM_PASSWORD` | Dein EGYM Passwort |
| `STRAVA_CLIENT_ID` | Deine Strava Client ID |
| `STRAVA_CLIENT_SECRET` | Deine Strava Client Secret |
| `STRAVA_REFRESH_TOKEN` | Der in Schritt 1 generierte Refresh-Token |
| `GH_PAT` | Dein GitHub Personal Access Token (aus Schritt 2) |

---

## 📂 Projektstruktur
* `main.py`: Das Python-Skript für den Datentransfer und die Token-Verwaltung.
* `.github/workflows/sync.yml`: Definiert den Zeitplan und die Umgebung für GitHub Actions.
* `requirements.txt`: Enthält die benötigten Python-Bibliotheken.

---

## 🔄 Funktionsweise
1. **Authentifizierung:** Das Skript nutzt den `STRAVA_REFRESH_TOKEN`, um einen kurzlebigen Access-Token zu generieren.
2. **EGYM-Daten:** Loggt sich bei EGYM ein, identifiziert automatisch die `club_uuid` und lädt die Workouts der letzten Wochen.
3. **Abgleich:** Lädt die Strava-Aktivitäten der letzten Tage. Ein "WeightTraining" am selben Tag ohne "EGYM" im Titel wird aktualisiert. Gibt es keines, wird das Workout als JSON-Datei hochgeladen.
4. **Persistenz:** Falls Strava einen neuen Refresh-Token ausgibt, wird dieser automatisch via GitHub API in den Repository-Secrets gespeichert.
