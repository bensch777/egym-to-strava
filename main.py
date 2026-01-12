import os
import requests
from datetime import datetime
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

def sync_to_strava(workouts, strava_token):
    """Synchronisiert EGYM-Daten zu Strava, falls noch nicht geschehen."""
    if not workouts: return
    headers = {"Authorization": f"Bearer {strava_token}"}
    strava_acts = requests.get("https://www.strava.com/api/v3/athlete/activities", headers=headers, params={"per_page": 10}).json()

    for workout in workouts:
        if workout.get('source') == 'GARMIN': continue
        date_str = workout.get('completedAt').split('T')[0]
        
        # Übungen aufbereiten
        exercises = []
        source = workout.get('exercises', []) or []
        if not source:
            for g in workout.get('exerciseGroups', []): source.extend(g.get('exercises', []))
        for ex in source:
            if ex.get('activity', {}).get('category') == "EGYM_MACHINE":
                details = [f"{s.get('weight')}kg x {s.get('numberOfReps')}" for s in ex.get('sets', []) if s.get('weight')]
                if details: exercises.append(f"🔹 {ex.get('label', '').replace('EGYM ', '')}: {' | '.join(details)}")

        if exercises:
            for s_act in strava_acts:
                s_date = s_act['start_date_local'].split('T')[0]
                # PRÜFUNG: Datum passt, Typ ist WeightTraining UND 'EGYM' steht noch NICHT im Titel
                if s_date == date_str and s_act['type'] == 'WeightTraining':
                    if "EGYM" in s_act['name']:
                        print(f"⏭️ {s_date}: Bereits als EGYM-Workout markiert.")
                        continue
                    
                    # Update durchführen
                    count = len(exercises)
                    title = f"🏋️ EGYM Zirkel ({count} Übungen)" if count >= 12 else f"💪 EGYM Krafttraining ({count} Übungen)"
                    requests.put(f"https://www.strava.com/api/v3/activities/{s_act['id']}", 
                                 headers=headers, json={"name": title, "description": "\n".join(exercises)})
                    print(f"✅ {s_date}: Strava Titel und Beschreibung aktualisiert.")
                    break

if __name__ == "__main__":
    s_token = get_strava_access_token()
    if s_token:
        e_workouts = get_egym_workouts()
        if e_workouts: sync_to_strava(e_workouts, s_token)