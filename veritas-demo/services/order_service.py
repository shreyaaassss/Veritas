"""
Simulated order-service — writes to /logs/order-service.log

Violation type: EXPOSURE_001
Trigger: raw PII (name + phone + address) dumped in a DEBUG log line.
source_system must be exactly "order-service" to match the blinkit registry.
"""

import os
import random
import time

from common import (
    ORDER_STATUSES, fake_address, fake_name, fake_order_id, fake_phone, ts,
)

LOG_FILE = "/logs/order-service.log"
RATE     = float(os.getenv("VIOLATION_RATE", "0.10"))
INTERVAL = float(os.getenv("EMIT_INTERVAL", "2.0"))

ZONES    = ["KG-4", "AN-2", "BN-7", "MU-3", "PU-1"]
AMOUNTS  = [149, 247, 389, 512, 678, 834, 1024, 1299]


def clean_line() -> str:
    order = fake_order_id()
    choice = random.randint(0, 2)
    if choice == 0:
        status = random.choice(ORDER_STATUSES)
        return f"[{ts()}] INFO order-service: order {order} status updated to '{status}'"
    elif choice == 1:
        zone = random.choice(ZONES)
        eta  = random.randint(15, 45)
        return f"[{ts()}] INFO order-service: order {order} assigned to zone {zone}, ETA {eta}min"
    else:
        amt = random.choice(AMOUNTS)
        return f"[{ts()}] INFO order-service: payment completed for order {order}, amount \u20b9{amt}"


def violation_line() -> str:
    """EXPOSURE_001 — raw customer PII in a debug log dump."""
    name    = fake_name()
    phone   = fake_phone()
    address = fake_address()
    return (
        f'[{ts()}] DEBUG order-service: fetched customer record '
        f'{{name: "{name}", phone: "{phone}", address: "{address}"}}'
    )


if __name__ == "__main__":
    print(f"[order-service] starting — violation_rate={RATE}, interval={INTERVAL}s", flush=True)
    with open(LOG_FILE, "a", buffering=1) as f:
        while True:
            line = violation_line() if random.random() < RATE else clean_line()
            f.write(line + "\n")
            time.sleep(INTERVAL)
