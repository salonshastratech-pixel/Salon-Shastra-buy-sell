# gunicorn -c deploy/gunicorn.conf.py wsgi:app
bind = "127.0.0.1:8000"
workers = 3
threads = 2
timeout = 60
graceful_timeout = 30
max_requests = 2000
max_requests_jitter = 200
accesslog = None  # the app writes structured JSON access logs itself
errorlog = "-"
forwarded_allow_ips = "127.0.0.1"
