import os
from celery import Celery

celery = Celery(
    "worker",
    broker=os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq//"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
)
def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'db'),
        database=os.environ.get('POSTGRES_DB', 'mydb'),
        user=os.environ.get('POSTGRES_USER', 'myuser'),
        password=os.environ.get('POSTGRES_PASSWORD', 'mypassword')
    )
    return conn

@celery.task
def process_data(data):
    import time
    time.sleep(2)
    result = f"Processed: {data}"
    print(result)
    return result

@celery.task(bind=True, max_retries=3, default_retry_delay=300)
def process_order(self, customer_id, items):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1. Validate items and inventory
        total_amount = 0
        product_details = {}
        for item in items:
            product_id = item["product_id"]
            quantity = item["quantity"]

            cur.execute("SELECT name, price, stock FROM products WHERE product_id = %s FOR UPDATE", (product_id,))
            product = cur.fetchone()

            if not product or product[2] < quantity:
                raise ValueError(f"Product {product_id} not available in sufficient stock or does not exist")
            
            product_details[product_id] = {"name": product[0], "price": product[1]}
            total_amount += float(product[1]) * quantity
        
        # 2. Create the order
        cur.execute("INSERT INTO orders (customer_id, total_amount, status) VALUES (%s, %s, %s) RETURNING order_id", 
                    (customer_id, total_amount, 'pending'))
        order_id = cur.fetchone()[0]

        # 3. Add order items and update stock
        for item in items:
            product_id = item["product_id"]
            quantity = item["quantity"]
            product_price = product_details[product_id]["price"]

            cur.execute("INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (%s, %s, %s, %s)",
                        (order_id, product_id, quantity, product_price))
            cur.execute("UPDATE products SET stock = stock - %s WHERE product_id = %s", (quantity, product_id))

        conn.commit()
        cur.close()
        conn.close()

        # 4. Simulate payment processing (placeholder)
        time.sleep(5)  # Simulate delay for payment processing
        
        # 5. Update order status and notify user (asynchronous tasks)
        update_order_status.delay(order_id, 'processed')
        send_order_confirmation_email.delay(customer_id, order_id)

        return {"status": "Order processed successfully", "order_id": order_id}

    except ValueError as e:
        if conn:
            conn.rollback()
        self.update_state(state='FAILURE', meta={'status': f"Order failed: {str(e)}"})
        return {"status": f"Order failed: {str(e)}"}
    except Exception as e:
        if conn:
            conn.rollback()
        self.retry(exc=e)
        self.update_state(state='FAILURE', meta={'status': f"Order processing failed, retrying: {str(e)}"})
        return {"status": f"Order processing failed: {str(e)}"}

@celery.task
def update_order_status(order_id, status):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status = %s WHERE order_id = %s", (status, order_id))
    conn.commit()
    cur.close()
    conn.close()

@celery.task
def send_order_confirmation_email(customer_id, order_id):
    # Implement email sending logic here
    print(f"Sending order confirmation email to customer {customer_id} for order {order_id}")