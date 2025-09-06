import time
import os
import redis
import psycopg2
from flask import Flask, jsonify, request
from celery_worker import process_data

app = Flask(__name__)
cache = redis.Redis(host=os.environ.get('REDIS_HOST', 'redis'), port=6379)

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
    return "Hello, Flask app is running!"

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)