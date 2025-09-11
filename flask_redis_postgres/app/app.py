import time
import os
import redis
import psycopg2
import yaml
from flask import Flask, jsonify, request, session, redirect, url_for, render_template_string, flash
import logging
from ldap3.utils.log import set_library_log_activation_level, set_library_log_detail_level
from ldap3.utils.log import EXTENDED as LDAP_LOG_EXTENDED
from flask_ldap3_login import LDAP3LoginManager, AuthenticationResponseStatus
from ldap3.core.exceptions import LDAPException
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv  # Import load_dotenv
from ldap3 import Server, Connection, SIMPLE, SYNC

# Enable Python's standard logging for debug messages
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# Set ldap3 library to its most verbose logging level
set_library_log_activation_level(logging.DEBUG)
set_library_log_detail_level(LDAP_LOG_EXTENDED)

# Load environment variables from .env file
load_dotenv()

from celery_worker import process_data
try:
    from celery_worker import process_order, celery, seed_database
    from celery.result import AsyncResult
    celery_features = True
except ImportError:
    celery_features = False

ldap_oauth_enabled = True

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecretkey")
cache = redis.Redis(host=os.environ.get('REDIS_HOST', 'redis'), port=6379)

def load_yaml_config(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return {}

ldap_cfg = load_yaml_config("security/auth/ldap-config.yaml")
oauth_cfg = load_yaml_config("security/auth/oauth-clients.yaml")

# --- Use environment variables for LDAP configuration ---
app.config['LDAP_HOST'] = os.environ.get('LDAP_HOST', 'ldap.forumsys.com')
app.config['LDAP_BASE_DN'] = os.environ.get('LDAP_BASE_DN', 'dc=example,dc=com')
app.config['LDAP_BIND_USER_DN'] = os.environ.get('LDAP_BIND_USER_DN', 'cn=read-only-admin,dc=example,dc=com')
app.config['LDAP_BIND_USER_PASSWORD'] = os.environ.get('LDAP_BIND_USER_PASSWORD', 'password')
app.config['LDAP_USER_DN'] = os.environ.get('LDAP_USER_DN', 'dc=example,dc=com')
app.config['LDAP_USER_RDN_ATTR'] = os.environ.get('LDAP_USER_RDN_ATTR', 'uid')
app.config['LDAP_USER_LOGIN_ATTR'] = os.environ.get('LDAP_USER_LOGIN_ATTR', 'uid')
app.config['LDAP_USER_SEARCH_SCOPE'] = os.environ.get('LDAP_USER_SEARCH_SCOPE', 'SUBTREE')
app.config['LDAP_GROUP_DN'] = os.environ.get('LDAP_GROUP_DN', 'dc=example,dc=com')
app.config['LDAP_GROUP_OBJECT_FILTER'] = os.environ.get('LDAP_GROUP_OBJECT_FILTER', '(objectclass=posixGroup)')
app.config['LDAP_GROUP_MEMBERS_ATTR'] = os.environ.get('LDAP_GROUP_MEMBERS_ATTR', 'memberUid')
app.config['LDAP_GROUP_SEARCH_SCOPE'] = os.environ.get('LDAP_GROUP_SEARCH_SCOPE', 'SUBTREE')
app.config['LDAP_ALWAYS_SEARCH_BIND'] = os.environ.get('LDAP_ALWAYS_SEARCH_BIND', True)
app.config['DEBUG'] = True
app.logger.setLevel(logging.DEBUG)

ldap_manager = LDAP3LoginManager(app)
oauth = OAuth(app)
client = (oauth_cfg.get('clients') or [{}])[0]

# --- Authlib remote app registration ---
CONF_URL = 'https://accounts.google.com/.well-known/openid-configuration'
oauth.register(
    name='google',
    server_metadata_url=CONF_URL,
    client_id=client.get('client_id') or os.environ.get('GOOGLE_CLIENT_ID', ''),
    client_secret=client.get('client_secret') or os.environ.get('GOOGLE_CLIENT_SECRET', ''),
    client_kwargs={'scope': 'openid email profile'}
)

def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'db'),
        database=os.environ.get('POSTGRES_DB', 'mydb'),
        user=os.environ.get('POSTGRES_USER', 'myuser'),
        password=os.environ.get('POSTGRES_PASSWORD', 'mypassword')
    )
    return conn

@app.route("/")
def index():
    user = session.get("user")
    return f"Hello, Flask app is running!{' Logged in as: ' + user['id'] if user else ''}"

# --------- LDAP/OAUTH LOGIN ---------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user'):
        flash('Already logged in.', 'success')
        return redirect(url_for('index'))

    if ldap_oauth_enabled and request.method == 'POST':
        if 'ldap_login' in request.form:
            username = request.form['username']
            password = request.form['password']
            
            try:
                # The authenticate method raises an LDAPException on failure
                response = ldap_manager.authenticate(username, password)
                
                if response.status == AuthenticationResponseStatus.success:
                    session['user'] = {'id': username, 'auth_method': 'ldap'}
                    app.logger.debug(f"Session after setting: {session}")
                    flash(f"Successfully logged in as {username} via LDAP.", 'success')
                    return redirect(url_for('profile'))
                else:
                    # In case of non-exception failure (e.g., wrong credentials)
                    flash("LDAP authentication failed.", 'danger')

            except LDAPException as e:
                app.logger.error(f"LDAP Exception: {e}")
                flash("An error occurred during LDAP authentication.", 'danger')
            
    return render_template_string('''
        <h1>Login</h1>
        
        <h2>LDAP Login</h2>
        <form method="post">
            <input type="hidden" name="ldap_login" value="1">
            <p>Username: <input type="text" name="username"></p>
            <p>Password: <input type="password" name="password"></p>
            <p><input type="submit" value="LDAP Login"></p>
        </form>
        
        {% if oauth_configured %}
        <h2>Or</h2>
        <p><a href="{{ url_for('google_login') }}">Login with Google</a></p>
        {% endif %}
    ''', oauth_configured=bool(client.get('client_id')))

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("You have been logged out.", 'info')
    return redirect(url_for('index'))

@app.route('/profile')
def profile():
    app.logger.debug(f"Session in profile route: {session}")
    if not session.get('user'):
        flash("Please log in to view this page.", 'warning')
        return redirect(url_for('login'))
    user = session.get('user')
    return f"<h1>Profile Page</h1><p>User ID: {user['id']}</p><p>Auth Method: {user['auth_method']}</p><p><a href='{url_for('logout')}'>Logout</a></p>"

# --- Corrected Google OAuth routes for Authlib ---
@app.route('/oauth/google')
def google_login():
    redirect_uri = url_for('google_authorized', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.route('/oauth/google/authorized')
def google_authorized():
    try:
        token = oauth.google.authorize_access_token()
        user_info = oauth.google.parse_id_token(token)
        session['user'] = {'id': user_info.get('email'), 'auth_method': 'google', 'name': user_info.get('name')}
        flash(f"Successfully logged in as {user_info.get('email')} via Google.", 'success')
        return redirect(url_for('profile'))
    except Exception as e:
        app.logger.error(f"Google OAuth failed: {e}")
        flash("Google authentication failed.", 'danger')
        return redirect(url_for('login'))

# Flask-LDAP3-Login callbacks (unchanged)
@ldap_manager.save_user
def save_user(dn, username, data, memberships):
    user = {
        'id': username,
        'dn': dn,
        'data': data,
        'memberships': memberships,
        'auth_method': 'ldap'
    }
    return user

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
