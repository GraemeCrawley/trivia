FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./
# The organizer guide is served at /guide.
COPY docs/RUNBOOK.md ./RUNBOOK.md

EXPOSE 8000

# ONE WORKER. NOT NEGOTIABLE.
# All live game state (the current question, each phone's deadline, the
# per-question barrier, scores) lives in this process's memory. A second worker
# would get its own copy and the game would silently split in half: half the
# room stuck on question 3, half on question 5, two different leaderboards, no
# error anywhere. Postgres is write-through durability for crash recovery, NOT
# shared state between workers. Do not add --workers.
# --timeout-graceful-shutdown is a BACKSTOP, not the fix. The app ends its own
# SSE streams on SIGTERM (see SHUTTING_DOWN in main.py); this bounds the damage
# if a future stream forgets to. Without either, one open stream makes SIGTERM
# hang forever and every redeploy is a SIGKILL in front of the room.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--timeout-graceful-shutdown", "5"]
