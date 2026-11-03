# AGENTS.md: setting up Trivia for a user

You are helping a human run their own copy of this game. README.md is the
user-facing version. Here are the essentials.

## What it is

One Docker Compose service, `trivia` (FastAPI, one uvicorn worker), on
`${TRIVIA_BIND:-0.0.0.0}:${TRIVIA_PORT:-8000}`. State: a named volume
`trivia-data` mounted at `/data` (selfies, crash-recovery snapshot, admin login
secret). Questions, images and branding are bind-mounted read-only from
`app/seed/` and `app/static/questions/`. Config comes from `.env` (template:
`.env.example`, every variable documented there).

## Ask the human for these (you can't get them yourself)

1. **Organizer password** (`BARGAMES_ADMIN_PASSWORD`). Offer to generate one
   (`openssl rand -base64 12`) and tell them where it's saved. Don't echo it into
   logs or commit messages.
2. **Where they'll play**: same Wi-Fi as this computer, or away from home (bar,
   etc.). Away from home needs an HTTPS tunnel; see README "Playing away from home".
3. Optional: a title for the night, and their own questions.

## Steps

```bash
cp .env.example .env            # then set BARGAMES_ADMIN_PASSWORD
docker compose up -d --build
```

For a tunnel / HTTPS setup also set `BARGAMES_COOKIE_SECURE=1`,
`BARGAMES_TRUST_PROXY=1`, `TRIVIA_BIND=127.0.0.1`, then
`cloudflared tunnel --url http://localhost:8000` (or `tailscale funnel 8000`).

## Verify

```bash
docker compose ps                                     # trivia "Up (healthy)"
curl -fsS http://localhost:${TRIVIA_PORT:-8000}/healthz
docker compose logs --tail=30 trivia                  # no tracebacks
```

Then have the human open `http://<LAN address>:8000` on a phone (find the address
with `hostname -I`, `ipconfig`, or macOS network settings) and confirm the PIN page
appears. If it doesn't, it's almost always the firewall or guest Wi-Fi isolation.

Questions: `app/seed/questions.json` (format in README "Writing your own
questions"). Validate after editing:
`python -c "import json;json.load(open('app/seed/questions.json'))"`, then
`docker compose restart`.

Tests:

```bash
pip install -r requirements.txt pytest httpx && pytest tests --ignore=tests/e2e
```

## Rules

- **Exactly one worker.** All live game state is in one process's memory. Never
  add `--workers` or scale the service; the room would silently split in two.
- **Keep all URLs in templates and JS relative** (`api/state`, not `/api/state`);
  the app may be served under a proxy sub-path.
- **Selfie serving is fail-closed** (`docs/HOW-IT-WORKS.md` section 6). Never serve
  photos from `/static` or any guessable path, and never add a file extension to
  `/selfie/<id>`.
- **Never commit `.env`, photos, `game-state.json` or `admin-secret`.** `.gitignore`
  covers these; don't override it with `git add -f`.
- **Never push players' names, photos or a real night's question pack** that
  mentions real people to any public remote, issue or paste site.
- **Never run `docker compose down -v`** without explicit confirmation: it deletes
  photos and any saved game.
- **No occasion-specific wording in code** (`tests/test_generic_engine.py` enforces
  it). Themes, titles and questions are data.
- Sample questions must stay obviously generic and made up.
