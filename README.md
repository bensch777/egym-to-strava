# 🏋️ EGYM to Strava Sync

Dieses Skript veredelt deine Strava-Krafttrainingseinheiten mit den detaillierten Daten deiner EGYM-Workouts. Es fügt Übungen, Gewichte und Wiederholungen automatisch in die Beschreibung ein, sobald du dein Training beendet hast.

<img width="579" height="308" alt="screenshot" src="https://github.com/user-attachments/assets/310f301f-837b-4011-a5c8-8ec4065747c1" />

## 💡 Das Prinzip
Dieses Skript erstellt **keine neuen Aktivitäten**. Stattdessen funktioniert es so:
1. Du startest wie gewohnt ein **Krafttraining** auf deiner Sportuhr (Apple Watch, Garmin, etc.) oder direkt in der Strava App.
2. Du absolvierst dein EGYM-Training.
3. Das Skript erkennt die Übereinstimmung (Datum & Typ), benennt die Aktivität in Strava um (z.B. "EGYM Zirkel") und schreibt alle Sätze und Gewichte in die Beschreibung.

## 🚀 Features
* **Automatisches Update:** Läuft via GitHub Actions (z.B. alle 3 Stunden).
* **Smart Detection:** Vergleicht Zeitstempel und verhindert Dopplungen durch Titel-Prüfung.
* **Vollständige Details:** Schreibt Übungsnamen, Sätze, Wiederholungen und Gewichte in die Strava-Beschreibung.
* **Cloud-Native:** Keine lokale Installation nötig, nutzt GitHub Secrets für maximale Sicherheit.

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
3. **Abgleich:** Lädt die letzten Aktivitäten von Strava. Wenn ein "WeightTraining" am selben Tag gefunden wird, das noch **nicht** "EGYM" im Titel hat, wird es aktualisiert.
4. **Persistenz:** Falls Strava einen neuen Refresh-Token ausgibt, wird dieser automatisch via GitHub API in den Repository-Secrets gespeichert.
