import os
import requests
from flask import Flask, render_template, jsonify, request, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

API_BASE = "http://reports.sudata.co/private"
CLIENT_ID = os.environ.get("CLIENT_ID", "asd1")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "asd3")


def get_token():
    """Token de autenticación sudata con caching en sesión."""
    if "access_token" in session:
        return session["access_token"]
    r = requests.post(f"{API_BASE}/login", json={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=10)
    r.raise_for_status()
    token = r.json()["access_token"]
    session["access_token"] = token
    return token


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/reports")
def reports():
    token = get_token()
    r = requests.get(f"{API_BASE}/reports",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()
    return jsonify(r.json())


@app.route("/api/report-config")
def report_config():
    """
    El accessToken de Power BI dura minutos -> siempre se pide fresh.
    Si el token de sudata vencio (401), se renueva y reintenta.
    """
    report_id = request.args.get("report_id")
    token = get_token()
    r = requests.get(f"{API_BASE}/report-config",
                     params={"report_id": report_id},
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if r.status_code == 401:
        session.pop("access_token", None)
        token = get_token()
        r = requests.get(f"{API_BASE}/report-config",
                         params={"report_id": report_id},
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()
    return jsonify(r.json())


if __name__ == "__main__":
    app.run(debug=True)
