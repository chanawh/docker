import time
import os
import redis
import psycopg2
import yaml
from flask import Flask, jsonify, request, session, redirect, url_for, render_template_string, flash
from celery_worker import process_data
try:
    from celery_worker import process_order, celery, seed_database
    from celery.result import AsyncResult
    celery_features = True
except ImportError:
    celery_features = False

try:
    from flask_ldap3_login import LDAP3LoginManager
    from flask_oauthlib.client import OAuth
    ldap_oauth_enabled = True
except ImportError:
    ldap_oauth_enabled = False

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecretkey")
cache = redis.Redis(host=os.environ.get('REDIS_HOST', 'redis'), port=6379)

def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'db'),
        database=os.environ.get('POSTGRES_DB', 'mydb'),
        user=os.environ.get('POSTGRES_USER', 'myuser'),
        password=os.environ.get('POSTGRES_PASSWORD', 'mypassword')
    )
    return conn

def load_yaml_config(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return {}

ldap_cfg = load_yaml_config("security/auth/ldap-config.yaml")
oauth_cfg = load_yaml_config("security/auth/oauth-clients.yaml")

if ldap_oauth_enabled:
    app.config['LDAP_HOST'] = ldap_cfg.get('LDAP_HOST') or os.environ.get('LDAP_HOST', 'ldap://ldap.forumsys.com')
    app.config['LDAP_BASE_DN'] = ldap_cfg.get('LDAP_BASE_DN') or os.environ.get('LDAP_BASE_DN', 'dc=example,dc=com')
    app.config['LDAP_BIND_USER_DN'] = ldap_cfg.get('LDAP_BIND_USER_DN') or os.environ.get('LDAP_BIND_USER_DN', None)
    app.config['LDAP_BIND_USER_PASSWORD'] = ldap_cfg.get('LDAP_BIND_USER_PASSWORD') or os.environ.get('LDAP_BIND_USER_PASSWORD', None)
    ldap_manager = LDAP3LoginManager(app)

    oauth = OAuth(app)
    client = (oauth_cfg.get('clients') or [{}])[0]
    google = oauth.remote_app(
        'google',
        consumer_key=client.get('client_id') or os.environ.get('GOOGLE_CLIENT_ID', ''),
        consumer_secret=client.get('client_secret') or os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        request_token_params={'scope': 'email'},
        base_url='https://www.googleapis.com/oauth2/v1/',
        request_token_url=None,
        access_token_url='https://accounts.google.com/o/oauth2/token',
        authorize_url='https://accounts.google.com/o/oauth2/auth'
    )

@app.route("/")
def index():
    user = session.get("user")
    return f"Hello, Flask app is running!{' Logged in as: ' + user if user else ''}"

@app.route("/redis")
def test_redis():
    start = time.time()
    cache.set("test_key", "Hello from Redis!")
    value = cache.get("test_key")
    elapsed = time.time() - start
    return jsonify({
        "source": "redis",
        "value": value.decode("utf-8"),
        "elapsed_seconds": elapsed
    })

@app.route("/postgres")
def test_postgres():
    start = time.time()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM kvstore WHERE key = %s", ("test_key",))
    row = cur.fetchone()
    cur.close()
    conn.close()
    value = row[0] if row else None
    elapsed = time.time() - start
    return jsonify({
        "source": "postgres",
        "value": value,
        "elapsed_seconds": elapsed
    })

@app.route("/process", methods=["POST"])
def process():
    req_json = request.get_json()
    data = req_json.get("data")
    task = process_data.delay(data)
    return jsonify({"task_id": task.id}), 202

if celery_features:
    @app.route("/tasks/<task_id>")
    def get_task_status(task_id):
        task = AsyncResult(task_id, app=celery)
        return jsonify({"state": task.state, "result": task.result})

    @app.route("/products", methods=["POST"])
    def add_product():
        data = request.get_json()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products (name, price, stock) VALUES (%s, %s, %s) RETURNING product_id",
            (data['name'], data['price'], data['stock'])
        )
        product_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"product_id": product_id}), 201

    @app.route("/products", methods=["GET"])
    def get_products():
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT product_id, name, price, stock FROM products")
        products = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([{"product_id": p[0], "name": p[1], "price": str(p[2]), "stock": p[3]} for p in products])

    @app.route("/products/<int:product_id>", methods=["GET"])
    def get_product(product_id):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT product_id, name, price, stock FROM products WHERE product_id = %s", (product_id,))
        product = cur.fetchone()
        cur.close()
        conn.close()
        if product:
            return jsonify({"product_id": product[0], "name": product[1], "price": str(product[2]), "stock": product[3]})
        return jsonify({"message": "Product not found"}), 404

    @app.route("/orders", methods=["POST"])
    def create_order():
        req_json = request.get_json()
        customer_id = req_json.get("customer_id")
        items = req_json.get("items")  # List of {"product_id": ..., "quantity": ...}
        if not customer_id or not items:
            return jsonify({"message": "Customer ID and items are required"}), 400
        try:
            task = process_order.delay(customer_id, items)
            return jsonify({"message": "Order placed, processing in background", "task_id": task.id}), 202
        except Exception as e:
            return jsonify({"message": f"Error placing order: {str(e)}"}), 500

    @app.route("/orders/<string:task_id>", methods=["GET"])
    def get_order_status(task_id):
        task = process_order.AsyncResult(task_id)
        if task.state == 'PENDING':
            response = {'state': task.state, 'status': 'Pending...'}
        elif task.state != 'FAILURE':
            response = {'state': task.state, 'status': task.info.get('status', ''), 'result': task.info.get('result', None)}
        else:
            response = {'state': task.state, 'status': str(task.info)}
        return jsonify(response)

    @app.route("/seed", methods=["POST"])
    def seed():
        req_json = request.get_json()
        num_records = req_json.get("num_records", 1000)
        task = seed_database.delay(num_records)
        return jsonify({"task_id": task.id}), 202

    @app.route("/seed_status/<task_id>", methods=["GET"])
    def seed_status(task_id):
        task = AsyncResult(task_id)
        response = {"state": task.state}
        if task.state == 'SUCCESS':
            response["result"] = task.result
        elif task.state == 'FAILURE':
            response["error"] = str(task.info)
        return jsonify(response)

