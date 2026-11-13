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
from garmin_fit_sdk import Encoder, Profile

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
# Angenommene Dauer pro Satz, falls EGYM keine Satzdauer liefert
SECONDS_PER_SET = 75

# Ordner für FIT-Dateien zum manuellen Import in Garmin Connect
FIT_DIR = os.getenv('FIT_DIR') or 'fit_export'

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
    ("rotator", "CORE_GENERIC"),
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
            "completedBefore": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "gymLocationId": login['homeClubUuid'],
            "measurementSystem": "METRIC", "locale": "de-DE"
        }
        res = session.get("https://mwa-api.int.api.egym.com/mwa/api/workouts/v1.0/workouts", headers=w_headers, params=params)
        return res.json()
    except Exception as e:
        print(f"❌ EGYM Fehler: {e}")
        return None

# Strava exercise_type -> Garmin (Kategorie, Übung) aus dem FIT-Profil.
# Garmin kennt kaum Maschinen, daher jeweils die nächstliegende Übung.
# Übung None: nur die Kategorie wird gesetzt.
GARMIN_MAP = {
    "MACHINE_LEG_PRESS": ("squat", "leg_press"),
    "MACHINE_LEG_CURL_SEATED": ("leg_curl", "leg_curl"),
    "MACHINE_LEG_EXTENSION": ("crunch", "leg_extensions"),
    "MACHINE_SEATED_ROW": ("row", "seated_cable_row"),
    "MACHINE_HIP_THRUST": ("hip_raise", "barbell_hip_thrust_with_bench"),
    "LAT_PULLDOWN": ("pull_up", "lat_pulldown"),
    "MACHINE_BICEP_CURL": ("curl", "cable_biceps_curl"),
    "MACHINE_SEATED_SHOULDER_PRESS": ("shoulder_press", "smith_machine_overhead_press"),
    "MACHINE_CHEST_PRESS": ("bench_press", "smith_machine_bench_press"),
    "MACHINE_BACK_EXTENSION": ("hyperextension", "weighted_static_back_extension"),
    "AB_CRUNCH_MACHINE": ("crunch", "weighted_crunch"),
    "MACHINE_HIP_ABDUCTION": ("hip_stability", "weighted_standing_hip_abduction"),
    "MACHINE_HIP_ADDUCTION": ("hip_stability", "weighted_standing_adduction"),
    "PEC_DECK_BUTTERFLY": ("flye", "cable_crossover"),
    "MACHINE_REAR_DELT_REVERSE_FLY": ("flye", "incline_reverse_flye"),
    "CORE_GENERIC": ("core", None),
}

def parse_time(value, tz=None):
    """Parst einen EGYM-Zeitstempel. EGYM liefert Ortszeit ohne Zeitzonenangabe."""
    if not value:
        return None
    text = re.sub(r'\.\d+', '', str(value).strip()).replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz or LOCAL_TZ)

def workout_tz(workout):
    """Zeitzone des Workouts laut EGYM, sonst die konfigurierte."""
    try:
        return ZoneInfo(workout.get('timezone') or '')
    except Exception:
        return LOCAL_TZ

def map_exercise(label):
    """Ordnet einen EGYM-Gerätenamen einem Strava exercise_type zu (oder None)."""
    name = (label or '').lower()
    for keyword, exercise_type in EXERCISE_MAP:
        if keyword in name:
            return exercise_type
    return None

def collect_exercises(workout):
    """Liefert die EGYM-Geräteübungen eines Workouts in zeitlicher Reihenfolge."""
    tz = workout_tz(workout)
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
            result.append({
                "name": (ex.get('label') or '').replace('EGYM ', '').strip(),
                "sets": sets,
                "completed": parse_time(ex.get('completedAt'), tz),
                "calories": ex.get('kiloCalories') or 0,
            })
    if all(ex["completed"] for ex in result):
        result.sort(key=lambda ex: ex["completed"])
    return result

def build_title(count):
    return f"🏋️ EGYM Zirkel ({count} Übungen)" if count >= 12 else f"💪 EGYM Krafttraining ({count} Übungen)"

def build_description(exercises):
    return "\n".join(
        f"🔹 {ex['name']}: " + " | ".join(f"{s.get('weight')}kg x {s.get('numberOfReps')}" for s in ex['sets'])
        for ex in exercises
    )

