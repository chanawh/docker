import time
import os
import redis
import psycopg2
from flask import Flask, jsonify, request
from celery_worker import process_data
from celery_worker import process_order
from celery.result import AsyncResult

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
        task = process_order.delay(customer_id, items)  # Delegate to Celery
        return jsonify({"message": "Order placed, processing in background", "task_id": task.id}), 202
    except Exception as e:
        return jsonify({"message": f"Error placing order: {str(e)}"}), 500

@app.route("/orders/<string:task_id>", methods=["GET"])
def get_order_status(task_id):
    task = process_order.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'status': 'Pending...'
        }
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'status': task.info.get('status', ''),
            'result': task.info.get('result', None)
        }
    else:
        response = {
            'state': task.state,
            'status': str(task.info),  # Exception information
        }
    return jsonify(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)