# bargames — UI contract

For ui-studio. The backend is built and tested (80 tests green). Restyle freely;
**keep the element ids and the data shape below** and your markup drops straight in.

## Files you own
`app/templates/join.html`, `app/templates/play.html`, `app/templates/admin.html`,
`app/static/style.css`, `app/static/app.js`, `app/static/admin.js`.
A working baseline of all six already exists — treat it as the functional
reference and replace the look, not the contract.

## How data arrives
- `GET api/state` -> the JSON snapshot below (also used on reconnect/visibility change).
- `GET api/stream` -> SSE. **Default event type**, so `es.onmessage`. Every message
  is a *full snapshot*, never a diff. Comment lines (`: beat`) are heartbeats — ignore.
- All URLs are **relative** (`api/state`, not `/api/state`). The app may be served
  under a sub-path by a proxy that strips it; an absolute path breaks there.

## Snapshot shape
```json
{
  "phase": "lobby|question|reveal|round_leaderboard|final|none",
  "server_time": 1758168000.5,
  "pin": "4821",
  "round": {"index": 0, "total": 3},
  "progress": {"answered": 17, "total": 24},
  "timer_seconds": 15.0,
  "you": {"id": "...", "name": "Alice", "score": 2400, "initial": "A", "selfie": true},
  "you_deadline": 1758168015.0,
  "you_answered": false,
  "you_seated": false,
  "question": {"id": "r1q1", "type": "multiple_choice", "prompt": "...",
               "options": ["a","b","c","d"], "image": "flag-1.png",
               "min": 0, "max": 100},
  "players":     [{"id","name","initial","selfie","score","answered","seated"}],
  "leaderboard": [{"id","name","initial","selfie","score"}],
  "reveal": {"correct": "...", "your_points": 850, "reason": "all_answered",
             "deltas": {"<player id>": 850}},
  "reveal_until": 1758168020.0
}
```
`question.correct` is **absent during play** and only appears under `reveal`. Do not
add any code path that expects it earlier.

## Required element ids

**play.html** — screens, exactly one visible at a time (toggle with `el.hidden`):
`#screen-lobby` `#screen-question` `#screen-waiting` `#screen-reveal`
`#screen-leaderboard` `#screen-final`

Inside them:
| id | purpose |
|---|---|
| `#timer-ring` `#timer-text` | countdown; driven from `you_deadline` minus server-clock offset |
| `#q-prompt` `#q-image` | question text and optional image (`selfie`-style route: `static/questions/<image>`) |
| `#q-options` | multiple choice — **2x2 grid**, four children, each `data-value` |
| `#q-truefalse` | two full-width buttons, `data-value="true"` / `"false"` |
| `#q-wheel` `#wheel-value` | number input (range) + live readout, bounded by `min`/`max` |
| `#q-text` `#q-text-submit` | free text |
| `#q-players` | pick_player grid, one child per other player, `data-value=<player id>` |
| `#bubbles` `#bubble-count` | the waiting room (below) |
| `#reveal-correct` `#reveal-points` | reveal |
| `#race` | horse race, one lane per player |
| `#leaderboard` `#final-leaderboard` | ranked lists |

**admin.html**: `#admin-login` `#admin-panel` `#cfg-timer` `#cfg-per-round`
`#cfg-reveal` `#btn-create` `#pin-display` `#join-link` `#roster` `#btn-start`
`#btn-end-question` `#btn-next-round` `#btn-delete-selfies` `#admin-bubbles` `#admin-race`
`#flagged-queue` `#flagged-list` `#panel-error`

`#panel-error` shows failures of the panel's own controls (e.g. tapping START
with an empty lobby -> "Nobody has joined yet."). It must live INSIDE
`#admin-panel`: `#admin-error` is inside `#admin-login`, which is hidden once she
is signed in, so a message written there is invisible.

`#flagged-queue` is the free-text review queue (docs/HOW-IT-WORKS.md section 7): near-miss
spellings that scored, which the organizer can revoke. It is `hidden` when empty,
and appears ONLY on the admin page — the payload contains other players' answers,
so on a player's phone it would be a live answer key. It arrives as `flagged` on
`api/admin/state` only, never on `api/state`.

**join.html** is a THREE-STEP wizard, one question per screen: `#step-pin` ->
`#step-name` -> `#step-selfie`, toggled with `el.hidden`, exactly one visible at a
time. `#join-error` is shared and sits outside the steps.
- `#step-pin`: `#pin-input` `#btn-pin-next`. The PIN is checked against
  `GET api/pin/<pin>` before she is asked for anything else, so a mistyped PIN is
  caught on screen one. No auto-advance on the fourth digit — it moved the screen
  out from under a travelling thumb; Enter works.
- `#step-name`: `#name-input` `#btn-name-next` `#btn-name-back`.
- `#step-selfie`: `#selfie-input` `#btn-join` `#btn-skip-selfie` `#btn-selfie-back`.
  Skip joins with no photo.
- `/j/<pin>` lands directly on `#step-name`.

**Player-facing copy must not use the racing metaphor** — no "horse", "saddle",
"gallop", "jockey" in anything a player reads, on any screen. It confuses a
first-time player who has not seen the leaderboard yet. Internal CSS class and JS
variable names are exempt and unchanged. Locked by `tests/test_join_copy.py`.

Legacy single-form ids, still present: `#pin-input` `#name-input` `#selfie-input`
`#btn-join` `#join-error`
`#admin-entry` `#admin-prompt` `#admin-prompt-input` `#admin-prompt-go` `#admin-prompt-error`

`#admin-entry` is the organizer's quiet way in from the public join page: a small
top-right icon button that toggles `#admin-prompt` (starts `hidden`). Correct
password -> navigate to `admin`; wrong -> inline message, stay put. Keep it
unobtrusive — it must never compete with the PIN field for a guest's thumb — but
keep the 44px tap target, the `aria-expanded` wiring and Escape/outside-click to
dismiss. The login is throttled server-side (5 failures -> 429 for 60s); surface
the 429 text rather than the generic wrong-password one.

## The two screens that matter most

**The bubble waiting room** (`#screen-waiting`) is what people stare at longest.
Every participant is a circular bubble: selfie via `selfie/<player id>` when
`selfie` is true, otherwise the initial on a coloured disc. Bubbles drift and bob
on looping CSS transforms with **staggered delays so they never move in unison**.
State must be encoded in **more than colour** — answered = full opacity, bright
ring, small check; still-thinking = desaturated, dimmed, slightly smaller, pulsing.
It has to read at a glance, across a table, in a dark bar, for someone colourblind.
`#bubble-count` reads "17 of 24 in".

**The horse race** (`#race`): one lane per player, the player's selfie or initial
disc as the horse's head, horizontal position = `score / leader_score`. Move with
`transform: translateX()` transitions only — 30 lanes must animate on the GPU.

## Constraints
- Phone-first, 390px and up. Thumb-sized tap targets; a tipsy person in a dark bar
  must not have to aim.
- No build step, no framework, no external fonts or CDNs. Plain CSS + vanilla JS.
- Selfie images are private: they load only from `selfie/<player id>` with the
  session cookie. Never copy them into canvas, localStorage, or a data URI.
