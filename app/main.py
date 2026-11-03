"""bargames — phone-first bar trivia.

See ../docs/HOW-IT-WORKS.md. One process, one worker: this module holds the single
live Game in memory and is the only authority on it.
"""

import asyncio
import json
import os
import secrets
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import persist
import selfies
from engine import Question
from game import Game, Phase

SSE_HEARTBEAT_SECONDS = 10
TICK_SECONDS = 0.2

# Cookie scope. Defaults suit a plain local install (http://<this-computer>:8000/).
# Behind a reverse proxy that serves the app under a sub-path and strips it
# (e.g. https://example.com/trivia/ -> /), the app never sees that prefix, so
# set BARGAMES_COOKIE_PATH to it explicitly. Set BARGAMES_COOKIE_SECURE=1 when
# the app is served over HTTPS.
COOKIE_PATH = os.getenv("BARGAMES_COOKIE_PATH", "/")
COOKIE_SECURE = os.getenv("BARGAMES_COOKIE_SECURE", "0") == "1"

ADMIN_PASSWORD = os.getenv("BARGAMES_ADMIN_PASSWORD", "")
def _admin_secret() -> str:
    """Stable across restarts, so a container bounce does not sign the
    organizer out and make her re-type the password in front of the room.

    Kept beside the game state rather than in the photo directory, which
    delete_all() wipes.
    """
    from_env = os.getenv("BARGAMES_ADMIN_SECRET")
    if from_env:
        return from_env
    path = Path(os.getenv(
        "BARGAMES_SECRET_FILE",
        str(Path(os.getenv("BARGAMES_SELFIE_DIR", "/selfies")) / "admin-secret"),
    ))
    try:
        if path.is_file():
            existing = path.read_text().strip()
            if existing:
                return existing
        path.parent.mkdir(parents=True, exist_ok=True)
        minted = secrets.token_urlsafe(24)
        path.write_text(minted)
        path.chmod(0o600)
        return minted
    except OSError:
        # Unwritable volume: fall back to a per-process secret. She gets logged
        # out by a restart, which is the old behaviour — not a new failure.
        return secrets.token_urlsafe(24)


ADMIN_SECRET = _admin_secret()

# The join page carries a visible door to the organizer login, and the whole
# internet can reach that page. The endpoint was always unthrottled; making it
# discoverable is the moment to fix that.
ADMIN_MAX_FAILURES = 5
ADMIN_LOCKOUT_SECONDS = 60.0
_admin_failures: dict[str, list] = {}


def _client_ip(request: Request) -> str:
    # Behind a reverse proxy the socket peer is always the proxy, so the
    # forwarded headers are the only thing that tells callers apart. They are
    # trusted ONLY when BARGAMES_TRUST_PROXY=1: on a direct install anyone can
    # send them, and trusting them would let a caller dodge the login lockout.
    if os.getenv("BARGAMES_TRUST_PROXY", "0") == "1":
        forwarded = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        )
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def _admin_locked(ip: str, now: float) -> bool:
    record = _admin_failures.get(ip)
    if not record:
        return False
    count, last = record
    if count < ADMIN_MAX_FAILURES:
        return False
    if now - last > ADMIN_LOCKOUT_SECONDS:
        _admin_failures.pop(ip, None)
        return False
    return True

BASE = Path(__file__).parent


