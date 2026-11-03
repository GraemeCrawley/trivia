# How it works

The design notes behind the code. Comments in `app/` and `tests/` point at the
numbered sections here.

## 1. What it is

A phone-first trivia game for a room full of people. The organizer runs it from
their phone. Up to ~30 players join from their own phones by typing a 4-digit PIN
or tapping a link. Everyone plays the same question at the same time, each on their
own countdown. Nobody's score moves until everybody has answered; then the answer is
revealed and the whole room scores at once.

## 2. Pacing: one question at a time, a timer per phone

Everyone is on the same question at the same time. Each phone runs **its own**
countdown, started when **that phone** rendered the question, so someone on slow
mobile data still gets the full time.

```
deadline_i = (when phone i rendered the question) + timer_seconds
ceiling    = (when the question opened)           + timer_seconds + 10s
```

`deadline_i` is clamped to `ceiling`, so one phone that never loads cannot hold
the room hostage.

**The question closes (the "barrier" releases) when any one of these is true:**

1. Every active player has answered, **or**
2. Every active player has answered or passed their own deadline, **or**
3. The organizer taps **End question**, **or**
4. The ceiling passes.

Then the server scores everyone at once, shows the reveal for `reveal_seconds`
(default 5), and moves to the next question on its own.

- A player who joins mid-question is **seated** and plays from the next question.
- A phone that locks or disconnects does not stall the room: the server holds its
  deadline and it passes on schedule.

## 3. The five question types

| Type | Player sees | Scored |
|---|---|---|
| `multiple_choice` | Four big buttons, optional image above | Exact match |
| `true_false` | Two buttons | Exact match |
| `number_wheel` | A drag-to-spin number dial | By closeness (`span`) |
| `free_text` | A text box | Fuzzy match; organizer can revoke close calls |
| `pick_player` | The other players; tap one | By agreement with the room |

## 4. Game flow

Organizer signs in → sets timer / questions per round → **New game** (gets a PIN)
→ players join (name + optional selfie) → **Start** → questions run one by one →
round leaderboard → **Next round** → … → final leaderboard.

## 5. Architecture

- **FastAPI + Jinja2 templates + plain JavaScript.** No build step.
- **Server-Sent Events** push a full state snapshot on every change; actions are plain
  POSTs. `GET api/state` re-syncs after a reload or reconnect. A heartbeat every 10s
  keeps proxies from closing idle streams.
- **The game lives in memory in exactly one process** (one uvicorn worker, pinned in
  the Dockerfile). A second worker would get its own copy of the game and silently
  split the room in two.
- **Crash recovery:** every state change is written to a JSON snapshot on the data
  volume (write-then-rename) and reloaded on boot, so a restart costs seconds, not the
  game.
- **Server-issued deadlines.** Phones only draw the countdown; the server rejects late
  answers.

## 6. Identity and selfie privacy

- On join the server mints a random player token, kept in `localStorage` and the
  address bar, so a refresh or reopened tab is the same player.
- Join also sets an HttpOnly `bg_session` cookie. Selfies are served only from
  `GET /selfie/<id>` (no file extension, so CDNs that cache by extension won't), and
  only to a valid session **in the same active game**. Anything else gets a bare `403`.
- Responses carry `Cache-Control: no-store, private` and `Vary: Cookie`.
- Photos live on the private data volume, never under `/static`, with random file
  names. Location data (EXIF) is stripped and images are shrunk to 256px on upload.
- Photos are deleted 24 hours after a game ends, or immediately from the admin panel.
- Known limit: the resume link contains the player's token, so anyone it is
  forwarded to can play as that player.

## 7. Scoring

```
points = round( accuracy x (600 + 400 x speed) )

accuracy : 1 or 0 for multiple_choice / true_false / free_text
           max(0, 1 - |guess - answer| / span) for number_wheel
speed    : share of that player's own clock still left when they answered (0-1)
```

A correct instant answer is 1000, a correct last-second answer is 600, wrong or
missing is 0. `pick_player` scores `round(1000 x share of players who picked the
same person)`.

**Free text:** answers are normalised (lowercase, no punctuation or leading "the/a")
and compared to each accepted answer. A similarity of 0.85+ scores. 0.70–0.85 scores
**and** shows up for the organizer under "Close calls" to revoke. Below 0.70 is wrong.

## 8. Content

Questions live in `app/seed/questions.json`; images in `app/static/questions/`.
Rounds are cut every `questions_per_round` questions (default 8). Title, badge and
colour theme live in `app/seed/branding.json`.

## 9. Screens

Join (PIN → name → optional selfie) → lobby → question → **waiting room** (everyone
as a floating bubble; lit = answered, dim = still thinking) → reveal → leaderboard.
The organizer's phone shows the same room plus the controls. See `UI-CONTRACT.md`
for the element ids the JavaScript depends on.

## 10. Testing

- `tests/` — fast unit and API tests (`pytest tests --ignore=tests/e2e`).
- `tests/e2e/` — real browsers (Playwright) driving up to thirty simulated phones,
  checking the timers, the barrier, scoring, selfie privacy and crash recovery.
