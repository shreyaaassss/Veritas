"""
Simulated support-ticketing service — writes to /logs/support-ticketing.log

Violation type: EXPOSURE_001
Trigger: customer PII (name + phone) appears in a support agent's freetext note.
source_system must be exactly "support-ticketing" to match the blinkit registry.
"""

import os
import random
import time

from common import (
    TICKET_CATEGORIES, fake_name, fake_order_id, fake_phone, fake_ticket_id, ts,
)

LOG_FILE = "/logs/support-ticketing.log"
RATE     = float(os.getenv("VIOLATION_RATE", "0.10"))
INTERVAL = float(os.getenv("EMIT_INTERVAL", "3.0"))

AGENTS        = [f"A-{n}" for n in [12, 14, 17, 21, 25, 31]]
RESOLUTIONS   = ["refund_initiated", "replacement_dispatched", "escalated_to_L2", "closed_no_action"]
SLA_MINUTES   = [23, 31, 47, 52, 68, 89]


def clean_line() -> str:
    ticket = fake_ticket_id()
    choice = random.randint(0, 2)
    if choice == 0:
        cat = random.choice(TICKET_CATEGORIES)
        return f"[{ts()}] INFO support-ticketing: ticket {ticket} opened, category: {cat}"
    elif choice == 1:
        agent = random.choice(AGENTS)
        res   = random.choice(RESOLUTIONS)
        return f"[{ts()}] INFO support-ticketing: ticket {ticket} resolved by agent {agent}, resolution: {res}"
    else:
        mins = random.choice(SLA_MINUTES)
        return f"[{ts()}] WARN support-ticketing: SLA breach risk — ticket {ticket} open {mins}min, threshold 60min"


def violation_line() -> str:
    """EXPOSURE_001 — agent note contains customer name and phone number."""
    name  = fake_name()
    phone = fake_phone()
    order = fake_order_id()
    issue = random.choice([
        f"requesting status on order {order}",
        f"reporting missing item in order {order}",
        f"asking for refund on order {order}",
        "unable to track delivery",
    ])
    return (
        f"[{ts()}] DEBUG support-ticketing: agent note — "
        f"customer {name} called, phone {phone}, {issue}"
    )


if __name__ == "__main__":
    print(f"[support-ticketing] starting — violation_rate={RATE}, interval={INTERVAL}s", flush=True)
    with open(LOG_FILE, "a", buffering=1) as f:
        while True:
            line = violation_line() if random.random() < RATE else clean_line()
            f.write(line + "\n")
            time.sleep(INTERVAL)