def _asset_version() -> str:
    """A token that changes whenever the CSS or JS changes.

    Static assets are served with max-age=14400 and a CDN may cache them, so
    without this a deploy does not reach a phone for up to four hours — the
    page comes back with new HTML and a four-hour-old stylesheet, which looks
    exactly like a broken layout. Appending this to the asset URLs means a new
    build is a new URL and cannot be served from anyone's old cache.
    """
    import hashlib
    h = hashlib.sha1()
    for name in ("static/style.css", "static/app.js", "static/admin.js"):
        f = Path(__file__).parent / name
        try:
            stat = f.stat()
            h.update(f"{name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def compute_build_id() -> str:
    """A short id for exactly this build, shown in the corner of every screen.

    The recurring confusion tonight has been "is this phone on the new code or
    a cached copy?". A stamp you can read off the screen and compare settles it
    without guesswork. Derived from the source files, so every deploy changes
    it and nothing else does.
    """
    import hashlib
    h = hashlib.sha1()
    root = Path(__file__).parent
    for name in sorted([
        "main.py", "game.py", "engine.py", "selfies.py", "persist.py",
        "static/style.css", "static/app.js", "static/admin.js",
        "templates/join.html", "templates/play.html", "templates/admin.html",
        "seed/questions.json",
    ]):
        f = root / name
        try:
            st = f.stat()
            h.update(f"{name}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:7]


BUILD_ID = compute_build_id()
# Which question pack to play. Lets a different set be dropped in without a
# rebuild — and lets tests that need a type the live pack does not contain
# (free text, the number wheel) run against the generic pack.
SEED = Path(os.getenv("BARGAMES_QUESTIONS") or (BASE / "seed" / "questions.json"))
BRANDING_FILE = BASE / "seed" / "branding.json"

def branding() -> dict:
    """Title, badge and theme for tonight.

    This is the ONLY channel through which an occasion reaches the app.
    bargames is a generic engine; anything occasion-specific belongs here, in
    data, and never in code, logic, seed questions, routes, ids or filenames.
    tests/test_generic_engine.py enforces that.
    """
    try:
        data = json.loads(BRANDING_FILE.read_text())
    except Exception:
        data = {}
    return {
        "title": os.getenv("BARGAMES_TITLE") or data.get("title") or "Bar Games",
        "badge": os.getenv("BARGAMES_BADGE") or data.get("badge") or "",
        "theme": os.getenv("BARGAMES_THEME") or data.get("theme") or "default",
    }


game: Game | None = None
subscribers: set[asyncio.Queue] = set()

# Set the instant SIGTERM arrives, so open SSE streams can end themselves.
#
# This CANNOT be signalled from lifespan teardown: uvicorn drains open
# connections BEFORE it runs teardown, so an event set there is set only after
# the wait it was meant to end — the streams wait for the event and the event
# waits for the streams. Measured at 12s and still needing SIGKILL. The signal
# handler fires during the drain, which is the whole point.
SHUTTING_DOWN = asyncio.Event()


def load_questions() -> list[Question]:
    data = json.loads(SEED.read_text())
    out = []
    for q in data["questions"]:
        out.append(Question(
            id=q["id"], type=q["type"], prompt=q["prompt"],
            correct=q.get("correct"), options=q.get("options"),
            span=q.get("span"), image=q.get("image"),
        ))
    return out


def new_game(timer=15.0, per_round=5, reveal=5.0) -> Game:
    return Game(
        questions=load_questions(), timer_seconds=timer,
        questions_per_round=per_round, reveal_seconds=reveal, now=time.time(),
    )


# ---------------------------------------------------------------- snapshots

def question_payload(g: Game) -> dict | None:
    """What a phone is allowed to know about the open question.

    The correct answer is deliberately absent. It is added only by
    reveal_payload, only once the barrier has released — otherwise anyone with
    devtools open wins the game.
    """
    q = g.current_question
    if q is None:
        return None
    raw = next((r for r in json.loads(SEED.read_text())["questions"] if r["id"] == q.id), {})
    return {
        "id": q.id, "type": q.type, "prompt": q.prompt,
        "options": q.options, "image": q.image,
        "min": raw.get("min"), "max": raw.get("max"),
    }


def snapshot(g: Game | None, token: str | None) -> dict:
    now = time.time()
    if g is None:
        return {"phase": "none", "server_time": now, "build": BUILD_ID}

    me = g.player_by_token(token) if token else None
    my_state = g.state_for(me) if me else None
    answered, total = g.progress()

    roster = []
    for p in g.players.values():
        st = g.barrier.states.get(p.id) if g.barrier else None
        roster.append({
            "id": p.id, "name": p.name, "initial": p.initial,
            "selfie": bool(p.selfie_id), "photo_rev": p.photo_rev, "score": p.score,
            "answered": bool(st and st.answered),
            "seated": bool(st and st.seated),
        })

    r_no, r_of, q_no, q_of = g.progress_label()
    out = {
        "phase": g.phase.value,
        "build": BUILD_ID,
        # Say whether this phone belongs to the game in progress. Without it
        # the client just kept rendering whatever it last had, so a player left
        # behind by "Start a new game" sat looking at "You're in."
        "you_known": me is not None,
        "paused": g.paused,
        "progress_label": {"round": r_no, "rounds": r_of,
                           "question": q_no, "questions": q_of},
        "server_time": now,
        "pin": g.pin,
        "round": {"index": g.round_index, "total": g.total_rounds},
        "progress": {"answered": answered, "total": total},
        "players": roster,
        "leaderboard": [
            {"id": p.id, "name": p.name, "initial": p.initial,
             "selfie": bool(p.selfie_id), "score": p.score}
            for p in g.leaderboard()
        ],
        "winners": g.winner_ids() if g.phase is Phase.FINAL else [],
        "timer_seconds": g.timer_seconds,
        "reveal_seconds": g.reveal_seconds,
    }

    if g.phase is Phase.QUESTION:
        out["question"] = question_payload(g)

        # How long until the room can move on: the latest deadline still
        # outstanding, capped by the ceiling. Her own clock is spent by the
        # time she is looking at this, so it is the only number that means
        # anything on the waiting screen.
        if g.barrier is not None:
            outstanding = [st for st in g.barrier.active() if not st.answered]
            if outstanding:
                latest = max(
                    (st.deadline if st.deadline is not None else g.barrier.ceiling)
                    for st in outstanding
                )
                out["waiting_until"] = min(latest, g.barrier.ceiling)
        if my_state:
            out["you_deadline"] = my_state.deadline
            out["you_answered"] = my_state.answered
            out["you_seated"] = my_state.seated

    if g.phase is Phase.REVEAL and g.last_reveal:
        out["question"] = question_payload(g)
        out["reveal"] = {
            "correct": g.last_reveal["correct"],
            "reason": g.last_reveal["reason"],
            "your_points": g.last_reveal["deltas"].get(me.id, 0) if me else 0,
            "deltas": g.last_reveal["deltas"],
        }
        out["reveal_until"] = g.reveal_until

    if me:
        out["you"] = {"id": me.id, "name": me.name, "score": me.score,
                      "initial": me.initial, "selfie": bool(me.selfie_id),
                      "photo_rev": me.photo_rev}
    return out


async def broadcast() -> None:
    for q in list(subscribers):
        # Bounded and drop-oldest: a phone on bad LTE must never make the
        # server queue stale frames. Every payload is a full snapshot, so the
        # newest one alone is always enough to render correct truth.
        while q.qsize() > 1:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            q.put_nowait(True)
        except asyncio.QueueFull:
            pass


async def ticker() -> None:
    last = None
    while True:
        await asyncio.sleep(TICK_SECONDS)
        if game is None:
            continue
        try:
            game.tick(time.time())
        except Exception:
            pass
        fingerprint = (
            game.phase, game.question_index, game.round_index,
            game.progress(), tuple(p.score for p in game.players.values()),
            len(game.players),
        )
        if fingerprint != last:
            last = fingerprint
            persist.save(game)
            await broadcast()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not ADMIN_PASSWORD:
        raise RuntimeError(
            "BARGAMES_ADMIN_PASSWORD is not set. Refusing to start: without it "
            "anyone who can reach the app could run the game. Set it in .env."
        )
    app.state.started_at = time.time()

    # Chain, do not replace: uvicorn installed its own SIGTERM handler before
    # lifespan startup runs, and clobbering it would break shutdown entirely.
    # call_soon_threadsafe because a signal handler must not touch an
    # asyncio.Event directly.
    loop = asyncio.get_running_loop()
    previous_handler = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum, frame):
        loop.call_soon_threadsafe(SHUTTING_DOWN.set)
        if callable(previous_handler):
            previous_handler(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        pass   # not the main thread (e.g. TestClient); the backstop still applies

    # Come back from a restart with the game intact. The worst failure at the
    # bar is this container bouncing mid-question in front of thirty people.
    global game
    restored = persist.load(load_questions())
    if restored is not None:
        game = restored

    task = asyncio.create_task(ticker())
    try:
        selfies.purge_older_than()
    except Exception:
        pass
    yield
    task.cancel()


app = FastAPI(title="bargames", lifespan=lifespan)


@app.middleware("http")
async def no_stale_shell(request: Request, call_next):
    """The HTML shell must never come from a cache.

    Static assets are fingerprinted and cached hard, which is right. The pages
    that reference them must not be, or a phone can hold an old shell pointing
    at old assets — which is how a stale build keeps coming back.
    """
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    # Theme art is a photograph of a real person and is public by necessity —
    # it has to load before anyone has joined. Keep it out of search indexes.
    if request.url.path.startswith("/static/theme/"):
        response.headers["X-Robots-Tag"] = "noindex, noimageindex, nofollow"
    return response
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


# ------------------------------------------------------------------- helpers

def token_of(request: Request) -> str | None:
    return request.query_params.get("t") or request.cookies.get("bg_session")


def require_player(request: Request):
    if game is None:
        raise HTTPException(404, "no game")
    p = game.player_by_token(token_of(request))
    if p is None:
        raise HTTPException(403, "unknown player")
    return p


def require_admin(request: Request) -> None:
    if request.cookies.get("bg_admin") != ADMIN_SECRET:
        raise HTTPException(403, "not the organizer")


def set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        "bg_session", token, httponly=True, secure=COOKIE_SECURE,
        samesite="lax", path=COOKIE_PATH, max_age=60 * 60 * 12,
    )


# -------------------------------------------------------------------- routes

@app.get("/guide", response_class=HTMLResponse)
async def guide(request: Request):
    """The organizer's one-page guide, rendered.

    Public on purpose — it is instructions, not secrets, and whoever is
    running the game should be able to open it on any phone without signing
    in. It points at the admin page but contains no password.
    """
    import markdown as md

    source = BASE / "RUNBOOK.md"
    if not source.is_file():                      # not copied into the image
        source = BASE.parent / "docs" / "RUNBOOK.md"
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(404, "guide not available")

    body = md.markdown(text, extensions=["tables", "sane_lists"])
    b = branding()
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Running the game — {b['title']}</title>
<link rel="stylesheet" href="static/style.css?v={ASSET_VERSION}">
</head>
<body class="guide-body" data-theme="{b['theme']}">
<article class="guide">{body}</article>
<p class="version">{BUILD_ID}</p>
</body></html>""")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "uptime_s": round(time.time() - app.state.started_at, 1),
            "phase": game.phase.value if game else "none",
            "players": len(game.players) if game else 0}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "join.html", {"pin": "", "b": branding(), "v": ASSET_VERSION, "build": BUILD_ID})


@app.get("/j/{pin}", response_class=HTMLResponse)
async def join_link(request: Request, pin: str):
    return templates.TemplateResponse(request, "join.html", {"pin": pin[:4], "b": branding(), "v": ASSET_VERSION, "build": BUILD_ID})


@app.get("/api/pin/{pin}")
async def api_pin_check(pin: str):
    """Does this PIN match tonight's game?

    Lets the first screen reject a mistyped PIN immediately instead of after
    the guest has also entered a name and a photo. It reveals no more than
    POST /api/join already does — a wrong PIN there says so too.
    """
    return {"ok": game is not None and pin.strip() == game.pin}


@app.post("/api/join")
async def api_join(
    pin: str = Form(...), name: str = Form(...), selfie: UploadFile | None = File(None)
):
    if game is None or pin.strip() != game.pin:
        raise HTTPException(400, "That PIN does not match a game.")
    if len(game.players) >= 60:
        raise HTTPException(400, "This game is full.")

    selfie_id = None
    if selfie is not None:
        raw = await selfie.read()
        if raw:
            if len(raw) > 12 * 1024 * 1024:
                raise HTTPException(400, "That photo is too large.")
            try:
                selfie_id = selfies.store(raw)
            except Exception:
                selfie_id = None   # a bad photo must never block joining

    player = game.join(name, now=time.time(), selfie_id=selfie_id)
    persist.save(game)
    resp = JSONResponse({"token": player.token, "id": player.id})
    set_session_cookie(resp, player.token)
    await broadcast()
    return resp


@app.post("/api/profile")
async def api_profile(
    request: Request, name: str = Form(...), selfie: UploadFile | None = File(None)
):
    """Fix your name, or add/replace your photo, from the lobby.

    Before this, someone who mistyped her name or skipped the selfie and
    changed her mind had no way back except joining again as a new player on
    zero.
    """
    player = require_player(request)
    if game.phase is not Phase.LOBBY:
        raise HTTPException(409, "You can only change this before the game starts.")

    cleaned = name.strip()[:24]
    if cleaned:
        player.name = cleaned

    if selfie is not None:
        raw = await selfie.read()
        if raw:
            if len(raw) > 12 * 1024 * 1024:
                raise HTTPException(400, "That photo is too large.")
            try:
                new_id = selfies.store(raw)
            except Exception:
                raise HTTPException(400, "Could not read that photo.")
            old_id = player.selfie_id
            player.selfie_id = new_id
            player.photo_rev += 1
            # Replaced faces do not linger on the volume.
            if old_id and old_id != new_id:
                selfies.delete_one(old_id)

    persist.save(game)
    await broadcast()
    return {"ok": True, "name": player.name, "selfie": bool(player.selfie_id)}


@app.get("/play", response_class=HTMLResponse)
async def play(request: Request):
    resp = templates.TemplateResponse(request, "play.html", {"b": branding(), "v": ASSET_VERSION, "build": BUILD_ID})
    # A resume link opened in a fresh browser — new phone, cleared tab — arrives
    # with a valid ?t= and no cookie, and without this every photo on the page
    # 403s even though the player is real.
    if game is not None:
        player = game.player_by_token(token_of(request))
        if player is not None:
            set_session_cookie(resp, player.token)
    return resp


@app.get("/api/state")
async def api_state(request: Request):
    return snapshot(game, token_of(request))


@app.post("/api/rendered")
async def api_rendered(request: Request):
    player = require_player(request)
    if game.phase is Phase.QUESTION:
        game.mark_rendered(player, now=time.time())
    # Hand the deadline straight back. Setting it changes no shared state, so
    # it triggers no broadcast — without this the phone that just started its
    # own clock has no way to learn when it ends until somebody else answers.
    state = game.state_for(player) if game.phase is Phase.QUESTION else None
    return {"ok": True,
            "deadline": state.deadline if state else None,
            "server_time": time.time()}


@app.post("/api/answer")
async def api_answer(request: Request):
    player = require_player(request)
    body = await request.json()
    try:
        game.submit(player, body.get("answer"), now=time.time())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    game.tick(time.time())
    persist.save(game)
    await broadcast()
    return {"ok": True}


@app.get("/api/stream")
async def api_stream(request: Request):
    token = token_of(request)
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    subscribers.add(queue)

    async def gen():
        try:
            yield f"data: {json.dumps(snapshot(game, token))}\n\n"
            last_beat = time.time()
            while True:
                if await request.is_disconnected() or SHUTTING_DOWN.is_set():
                    break

                # Race the next update against shutdown. Waiting on the queue
                # alone is what made SIGTERM hang forever: during a graceful
                # shutdown the CLIENT has not disconnected — the server is the
                # one going away — so this loop would happily heartbeat while
                # uvicorn waited for a connection that by design never ends.
                getter = asyncio.ensure_future(queue.get())
                stopper = asyncio.ensure_future(SHUTTING_DOWN.wait())
                done, _ = await asyncio.wait(
                    {getter, stopper},
                    timeout=SSE_HEARTBEAT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in (getter, stopper):
                    if not task.done():
                        task.cancel()

                if stopper in done:
                    break
                if getter in done:
                    yield f"data: {json.dumps(snapshot(game, token))}\n\n"
                else:
                    # Keeps bytes moving across a silent answer window so no
                    # idle timeout in the chain can kill the game.
                    yield f": beat {int(time.time() - last_beat)}\n\n"
        finally:
            subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })


@app.get("/selfie/{selfie_id}")
async def selfie(request: Request, selfie_id: str):
    """Fail-closed. See docs/HOW-IT-WORKS.md section 6.

    No valid same-game session, no photo — no exceptions, no body, no hint
    about whether the id existed. Note the route carries NO file extension:
    CDNs such as Cloudflare cache by extension by default, and an edge-cached selfie would
    outlive every other control here.
    """
    if game is None:
        raise HTTPException(403)

    # Anyone inside tonight's game may see the faces: any player, and the
    # organizer, who watches the bubble room to see who is holding up the
    # question but holds an admin session rather than a player token.
    viewer_is_player = game.player_by_token(token_of(request)) is not None
    viewer_is_organizer = request.cookies.get("bg_admin") == ADMIN_SECRET
    if not (viewer_is_player or viewer_is_organizer):
        raise HTTPException(403)

    # The path segment is the PLAYER id, not the storage id. The storage id is
    # deliberately never published, so it is resolved here — which also means
    # a photo cannot be requested by guessing filenames.
    subject = game.players.get(selfie_id)
    data = selfies.read(subject.selfie_id) if subject and subject.selfie_id else None
    if data is None:
        raise HTTPException(403)
    return Response(content=data, media_type="image/jpeg", headers={
        "Cache-Control": "no-store, private, max-age=0",
        "Vary": "Cookie",
        "X-Robots-Tag": "noindex, nofollow",
    })


# --------------------------------------------------------------------- admin

@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    return templates.TemplateResponse(request, "admin.html", {"b": branding(), "v": ASSET_VERSION, "build": BUILD_ID})


@app.post("/api/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    ip = _client_ip(request)
    now = time.time()

    # Checked BEFORE the password comparison, so a lockout is a real lockout
    # and not just a slower oracle for whoever is guessing.
    if _admin_locked(ip, now):
        raise HTTPException(429, "Too many attempts. Wait a minute.")

    if not secrets.compare_digest(password, ADMIN_PASSWORD):
        count, _ = _admin_failures.get(ip, (0, now))
        _admin_failures[ip] = [count + 1, now]
        raise HTTPException(403, "Wrong password.")

    _admin_failures.pop(ip, None)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("bg_admin", ADMIN_SECRET, httponly=True, secure=COOKIE_SECURE,
                    samesite="lax", path=COOKIE_PATH, max_age=60 * 60 * 12)
    return resp


@app.post("/api/admin/create")
async def admin_create(request: Request):
    require_admin(request)
    global game
    body = await request.json()
    game = new_game(
        timer=float(body.get("timer_seconds", 15)),
        per_round=int(body.get("questions_per_round", 5)),
        reveal=float(body.get("reveal_seconds", 5)),
    )
    persist.save(game)
    await broadcast()
    return {"ok": True, "pin": game.pin}


@app.post("/api/admin/start")
async def admin_start(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    try:
        game.start(now=time.time())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    persist.save(game)
    await broadcast()
    return {"ok": True}


@app.post("/api/admin/end_question")
async def admin_end_question(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    game.end_question(now=time.time())
    persist.save(game)
    await broadcast()
    return {"ok": True}


@app.post("/api/admin/next_round")
async def admin_next_round(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    try:
        game.next_round(now=time.time())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    persist.save(game)
    await broadcast()
    return {"ok": True}


@app.post("/api/admin/pause")
async def admin_pause(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    body = await request.json()
    if bool(body.get("paused", True)):
        game.pause(now=time.time())
    else:
        game.resume(now=time.time())
    persist.save(game)
    await broadcast()
    return {"ok": True, "paused": game.paused}


@app.post("/api/admin/next_question")
async def admin_next_question(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    try:
        game.next_question(now=time.time())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    persist.save(game)
    await broadcast()
    return {"ok": True}


@app.post("/api/admin/reset")
async def admin_reset(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    game.reset(now=time.time())
    persist.save(game)
    await broadcast()
    return {"ok": True}


@app.post("/api/admin/remove_player")
async def admin_remove_player(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    body = await request.json()
    removed = game.remove_player(str(body.get("player_id", "")))
    if removed is None:
        raise HTTPException(404, "no such player")
    if removed.selfie_id:
        selfies.delete_one(removed.selfie_id)
    # Her removal may be what the barrier was waiting for.
    game.tick(time.time())
    persist.save(game)
    await broadcast()
    return {"ok": True, "name": removed.name}


@app.post("/api/admin/delete_selfies")
async def admin_delete_selfies(request: Request):
    require_admin(request)
    removed = selfies.delete_all()
    if game is not None:
        for p in game.players.values():
            p.selfie_id = None
        persist.save(game)
    await broadcast()
    return {"ok": True, "removed": removed}


@app.get("/api/admin/state")
async def admin_state(request: Request):
    require_admin(request)
    data = snapshot(game, None)
    data["is_admin"] = True
    # Only the organizer ever sees the review queue — it contains other
    # players' answers, which would be a live answer key on a player's phone.
    data["flagged"] = list(game.flagged) if game else []
    return data


@app.post("/api/admin/review")
async def admin_review(request: Request):
    require_admin(request)
    if game is None:
        raise HTTPException(400, "no game")
    body = await request.json()
    changed = game.review(
        body.get("player_id", ""), body.get("question_id", ""),
        bool(body.get("accept", True)),
    )
    if changed:
        persist.save(game)
        await broadcast()
    return {"ok": True, "changed": changed}
