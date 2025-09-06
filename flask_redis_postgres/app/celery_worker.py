import os
from celery import Celery

celery = Celery(
    "worker",
    broker=os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq//"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
)

@celery.task
def process_data(data):
    import time
    time.sleep(2)
    result = f"Processed: {data}"
    print(result)
    return result