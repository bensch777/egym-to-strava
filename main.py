import io
import json
import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from github import Github

# Umgebungsvariablen laden
load_dotenv()

TENANT = os.getenv('EGYM_TENANT', 'benefit')
EGYM_EMAIL = os.getenv('EGYM_EMAIL')
EGYM_PASSWORD = os.getenv('EGYM_PASSWORD')
STRAVA_CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
STRAVA_REFRESH_TOKEN = os.getenv('STRAVA_REFRESH_TOKEN')
GH_PAT = os.getenv('GH_PAT')
REPO_NAME = os.getenv('GITHUB_REPOSITORY')

# Zeitzone für Tagesabgleich und utc_offset im Strava-Upload
LOCAL_TZ = ZoneInfo(os.getenv('TIMEZONE') or 'Europe/Berlin')
# Nur Workouts der letzten N Tage werden synchronisiert (verhindert Massen-Backfill)
LOOKBACK_DAYS = int(os.getenv('LOOKBACK_DAYS') or 7)
# DRY_RUN=1: nichts schreiben, stattdessen ausgeben, was passieren würde
DRY_RUN = (os.getenv('DRY_RUN') or '').lower() in ('1', 'true', 'yes')
# Geschätzte Dauer pro Satz, falls EGYM keine Workout-Dauer liefert
SECONDS_PER_SET = 75

STRAVA_API = "https://www.strava.com/api/v3"

# EGYM-Gerätename (Stichwort, klein geschrieben) -> Strava exercise_type.
# Reihenfolge zählt: der erste Treffer gewinnt, spezifische Begriffe stehen oben.
# Gültige Werte: https://developers.strava.com/docs/uploads/ ("Supported Exercises")
EXERCISE_MAP = [
    ("reverse butterfly", "MACHINE_REAR_DELT_REVERSE_FLY"),
    ("butterfly reverse", "MACHINE_REAR_DELT_REVERSE_FLY"),
    ("butterfly", "PEC_DECK_BUTTERFLY"),
    ("beinpresse", "MACHINE_LEG_PRESS"),
    ("leg press", "MACHINE_LEG_PRESS"),
    ("beinstrecker", "MACHINE_LEG_EXTENSION"),
    ("leg extension", "MACHINE_LEG_EXTENSION"),
    ("beinbeuger", "MACHINE_LEG_CURL_SEATED"),
    ("leg curl", "MACHINE_LEG_CURL_SEATED"),
    ("brustpresse", "MACHINE_CHEST_PRESS"),
    ("chest press", "MACHINE_CHEST_PRESS"),
    ("latzug", "LAT_PULLDOWN"),
    ("lat pull", "LAT_PULLDOWN"),
    ("ruder", "MACHINE_SEATED_ROW"),
    ("seated row", "MACHINE_SEATED_ROW"),
    ("schulterpresse", "MACHINE_SEATED_SHOULDER_PRESS"),
    ("shoulder press", "MACHINE_SEATED_SHOULDER_PRESS"),
    ("bauch", "AB_CRUNCH_MACHINE"),
    ("abdominal", "AB_CRUNCH_MACHINE"),
    ("rückenstrecker", "MACHINE_BACK_EXTENSION"),
    ("rueckenstrecker", "MACHINE_BACK_EXTENSION"),
    ("rückentrainer", "MACHINE_BACK_EXTENSION"),
    ("back extension", "MACHINE_BACK_EXTENSION"),
    ("abdu", "MACHINE_HIP_ABDUCTION"),
    ("addu", "MACHINE_HIP_ADDUCTION"),
    ("bizeps", "MACHINE_BICEP_CURL"),
    ("biceps", "MACHINE_BICEP_CURL"),
    ("bicep", "MACHINE_BICEP_CURL"),
    ("armbeuger", "MACHINE_BICEP_CURL"),
    ("trizeps", "MACHINE_TRICEP_EXTENSION"),
    ("triceps", "MACHINE_TRICEP_EXTENSION"),
    ("tricep", "MACHINE_TRICEP_EXTENSION"),
    ("armstrecker", "MACHINE_TRICEP_EXTENSION"),
    ("dip", "SEATED_DIP_MACHINE"),
    ("glut", "MACHINE_HIP_THRUST"),
    ("hip thrust", "MACHINE_HIP_THRUST"),
    ("seitbeuger", "SIDE_BEND"),
    ("lateral flexion", "SIDE_BEND"),
    ("rotation", "CORE_GENERIC"),
    ("rotary", "CORE_GENERIC"),
    ("kniebeuge", "SQUAT_GENERIC"),
    ("squat", "SQUAT_GENERIC"),
    ("wade", "MACHINE_CALF_PRESS"),
    ("calf", "MACHINE_CALF_PRESS"),
    ("klimmzug", "MACHINE_ASSISTED_PULL_UP"),
    ("pull up", "MACHINE_ASSISTED_PULL_UP"),
    ("pullover", "MACHINE_PULLOVER"),
    ("seitheben", "LATERAL_RAISE_GENERIC"),
    ("lateral raise", "LATERAL_RAISE_GENERIC"),
]

