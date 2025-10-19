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

# This is temporary debugging code - Log environment variables
print("=== ENVIRONMENT DEBUG ===")
print(f"Supabase URL: {SUPABASE_URL}")
print(f"Supabase Anon Key (first 10 chars): {SUPABASE_ANON_KEY[:10]}..." if SUPABASE_ANON_KEY else "None")
print(f"App Base URL: {APP_BASE_URL}")
print(f"Secret Key (first 10 chars): {SECRET_KEY[:10]}..." if SECRET_KEY else "None")

app = Flask(__name__)
app.secret_key = SECRET_KEY

# This is temporary debugging code - Set up logging to see debug output
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)


def login_required(view_func):
	@wraps(view_func)
	def wrapped(*args, **kwargs):
		if not session.get("user"):
			return redirect(url_for("login"))
		return view_func(*args, **kwargs)
	return wrapped


@app.route("/")
def index():
	app.logger.debug("=== INDEX ROUTE DEBUG ===")
	app.logger.debug(f"Session user: {session.get('user')}")
	if session.get("user"):
		app.logger.debug("User found in session, redirecting to dashboard")
		return redirect(url_for("dashboard"))
	app.logger.debug("No user in session, redirecting to login")
	return redirect(url_for("login"))


@app.route("/login")
def login():
	app.logger.debug("=== LOGIN ROUTE DEBUG ===")
	app.logger.debug(f"Session user: {session.get('user')}")
	if session.get("user"):
		app.logger.debug("User found in session, redirecting to dashboard")
		return redirect(url_for("dashboard"))
	app.logger.debug("Rendering login template")
	return render_template("login.html", supabase_url=SUPABASE_URL, supabase_anon_key=SUPABASE_ANON_KEY)


@app.route("/callback")
def callback_page():
	# This page is where Supabase redirects back to. It runs client-side JS to
	# exchange the code for a session and then posts to /auth/complete.
	app.logger.debug("=== CALLBACK ROUTE DEBUG ===")
	app.logger.debug(f"Request URL: {request.url}")
	app.logger.debug(f"Request args: {dict(request.args)}")
	app.logger.debug(f"Request method: {request.method}")
	app.logger.debug(f"Request headers: {dict(request.headers)}")
	app.logger.debug(f"Request form data: {dict(request.form)}")
	app.logger.debug(f"Request JSON: {request.get_json(silent=True)}")
	
	# Check if we have the OAuth parameters that Supabase expects
	code = request.args.get('code')
	state = request.args.get('state')
	error = request.args.get('error')
	error_description = request.args.get('error_description')
	
	app.logger.debug(f"OAuth parameters - code: {code is not None}, state: {state is not None}")
	app.logger.debug(f"OAuth error: {error}, description: {error_description}")
	
	if error:
		app.logger.error(f"OAuth error from provider: {error} - {error_description}")
		return render_template("callback.html", 
			supabase_url=SUPABASE_URL, 
			supabase_anon_key=SUPABASE_ANON_KEY,
			oauth_error=error,
			oauth_error_description=error_description)
	
	app.logger.debug("Rendering callback template")
	return render_template("callback.html", supabase_url=SUPABASE_URL, supabase_anon_key=SUPABASE_ANON_KEY)


@app.post("/auth/complete")
def auth_complete():
	# This is temporary debugging code - Log incoming request
	app.logger.debug("=== AUTH COMPLETE DEBUG ===")
	app.logger.debug(f"Request method: {request.method}")
	app.logger.debug(f"Request content type: {request.content_type}")
	app.logger.debug(f"Request headers: {dict(request.headers)}")
	
	data = request.get_json(silent=True) or {}
	# This is temporary debugging code - Log request data
	app.logger.debug(f"Request data: {data}")
	
	access_token = data.get("access_token")
	provider = data.get("provider")
	
	# This is temporary debugging code - Log extracted values
	app.logger.debug(f"Access token present: {bool(access_token)}")
	app.logger.debug(f"Access token (first 20 chars): {access_token[:20] + '...' if access_token else 'None'}")
	app.logger.debug(f"Provider: {provider}")
	
	if not access_token:
		# This is temporary debugging code - Log missing token error
		app.logger.error("ERROR: Missing access_token")
		return jsonify({"ok": False, "error": "missing access_token"}), 400

	# This is temporary debugging code - Log Supabase config
	app.logger.debug(f"Supabase URL: {SUPABASE_URL}")
	app.logger.debug(f"Supabase Anon Key (first 10 chars): {SUPABASE_ANON_KEY[:10]}...")

	# Verify the access token with Supabase and fetch the user
	# This is temporary debugging code - Log before making request
	app.logger.debug("Making request to Supabase auth endpoint...")
	
	user_resp = requests.get(
		f"{SUPABASE_URL}/auth/v1/user",
		headers={
			"Authorization": f"Bearer {access_token}",
			"apikey": SUPABASE_ANON_KEY,
		},
		timeout=10,
	)
	
	# This is temporary debugging code - Log response details
	app.logger.debug(f"Supabase response status: {user_resp.status_code}")
	app.logger.debug(f"Supabase response headers: {dict(user_resp.headers)}")
	
	if user_resp.status_code != 200:
		# This is temporary debugging code - Log error response
		app.logger.error(f"ERROR: Supabase returned {user_resp.status_code}")
		app.logger.error(f"Response text: {user_resp.text}")
		return jsonify({"ok": False, "error": "invalid token"}), 401

	user_json = user_resp.json()
	# This is temporary debugging code - Log user data
	app.logger.debug(f"User data from Supabase: {user_json}")
	
	# Persist minimal user info in the session
	session["user"] = {
		"id": user_json.get("id"),
		"email": (user_json.get("email") or (user_json.get("user_metadata") or {}).get("email")),
		"provider": provider,
	}
	
	# This is temporary debugging code - Log session data
	app.logger.debug(f"Session user set to: {session.get('user')}")
	app.logger.debug("Authentication completed successfully")
	
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

