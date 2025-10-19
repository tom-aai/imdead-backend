import os
import logging
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from dotenv import load_dotenv
import requests

# Load environment variables: prefer .env.local, fallback to default .env if present
_loaded = load_dotenv(".env.local")
if not _loaded:
	load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", os.urandom(32).hex())
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5001")


app = Flask(__name__)
app.secret_key = SECRET_KEY



def login_required(view_func):
	@wraps(view_func)
	def wrapped(*args, **kwargs):
		if not session.get("user"):
			return redirect(url_for("login"))
		return view_func(*args, **kwargs)
	return wrapped


@app.route("/")
def index():
	if session.get("user"):
		return redirect(url_for("dashboard"))
	return redirect(url_for("login"))


@app.route("/login")
def login():
	if session.get("user"):
		return redirect(url_for("dashboard"))
	return render_template("login.html", supabase_url=SUPABASE_URL, supabase_anon_key=SUPABASE_ANON_KEY)


@app.route("/callback")
def callback_page():
	# This page is where Supabase redirects back to. It runs client-side JS to
	# exchange the code for a session and then posts to /auth/complete.
	
	# Check if we have the OAuth parameters that Supabase expects
	error = request.args.get('error')
	error_description = request.args.get('error_description')
	
	if error:
		return render_template("callback.html", 
			supabase_url=SUPABASE_URL, 
			supabase_anon_key=SUPABASE_ANON_KEY,
			oauth_error=error,
			oauth_error_description=error_description)
	
	return render_template("callback.html", supabase_url=SUPABASE_URL, supabase_anon_key=SUPABASE_ANON_KEY)


@app.post("/auth/complete")
def auth_complete():
	data = request.get_json(silent=True) or {}
	access_token = data.get("access_token")
	provider = data.get("provider")
	
	if not access_token:
		return jsonify({"ok": False, "error": "missing access_token"}), 400

	# Verify the access token with Supabase and fetch the user
	user_resp = requests.get(
		f"{SUPABASE_URL}/auth/v1/user",
		headers={
			"Authorization": f"Bearer {access_token}",
			"apikey": SUPABASE_ANON_KEY,
		},
		timeout=10,
	)
	
	if user_resp.status_code != 200:
		return jsonify({"ok": False, "error": "invalid token"}), 401

	user_json = user_resp.json()
	
	# Persist minimal user info in the session
	session["user"] = {
		"id": user_json.get("id"),
		"email": (user_json.get("email") or (user_json.get("user_metadata") or {}).get("email")),
		"provider": provider,
	}
	
	return jsonify({"ok": True})


@app.route("/dashboard")
@login_required
def dashboard():
	return render_template("dashboard.html", user=session.get("user"))


@app.route("/logout")
@login_required
def logout():
	# Clear server-side session
	session.clear()
	# Optional: log out from Supabase and then return to login
	query = urlencode({
		"redirect_to": f"{APP_BASE_URL}/login",
	})
	logout_url = f"{SUPABASE_URL}/auth/v1/logout?{query}"
	# We cannot call logout server-side without a user access token; just redirect client to login
	return redirect(url_for("login"))


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)