def update_github_secret(new_refresh_token):
    """Aktualisiert den Refresh-Token in den GitHub Secrets."""
    if not GH_PAT or not REPO_NAME:
        return
    try:
        g = Github(GH_PAT)
        repo = g.get_repo(REPO_NAME)
        repo.create_secret("STRAVA_REFRESH_TOKEN", new_refresh_token)
        print("🔐 GitHub Secret 'STRAVA_REFRESH_TOKEN' aktualisiert.")
    except Exception as e:
        print(f"❌ GitHub API Fehler: {e}")

def get_strava_access_token():
    """Holt Access-Token und rotiert bei Bedarf den Refresh-Token."""
    url = "https://www.strava.com/oauth/token"
    payload = {
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': STRAVA_REFRESH_TOKEN,
        'grant_type': 'refresh_token'
    }
    res = requests.post(url, data=payload)
    if res.status_code == 200:
        data = res.json()
        if data.get('refresh_token') != STRAVA_REFRESH_TOKEN:
            update_github_secret(data['refresh_token'])
        return data['access_token']
    return None

def get_egym_workouts():
    """Holt EGYM Daten direkt über die MWA-API."""
    session = requests.Session()
    base_url = f"https://{TENANT}.netpulse.com"
    headers = {'X-NP-APP-Version': '3.71', 'X-NP-API-Version': '1.5', 'User-Agent': 'NetpulseFitness/3.71'}
    try:
        login = session.post(f"{base_url}/np/exerciser/login", data={'username': EGYM_EMAIL, 'password': EGYM_PASSWORD}, headers=headers).json()
        token_res = session.get(f"{base_url}/np/micro-web-app/v1.0/exercisers/{login['uuid']}/tokens/FLS", headers=headers).json()
        
        w_headers = {"Authorization": f"Bearer {token_res['accessToken']}", "X-Exerciser-Id": login['uuid'], "User-Agent": "egym/10.36.0"}
        params = {
            "completedAfter": "2025-12-01T00:00:00Z",
            "completedBefore": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "gymLocationId": login['homeClubUuid'],
            "measurementSystem": "METRIC", "locale": "de-DE"
        }
        res = session.get("https://mwa-api.int.api.egym.com/mwa/api/workouts/v1.0/workouts", headers=w_headers, params=params)
        return res.json()
    except Exception as e:
        print(f"❌ EGYM Fehler: {e}")
        return None

def parse_time(value):
    """Parst einen ISO-Zeitstempel von EGYM; ohne Zeitzone wird UTC angenommen."""
    if not value:
        return None
    text = re.sub(r'\.\d+', '', str(value).strip()).replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

