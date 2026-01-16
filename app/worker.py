from rq import Queue
from redis import Redis
# Imports not strictly needed here for queue definition, 
# but must be importable by the RQ worker process.
from app.services.payment_service import (
    process_successful_delivery_payment,
    process_successful_food_payment,
    # process_successful_laundry_payment
)
from app.config.config import settings

redis_conn = Redis.from_url(settings.REDIS_URL)
queue = Queue(connection=redis_conn)