def utc_iso(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def timed_sets(exercises):
    """Liefert je Satz (Übung, Satz, Beginn, Dauer); Beginn rückwärts aus Endzeit und Satzdauer."""
    result = []
    for ex in exercises:
        rows, end = [], ex["completed"]
        for s in reversed(ex["sets"]):
            duration = s.get('duration') or SECONDS_PER_SET
            end = end - timedelta(seconds=duration) if end else None
            rows.insert(0, (ex, s, end, duration))
        result.extend(rows)
    return result

def workout_start(exercises, completed):
    starts = [start for _, _, start, _ in timed_sets(exercises) if start]
    if starts:
        return min(starts)
    return completed - timedelta(seconds=sum(len(ex["sets"]) for ex in exercises) * SECONDS_PER_SET)

def total_calories(exercises):
    return int(round(sum(ex["calories"] for ex in exercises)))

def build_strava_json(exercises, completed):
    """Baut die Strava-Strength-JSON-Datei. Gibt (payload, nicht zugeordnete Übungen) zurück."""
    sets, unmapped = [], []
    for ex, s, set_start, _ in timed_sets(exercises):
        exercise_type = map_exercise(ex["name"])
        if not exercise_type:
            if ex["name"] not in unmapped:
                unmapped.append(ex["name"])
            continue
        entry = {"exercise_type": exercise_type, "weight": float(s['weight'])}
        if s.get('numberOfReps'):
            entry["repetitions"] = int(s['numberOfReps'])
        if set_start:
            entry["start_time"] = utc_iso(set_start)
        sets.append(entry)
    if not sets:
        return None, unmapped

    started = workout_start(exercises, completed)
    payload = {
        "version": "1.0",
        "start_time": utc_iso(started),
        "utc_offset": int(started.utcoffset().total_seconds()),
        "elapsed_time": max(60, int((completed - started).total_seconds())),
        "creator": {"name": "EGYM"},
        "sets": sets,
    }
    active = sum(s.get('duration') or 0 for ex in exercises for s in ex["sets"])
    if active:
        payload["active_time"] = min(int(active), payload["elapsed_time"])
    if total_calories(exercises):
        payload["total_calories"] = total_calories(exercises)
    return payload, unmapped

def garmin_exercise(name):
    """Garmin-Kategorie und -Übung (als FIT-Profilwerte) für einen EGYM-Gerätenamen."""
    types = Profile['types']
    category, exercise = GARMIN_MAP.get(map_exercise(name), ("unknown", None))
    category_id = {v: k for k, v in types['exercise_category'].items()}[category]
    if not exercise:
        return category_id, None
    return category_id, {v: k for k, v in types[f'{category}_exercise_name'].items()}[exercise]

def build_fit(exercises, completed):
    """Baut eine FIT-Aktivitätsdatei (Krafttraining mit Sätzen und Pausen) für Garmin Connect."""
    num = Profile['mesg_num']
    started = workout_start(exercises, completed)
    elapsed = max(60, int((completed - started).total_seconds()))
    encoder = Encoder()
    encoder.on_mesg(num['FILE_ID'], {'type': 'activity', 'manufacturer': 'development', 'product': 0,
                                     'serial_number': int(started.timestamp()) % 2**32, 'time_created': started})
    encoder.on_mesg(num['EVENT'], {'timestamp': started, 'event': 'timer', 'event_type': 'start'})
    index, previous_end = 0, started
    for ex, s, set_start, duration in timed_sets(exercises):
        set_start = set_start or previous_end
        if set_start > previous_end:  # Pause zwischen zwei Sätzen
            encoder.on_mesg(num['SET'], {'timestamp': set_start, 'start_time': previous_end, 'set_type': 'rest',
                                         'duration': (set_start - previous_end).total_seconds(),
                                         'message_index': index})
            index += 1
        category, exercise = garmin_exercise(ex["name"])
        mesg = {'timestamp': set_start + timedelta(seconds=duration), 'start_time': set_start,
                'set_type': 'active', 'duration': duration, 'weight': float(s['weight']),
                'weight_display_unit': 'kilogram', 'category': [category], 'message_index': index}
        if exercise is not None:
            mesg['category_subtype'] = [exercise]
        if s.get('numberOfReps'):
            mesg['repetitions'] = int(s['numberOfReps'])
        encoder.on_mesg(num['SET'], mesg)
        index += 1
        previous_end = mesg['timestamp']
    encoder.on_mesg(num['EVENT'], {'timestamp': completed, 'event': 'timer', 'event_type': 'stop_all'})
    summary = {'timestamp': completed, 'start_time': started, 'total_elapsed_time': elapsed,
               'total_timer_time': elapsed, 'total_calories': total_calories(exercises),
               'sport': 'training', 'sub_sport': 'strength_training', 'message_index': 0}
    encoder.on_mesg(num['LAP'], {**summary, 'event': 'lap', 'event_type': 'stop'})
    encoder.on_mesg(num['SESSION'], {**summary, 'event': 'session', 'event_type': 'stop',
                                     'first_lap_index': 0, 'num_laps': 1, 'trigger': 'activity_end'})
    fit_epoch = datetime(1989, 12, 31, tzinfo=timezone.utc)
    encoder.on_mesg(num['ACTIVITY'], {
        'timestamp': completed, 'total_timer_time': elapsed, 'num_sessions': 1, 'type': 'manual',
        'event': 'activity', 'event_type': 'stop',
        'local_timestamp': int((completed - fit_epoch).total_seconds() + completed.utcoffset().total_seconds())})
    return encoder.close()

def write_fit(exercises, completed, date_str):
    """Legt die FIT-Datei zum manuellen Import in Garmin Connect ab."""
    try:
        os.makedirs(FIT_DIR, exist_ok=True)
        path = os.path.join(FIT_DIR, f"egym-{completed.strftime('%Y%m%dT%H%M%S')}.fit")
        with open(path, 'wb') as handle:
            handle.write(build_fit(exercises, completed))
        print(f"📁 {date_str}: FIT-Datei für Garmin Connect erstellt: {path}")
    except Exception as e:
        print(f"❌ {date_str}: FIT-Datei konnte nicht erstellt werden: {e}")

def dump_egym_devices(workouts):
    """Gibt im Testlauf alle EGYM-Geräte mit ihrer Strava-Zuordnung aus."""
    devices = {}
    for w in workouts:
        if not isinstance(w, dict):
            continue
        for ex in w.get('exercises') or []:
            act = ex.get('activity') or {}
            if act.get('category') == "EGYM_MACHINE":
                name = (ex.get('label') or '').replace('EGYM ', '').strip()
                devices[name] = (map_exercise(name), (act.get('name') or {}).get('en'), act.get('muscleMapping'))
    print(f"🧪 EGYM lieferte {len(workouts)} Workouts mit {len(devices)} verschiedenen Geräten:")
    for name, (exercise_type, english, muscles) in sorted(devices.items()):
        print(f"   {name} -> {exercise_type} (EGYM: {english}, Muskeln: {json.dumps(muscles, ensure_ascii=False)})")

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
    if DRY_RUN: dump_egym_devices(workouts)
    headers = {"Authorization": f"Bearer {strava_token}"}
    strava_acts = get_strava_activities(headers)
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    for workout in workouts:
        exercises = collect_exercises(workout)
        if not exercises: continue
        completed = parse_time(workout.get('completedAt'), workout_tz(workout))
        if not completed or completed < cutoff: continue
        date_str = completed.strftime('%Y-%m-%d')

        title = build_title(len(exercises))
        description = build_description(exercises)
        payload, unmapped = build_strava_json(exercises, completed)
        if DRY_RUN:
            print(f"🧪 {date_str}: {title}\n{json.dumps(payload, indent=1, ensure_ascii=False)}")
            write_fit(exercises, completed, date_str)

        same_day = [a for a in strava_acts
                    if a['start_date_local'].split('T')[0] == date_str and a['type'] == 'WeightTraining']
        if any("EGYM" in a['name'] for a in same_day):
            print(f"⏭️ {date_str}: Bereits als EGYM-Workout in Strava.")
            continue

        # Fallback: vorhandene Aktivität (z.B. von der Uhr) nur umbenennen und beschreiben
        if same_day:
            if DRY_RUN:
                print(f"🧪 {date_str}: Würde Aktivität {same_day[0]['id']} umbenennen und beschreiben.")
                continue
            res = requests.put(f"{STRAVA_API}/activities/{same_day[0]['id']}",
                               headers=headers, json={"name": title, "description": description})
            if res.ok:
                print(f"✅ {date_str}: Strava Titel und Beschreibung aktualisiert.")
            else:
                print(f"❌ {date_str}: Strava Update fehlgeschlagen ({res.status_code}): {res.text}")
            continue

        # Kein Krafttraining an dem Tag: als neue Aktivität mit Sätzen hochladen
        if unmapped:
            print(f"⚠️ {date_str}: Kein Strava-Übungstyp für {', '.join(unmapped)} "
                  f"(nur in der Beschreibung, bitte EXERCISE_MAP ergänzen).")
        if not payload:
            print(f"❌ {date_str}: Keine Übung zuordenbar, Upload übersprungen.")
            continue
        if DRY_RUN:
            print(f"🧪 {date_str}: Würde als neue Aktivität hochladen.")
            continue
        external_id = f"egym-{completed.strftime('%Y%m%dT%H%M%S')}"
        activity_id = upload_to_strava(payload, title, description, external_id, headers)
        write_fit(exercises, completed, date_str)
        if activity_id:
            print(f"✅ {date_str}: Neue Strava-Aktivität mit {len(payload['sets'])} Sätzen angelegt "
                  f"(https://www.strava.com/activities/{activity_id}).")

if __name__ == "__main__":
    s_token = get_strava_access_token()
    if s_token:
        e_workouts = get_egym_workouts()
        if e_workouts: sync_to_strava(e_workouts, s_token)