def map_exercise(label):
    """Ordnet einen EGYM-Gerätenamen einem Strava exercise_type zu (oder None)."""
    name = (label or '').lower()
    for keyword, exercise_type in EXERCISE_MAP:
        if keyword in name:
            return exercise_type
    return None

def collect_exercises(workout):
    """Liefert die EGYM-Geräteübungen eines Workouts als (Name, Sätze)."""
    source = list(workout.get('exercises') or [])
    if not source:
        for group in workout.get('exerciseGroups') or []:
            source.extend(group.get('exercises') or [])
    result = []
    for ex in source:
        if (ex.get('activity') or {}).get('category') != "EGYM_MACHINE":
            continue
        sets = [s for s in ex.get('sets') or [] if s.get('weight')]
        if sets:
            result.append(((ex.get('label') or '').replace('EGYM ', '').strip(), sets))
    return result

def build_title(count):
    return f"🏋️ EGYM Zirkel ({count} Übungen)" if count >= 12 else f"💪 EGYM Krafttraining ({count} Übungen)"

def build_description(exercises):
    return "\n".join(
        f"🔹 {name}: " + " | ".join(f"{s.get('weight')}kg x {s.get('numberOfReps')}" for s in sets)
        for name, sets in exercises
    )

def build_strava_json(workout, exercises, completed):
    """Baut die Strava-Strength-JSON-Datei. Gibt (payload, nicht zugeordnete Übungen) zurück."""
    sets, unmapped = [], []
    for name, ex_sets in exercises:
        exercise_type = map_exercise(name)
        if not exercise_type:
            unmapped.append(name)
            continue
        for s in ex_sets:
            entry = {"exercise_type": exercise_type, "weight": float(s['weight'])}
            if s.get('numberOfReps'):
                entry["repetitions"] = int(s['numberOfReps'])
            sets.append(entry)
    if not sets:
        return None, unmapped

    # Startzeit: von EGYM, falls vorhanden – sonst aus der Satzanzahl geschätzt
    started = None
    for key in ('startedAt', 'startTime', 'createdAt'):
        started = parse_time(workout.get(key))
        if started:
            break
    if started and timedelta(minutes=1) <= completed - started <= timedelta(hours=4):
        elapsed = int((completed - started).total_seconds())
    else:
        elapsed = max(60, sum(len(ex_sets) for _, ex_sets in exercises) * SECONDS_PER_SET)
        started = completed - timedelta(seconds=elapsed)
        print(f"ℹ️ Keine Workout-Dauer von EGYM, geschätzt: {elapsed // 60} min.")

    payload = {
        "version": "1.0",
        "start_time": started.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "utc_offset": int(started.astimezone(LOCAL_TZ).utcoffset().total_seconds()),
        "elapsed_time": elapsed,
        "creator": {"name": "EGYM"},
        "sets": sets,
    }
    return payload, unmapped

def get_strava_activities(headers):
    """Lädt alle Strava-Aktivitäten im Sync-Zeitraum."""
    after = int((datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS + 1)).timestamp())
    activities, page = [], 1
    while True:
        res = requests.get(f"{STRAVA_API}/athlete/activities", headers=headers,
                           params={"after": after, "per_page": 100, "page": page})
        res.raise_for_status()
        batch = res.json()
        activities.extend(batch)
        if len(batch) < 100:
            return activities
        page += 1

