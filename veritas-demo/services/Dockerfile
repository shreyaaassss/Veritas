FROM python:3.12-slim

WORKDIR /app

# Copy all service scripts and shared helpers
COPY common.py \
     order_service.py \
     support_service.py \
     marketing_service.py \
     delivery_service.py \
     ./

# No pip install needed — pure Python stdlib + common.py
# Each service is started via docker-compose 'command:'

CMD ["echo", "Specify a service: command: python order_service.py"]