# --------- LDAP/OAUTH LOGIN ---------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if not ldap_oauth_enabled:
        return "LDAP/OAuth not installed.", 501
    login_html = """
    <!doctype html>
    <title>Login</title>
    <h2>Login</h2>
    <form method="post">
        <input name="username" placeholder="Username">
        <input name="password" type="password" placeholder="Password">
        <button type="submit">LDAP Login</button>
    </form>
    <a href="{{ url_for('login_google') }}">Login with Google</a>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        <ul>
        {% for category, message in messages %}
          <li><strong>{{ category }}:</strong> {{ message }}</li>
        {% endfor %}
        </ul>
      {% endif %}
    {% endwith %}
    """
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        response = ldap_manager.authenticate(username, password)
        if response.status == 'success':
            session['user'] = username
            flash('LDAP login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('LDAP login failed.', 'danger')
    return render_template_string(login_html)

@app.route('/login/google')
def login_google():
    if not ldap_oauth_enabled:
        return "LDAP/OAuth not installed.", 501
    return google.authorize(callback=url_for('google_authorized', _external=True))

@app.route('/oauth2callback')
def google_authorized():
    if not ldap_oauth_enabled:
        return "LDAP/OAuth not installed.", 501
    resp = google.authorized_response()
    if resp is None or resp.get('access_token') is None:
        flash('Google OAuth access denied.', 'danger')
        return redirect(url_for('login'))
    session['google_token'] = (resp['access_token'], '')
    me = google.get('userinfo')
    session['user'] = me.data.get('email', 'unknown')
    flash('Google login successful!', 'success')
    return redirect(url_for('index'))

if ldap_oauth_enabled:
    @google.tokengetter
    def get_google_oauth_token():
        return session.get('google_token')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)