# Render web service start command.
# Single worker (`--workers 1`) is important: the in-process APScheduler starts
# once at import time, so running multiple gunicorn workers would fire multiple
# background scan loops. With one worker there's exactly one scheduler.
web: gunicorn app:app --workers 1 --timeout 120
