"""Crash recovery by whole-state snapshot.

An early design called for Postgres write-through. A single JSON snapshot
on the selfie volume buys the same thing — the game survives a container
restart — for a fraction of the code and with one fewer dependency at boot: if
Postgres were the recovery path and Postgres were down, the app would not start
at all, which is a worse failure than the one being defended against. Write
volume here is a few hundred bytes a second at most. See docs/HOW-IT-WORKS.md
section 5.
"""

import json
import os
import tempfile
from pathlib import Path

from engine import PlayerAnswerState, QuestionBarrier
from game import Game, Phase, Player

STATE_FILE = Path(os.getenv("BARGAMES_STATE_FILE", "/selfies/game-state.json"))


def save(game: Game) -> None:
    """Never raises. A failed snapshot must not take the game down with it."""
    try:
        data = {
            "pin": game.pin,
            "phase": game.phase.value,
            "timer_seconds": game.timer_seconds,
            "questions_per_round": game.questions_per_round,
            "reveal_seconds": game.reveal_seconds,
            "round_index": game.round_index,
            "question_index": game.question_index,
            "reveal_until": game.reveal_until,
            "paused": game.paused,
            "paused_at": game.paused_at,
            "last_reveal": game.last_reveal,
            "players": [
                {"id": p.id, "name": p.name, "token": p.token, "score": p.score,
                 "selfie_id": p.selfie_id, "photo_rev": p.photo_rev,
                 "joined_at": p.joined_at}
                for p in game.players.values()
            ],
            "barrier": None if game.barrier is None else {
                "opened_at": game.barrier.opened_at,
                "timer_seconds": game.barrier.timer_seconds,
                "ceiling_grace": game.barrier.ceiling_grace,
                "admin_ended": game.barrier.admin_ended,
                "states": [
                    {"player_id": s.player_id, "deadline": s.deadline,
                     "submitted_at": s.submitted_at, "seated": s.seated,
                     "answer": s.answer}
                    for s in game.barrier.states.values()
                ],
            },
        }
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a crash mid-write leaves the previous good
        # snapshot in place rather than a truncated file.
        fd, tmp = tempfile.mkstemp(dir=STATE_FILE.parent)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def load(questions: list) -> Game | None:
    try:
        data = json.loads(STATE_FILE.read_text())
    except Exception:
        return None

    try:
        game = Game(
            questions=questions,
            timer_seconds=data["timer_seconds"],
            questions_per_round=data["questions_per_round"],
            reveal_seconds=data.get("reveal_seconds", 5.0),
        )
        game.pin = data["pin"]
        game.phase = Phase(data["phase"])
        game.round_index = data["round_index"]
        game.question_index = data["question_index"]
        game.reveal_until = data.get("reveal_until", 0.0)
        game.paused = data.get("paused", False)
        game.paused_at = data.get("paused_at", 0.0)
        game.last_reveal = data.get("last_reveal")
        game.players = {
            p["id"]: Player(id=p["id"], name=p["name"], token=p["token"],
                            score=p["score"], selfie_id=p.get("selfie_id"),
                            photo_rev=p.get("photo_rev", 0),
                            joined_at=p.get("joined_at", 0.0))
            for p in data["players"]
        }
        b = data.get("barrier")
        if b:
            game.barrier = QuestionBarrier(
                opened_at=b["opened_at"], timer_seconds=b["timer_seconds"],
                ceiling_grace=b["ceiling_grace"], admin_ended=b["admin_ended"],
                states={
                    s["player_id"]: PlayerAnswerState(
                        player_id=s["player_id"], deadline=s["deadline"],
                        submitted_at=s["submitted_at"], seated=s["seated"],
                        answer=s.get("answer"))
                    for s in b["states"]
                },
            )
        return game
    except Exception:
        return None


def clear() -> None:
    try:
        STATE_FILE.unlink()
    except OSError:
        pass
