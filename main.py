import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from github import Github  # pip install PyGithub

# Umgebungsvariablen laden (lokal aus .env, bei GitHub aus Secrets)
load_dotenv()

# Konfiguration
TENANT = os.getenv('EGYM_TENANT', 'benefit')
EGYM_EMAIL = os.getenv('EGYM_EMAIL')
EGYM_PASSWORD = os.getenv('EGYM_PASSWORD')

STRAVA_CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
STRAVA_REFRESH_TOKEN = os.getenv('STRAVA_REFRESH_TOKEN')

GH_PAT = os.getenv('GH_PAT')
REPO_NAME = os.getenv('GITHUB_REPOSITORY')

def update_github_secret(new_refresh_token):
    """Aktualisiert den Refresh-Token in den GitHub Secrets (falls GH_PAT vorhanden)."""
    if not GH_PAT or not REPO_NAME:
        print("ℹ️ Lokaler Modus oder kein GH_PAT: Secret wird nicht aktualisiert.")
        return

    try:
        g = Github(GH_PAT)
        repo = g.get_repo(REPO_NAME)
        repo.create_secret("STRAVA_REFRESH_TOKEN", new_refresh_token)
        print("🔐 GitHub Secret 'STRAVA_REFRESH_TOKEN' wurde erfolgreich aktualisiert.")
    except Exception as e:
        print(f"❌ Fehler beim Update des GitHub Secrets: {e}")

def get_strava_access_token():
    """Tauscht Refresh-Token gegen Access-Token und rotiert den Refresh-Token bei Bedarf."""
    print("🔄 Strava: Erneuere Access-Token...")
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
        new_refresh = data.get('refresh_token')
        
        # Falls Strava den Refresh-Token rotiert hat
        if new_refresh and new_refresh != STRAVA_REFRESH_TOKEN:
            update_github_secret(new_refresh)
            
        return data['access_token']
    else:
        print(f"❌ Strava Auth-Fehler: {res.text}")
        return None

def get_egym_workouts():
    """Loggt sich bei EGYM ein und holt Workouts ohne Umwege über Dateien."""
    session = requests.Session()
    base_url = f"https://{TENANT}.netpulse.com"
    headers = {'X-NP-APP-Version': '3.71', 'X-NP-API-Version': '1.5', 'User-Agent': 'NetpulseFitness/3.71'}

    print(f"🚀 EGYM: Login für {EGYM_EMAIL}...")
    try:
        # 1. Login
        login_res = session.post(f"{base_url}/np/exerciser/login", data={'username': EGYM_EMAIL, 'password': EGYM_PASSWORD}, headers=headers)
        login_res.raise_for_status()
        user_data = login_res.json()
        
        # 2. Token Tausch (FLS Access Token)
        token_url = f"{base_url}/np/micro-web-app/v1.0/exercisers/{user_data['uuid']}/tokens/FLS"
        token_res = session.get(token_url, headers=headers)
        access_token = token_res.json()['accessToken']

        # 3. Workouts laden
        print("📡 EGYM: Lade Trainingsdaten...")
        params = {
            "completedAfter": "2025-12-01T00:00:00Z",
            "completedBefore": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "gymLocationId": user_data['homeClubUuid'],
            "measurementSystem": "METRIC",
            "locale": "de-DE"
        }
        w_headers = {"Authorization": f"Bearer {access_token}", "X-Exerciser-Id": user_data['uuid'], "User-Agent": "egym/10.36.0"}
        res = session.get("https://mwa-api.int.api.egym.com/mwa/api/workouts/v1.0/workouts", headers=w_headers, params=params)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"❌ EGYM Fehler: {e}")
        return None

def sync_to_strava(workouts, strava_token):
    """Matcht Workouts und aktualisiert Strava direkt."""
    if not workouts: return
    
    headers = {"Authorization": f"Bearer {strava_token}"}
    print("🏃 Strava: Suche passende Aktivitäten...")
    res = requests.get("https://www.strava.com/api/v3/athlete/activities", headers=headers, params={"per_page": 10})
    
    if res.status_code != 200:
        print("❌ Strava: Abruf der Aktivitäten fehlgeschlagen.")
        return
    
    strava_acts = res.json()

    for workout in workouts:
        if workout.get('source') == 'GARMIN': continue
        
        date_str = workout.get('completedAt').split('T')[0]
        exercises = []
        
        # Extraktion der Übungen
        source = workout.get('exercises', []) or []
        if not source:
            for g in workout.get('exerciseGroups', []): source.extend(g.get('exercises', []))

        for ex in source:
            if ex.get('activity', {}).get('category') == "EGYM_MACHINE":
                name = ex.get('label', "").replace('EGYM ', '')
                details = [f"{s.get('weight')}kg x {s.get('numberOfReps')}" for s in ex.get('sets', []) if s.get('weight')]
                if details: exercises.append(f"🔹 {name}: {' | '.join(details)}")

        if exercises:
            for s_act in strava_acts:
                s_date = s_act['start_date_local'].split('T')[0]
                if s_date == date_str and s_act['type'] == 'WeightTraining':
                    title = f"🏋️ EGYM Zirkel ({len(exercises)} Übungen)" if len(exercises) >= 12 else f"💪 EGYM Krafttraining ({len(exercises)} Übungen)"
                    update_url = f"https://www.strava.com/api/v3/activities/{s_act['id']}"
                    requests.put(update_url, headers=headers, json={"name": title, "description": "\n".join(exercises)})
                    print(f"✅ Strava: Aktivität am {s_date} wurde aktualisiert.")
                    break

if __name__ == "__main__":
    s_token = get_strava_access_token()
    if s_token:
        e_workouts = get_egym_workouts()
        if e_workouts:
            sync_to_strava(e_workouts, s_token)