def upload_to_strava(payload, title, description, external_id, headers):
    """Lädt die JSON-Datei hoch und wartet auf die Verarbeitung. Gibt die Activity-ID zurück."""
    res = requests.post(
        f"{STRAVA_API}/uploads", headers=headers,
        data={"data_type": "json", "sport_type": "WeightTraining", "name": title,
              "description": description, "external_id": external_id},
        files={"file": (f"{external_id}.json", io.BytesIO(json.dumps(payload).encode('utf-8')), "application/json")},
    )
    status = res.json() if res.content else {}
    if res.status_code != 201:
        print(f"❌ Strava Upload abgelehnt ({res.status_code}): {status.get('error') or res.text}")
        return None
    for _ in range(30):
        if status.get('error'):
            print(f"❌ Strava Upload Fehler: {status['error']}")
            return None
        if status.get('activity_id'):
            return status['activity_id']
        time.sleep(2)
        status = requests.get(f"{STRAVA_API}/uploads/{status['id']}", headers=headers).json()
    print("⚠️ Strava verarbeitet den Upload noch, Ergebnis unbekannt.")
    return None

def sync_to_strava(workouts, strava_token):
    """Synchronisiert EGYM-Daten zu Strava.

    Gibt es am Tag des Workouts schon ein Krafttraining in Strava (z.B. von der Uhr),
    werden Titel und Beschreibung ergänzt. Sonst wird das Workout als neue Aktivität
    mit strukturierten Sätzen hochgeladen.
    """
    if not workouts: return
    headers = {"Authorization": f"Bearer {strava_token}"}
    strava_acts = get_strava_activities(headers)
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    for workout in workouts:
        if workout.get('source') == 'GARMIN': continue
        completed = parse_time(workout.get('completedAt'))
        if not completed or completed < cutoff: continue
        date_str = completed.astimezone(LOCAL_TZ).strftime('%Y-%m-%d')

        exercises = collect_exercises(workout)
        if not exercises: continue
        title = build_title(len(exercises))
        description = build_description(exercises)

        same_day = [a for a in strava_acts
                    if a['start_date_local'].split('T')[0] == date_str and a['type'] == 'WeightTraining']
        if any("EGYM" in a['name'] for a in same_day):
            print(f"⏭️ {date_str}: Bereits als EGYM-Workout in Strava.")
            continue

        # Fallback: vorhandene Aktivität (z.B. von der Uhr) nur umbenennen und beschreiben
        if same_day:
            if DRY_RUN:
                print(f"🧪 {date_str}: Würde Aktivität {same_day[0]['id']} aktualisieren: {title}\n{description}")
                continue
            res = requests.put(f"{STRAVA_API}/activities/{same_day[0]['id']}",
                               headers=headers, json={"name": title, "description": description})
            if res.ok:
                print(f"✅ {date_str}: Strava Titel und Beschreibung aktualisiert.")
            else:
                print(f"❌ {date_str}: Strava Update fehlgeschlagen ({res.status_code}): {res.text}")
            continue

        # Kein Krafttraining an dem Tag: als neue Aktivität mit Sätzen hochladen
        payload, unmapped = build_strava_json(workout, exercises, completed)
        if unmapped:
            print(f"⚠️ {date_str}: Kein Strava-Übungstyp für {', '.join(unmapped)} "
                  f"(nur in der Beschreibung, bitte EXERCISE_MAP ergänzen).")
        if not payload:
            print(f"❌ {date_str}: Keine Übung zuordenbar, Upload übersprungen.")
            continue
        external_id = f"egym-{workout.get('id') or completed.strftime('%Y%m%dT%H%M%S')}"
        if DRY_RUN:
            print(f"🧪 {date_str}: Würde hochladen: {title}\n"
                  f"Workout-Felder von EGYM: {sorted(workout.keys())}\n"
                  f"{json.dumps(payload, indent=2, ensure_ascii=False)}")
            continue
        activity_id = upload_to_strava(payload, title, description, external_id, headers)
        if activity_id:
            print(f"✅ {date_str}: Neue Strava-Aktivität mit {len(payload['sets'])} Sätzen angelegt "
                  f"(https://www.strava.com/activities/{activity_id}).")

if __name__ == "__main__":
    s_token = get_strava_access_token()
    if s_token:
        e_workouts = get_egym_workouts()
        if e_workouts: sync_to_strava(e_workouts, s_token)
