"""The game state machine.

One Game object per party. Lives in memory in a single process (see
docs/HOW-IT-WORKS.md section 5) and is the authority on everything: whose clock
is running, who has answered, when the barrier releases, what everyone scored.

Every method that cares about time takes `now` explicitly rather than reading
the clock. That is what makes the barrier's nasty cases — a phone that renders
late, a phone that never renders, an answer one tick past the buzzer —
reproducible in a unit test instead of only at the bar.
"""

import random
import secrets
from dataclasses import dataclass, field
from enum import Enum

from engine import (
    CEILING_GRACE_SECONDS,
    needs_review,
    PlayerAnswerState,
    Question,
    QuestionBarrier,
    accuracy_of,
    consensus_points,
    deadline_for,
    points_for,
    speed_fraction,
)


class Phase(str, Enum):
    LOBBY = "lobby"
    QUESTION = "question"
    REVEAL = "reveal"
    ROUND_LEADERBOARD = "round_leaderboard"
    FINAL = "final"


@dataclass
class Player:
    id: str
    name: str
    token: str
    score: int = 0
    selfie_id: str | None = None
    # Bumped whenever the photo changes. Other phones need a signal they can
    # see: the photo URL is keyed on the player id, so without this neither the
    # bubble nor the browser has any reason to fetch a new image.
    photo_rev: int = 0
    joined_at: float = 0.0

    @property
    def initial(self) -> str:
        return (self.name.strip()[:1] or "?").upper()


@dataclass
class Game:
    questions: list[Question]
    timer_seconds: float = 15.0
    questions_per_round: int = 8
    reveal_seconds: float = 5.0
    now: float = 0.0

    pin: str = field(default_factory=lambda: f"{random.randint(0, 9999):04d}")
    phase: Phase = Phase.LOBBY
    players: dict[str, Player] = field(default_factory=dict)
    round_index: int = 0
    question_index: int = 0          # absolute index into self.questions
    barrier: QuestionBarrier | None = None
    reveal_until: float = 0.0
    last_reveal: dict | None = None
    # Near-miss free-text answers awaiting the organizer's judgement. Scored
    # generously already; she can revoke.
    flagged: list[dict] = field(default_factory=list)

    # Organizer has stopped the room. Every deadline is an absolute timestamp,
    # so pausing is not a flag you can check at the edges — the timestamps
    # themselves have to move on resume, or everyone silently loses the wall
    # time the game was stopped for.
    paused: bool = False
    paused_at: float = 0.0

    # ---- lookups -------------------------------------------------------

    def player_by_token(self, token: str) -> Player | None:
        return next((p for p in self.players.values() if p.token == token), None)

    def state_for(self, player: Player) -> PlayerAnswerState:
        if self.barrier is None:
            return PlayerAnswerState(player_id=player.id)
        return self.barrier.states.setdefault(
            player.id, PlayerAnswerState(player_id=player.id)
        )

    @property
    def current_question(self) -> Question | None:
        if self.question_index >= len(self.questions):
            return None
        return self.questions[self.question_index]

    @property
    def total_rounds(self) -> int:
        if not self.questions:
            return 0
        return -(-len(self.questions) // self.questions_per_round)

    # ---- joining -------------------------------------------------------

    def join(self, name: str, now: float, selfie_id: str | None = None) -> Player:
        pid = secrets.token_urlsafe(6)
        player = Player(
            id=pid,
            name=name.strip()[:24] or "Anonymous",
            token=secrets.token_urlsafe(16),
            selfie_id=selfie_id,
            photo_rev=1 if selfie_id else 0,
            joined_at=now,
        )
        self.players[pid] = player

        # Joining mid-question seats her for the next one. Dropping her into a
        # question already in flight would either hand her a stub of a clock or
        # extend everyone else's.
        if self.phase is Phase.QUESTION and self.barrier is not None:
            self.barrier.states[pid] = PlayerAnswerState(player_id=pid, seated=True)
        return player

    # ---- running a question -------------------------------------------

    def start(self, now: float) -> None:
        if self.phase is not Phase.LOBBY:
            raise ValueError("game already started")
        if not self.players:
            # Opening a question nobody can answer is how the deck gets burned
            # (see QuestionBarrier.released_at). Refuse with something the
            # organizer can act on instead.
            raise ValueError("Nobody has joined yet.")
        self._open_question(now)

    def _open_question(self, now: float) -> None:
        self.phase = Phase.QUESTION
        self.barrier = QuestionBarrier(
            opened_at=now,
            timer_seconds=self.timer_seconds,
            ceiling_grace=CEILING_GRACE_SECONDS,
            states={pid: PlayerAnswerState(player_id=pid) for pid in self.players},
        )

    def mark_rendered(self, player: Player, now: float) -> None:
        """This phone has the question on screen — start ITS clock."""
        st = self.state_for(player)
        if st.seated or st.deadline is not None or self.barrier is None:
            return
        st.deadline = deadline_for(
            now, self.barrier.opened_at, self.timer_seconds, self.barrier.ceiling_grace
        )

    def submit(self, player: Player, answer, now: float) -> None:
        if self.paused:
            raise ValueError("the game is paused")
        if self.phase is not Phase.QUESTION or self.barrier is None:
            raise ValueError("no question is open")
        st = self.state_for(player)
        if st.seated:
            raise ValueError("seated for the next question")
        if st.answered:
            raise ValueError("already answered")
        if st.deadline is None:
            # Answering without having rendered: treat the submission itself as
            # the render so she still gets scored rather than silently dropped.
            self.mark_rendered(player, now)
            st = self.state_for(player)
        if now > st.deadline:
            raise ValueError("too late")
        st.submitted_at = now
        st.answer = answer

    def end_question(self, now: float) -> None:
        """Organizer override — score what is in and move on."""
        if self.barrier is not None:
            self.barrier.admin_ended = True
        self.tick(now)

    def progress(self) -> tuple[int, int]:
        return self.barrier.progress() if self.barrier else (0, 0)

    def progress_label(self) -> tuple[int, int, int, int]:
        """(round, of rounds, question, of questions in this round), 1-based."""
        per = self.questions_per_round
        round_no = self.round_index + 1
        question_no = (self.question_index % per) + 1
        remaining = len(self.questions) - (self.round_index * per)
        in_round = max(1, min(per, remaining))
        return round_no, self.total_rounds, question_no, in_round

    # ---- organizer controls -------------------------------------------

    def pause(self, now: float) -> None:
        if self.paused:
            return
        self.paused = True
        self.paused_at = now

    def resume(self, now: float) -> None:
        """Hand back exactly the time the room was stopped for."""
        if not self.paused:
            return
        delta = max(0.0, now - self.paused_at)
        self.paused = False
        self.paused_at = 0.0

        if self.barrier is not None:
            self.barrier.opened_at += delta          # moves the ceiling with it
            for st in self.barrier.states.values():
                if st.deadline is not None:
                    st.deadline += delta
        if self.reveal_until:
            self.reveal_until += delta

    def next_question(self, now: float) -> None:
        """Skip to the next question, in one tap.

        Distinct from end_question: END stops the clock and shows the room the
        answer; NEXT moves straight on past it. Stopping at the reveal here
        made the two buttons do the same thing and took two taps to advance.

        Answers already submitted still score — skipping the reveal skips the
        display of it, not the points.
        """
        if self.paused:
            self.resume(now)

        if self.phase is Phase.QUESTION:
            self.end_question(now)          # scores what is in -> REVEAL
            if self.phase is Phase.REVEAL:  # then straight past the reveal
                self._after_reveal(now)
        elif self.phase is Phase.REVEAL:
            self._after_reveal(now)
        else:
            raise ValueError("no question to move on from")

    def reset(self, now: float) -> None:
        """Back to the lobby, everyone on zero — but still in the room.

        Players and the PIN are kept deliberately: a reset mid-party otherwise
        means thirty people re-typing a PIN.
        """
        self.phase = Phase.LOBBY
        self.round_index = 0
        self.question_index = 0
        self.barrier = None
        self.reveal_until = 0.0
        self.last_reveal = None
        self.flagged = []
        self.paused = False
        self.paused_at = 0.0
        for player in self.players.values():
            player.score = 0

    # ---- the clock -----------------------------------------------------

    def tick(self, now: float) -> None:
        """Advance the machine. Safe and cheap to call as often as you like."""
        self.now = now
        if self.paused:
            # Nothing moves while the room is stopped: not the barrier, not the
            # reveal hold. Deadlines are caught up in resume().
            return

        if self.phase is Phase.QUESTION and self.barrier is not None:
            reason = self.barrier.released_at(now)
            if reason is not None:
                self._reveal(now, reason)

        elif self.phase is Phase.REVEAL and now >= self.reveal_until:
            self._after_reveal(now)

    def _reveal(self, now: float, reason: str) -> None:
        question = self.current_question
        deltas: dict[str, int] = {}

        if question.type == "pick_player":
            picks = {
                pid: getattr(st, "answer", None)
                for pid, st in self.barrier.states.items()
                if not st.seated
            }
            deltas = consensus_points(picks)
        else:
            for pid, st in self.barrier.states.items():
                if st.seated:
                    continue
                answer = getattr(st, "answer", None)
                if answer is None:
                    deltas[pid] = 0
                    continue
                elapsed = st.submitted_at - (st.deadline - self.timer_seconds)
                deltas[pid] = points_for(
                    accuracy_of(question, answer),
                    speed_fraction(elapsed, self.timer_seconds),
                )
                if needs_review(question, answer):
                    self.flagged.append({
                        "player_id": pid,
                        "name": self.players[pid].name,
                        "question_id": question.id,
                        "prompt": question.prompt,
                        "given": answer,
                        "points": deltas[pid],
                        "accepted": True,
                    })

        for pid, delta in deltas.items():
            self.players[pid].score += delta

        self.phase = Phase.REVEAL
        self.reveal_until = now + self.reveal_seconds
        self.last_reveal = {
            "question_id": question.id,
            "reason": reason,
            "correct": question.correct,
            "deltas": deltas,
        }

    def _after_reveal(self, now: float) -> None:
        self.question_index += 1
        end_of_round = self.question_index % self.questions_per_round == 0
        out_of_questions = self.question_index >= len(self.questions)

        if out_of_questions:
            # The game ends itself. Stopping on a round leaderboard here meant
            # the organizer had to press "Next round" to finish a game that was
            # already over — a button whose label was wrong at the only moment
            # it mattered.
            self.phase = Phase.FINAL
            return

        if end_of_round:
            # Mid-game rounds DO wait for her: the room wants a moment to look
            # at the leaderboard.
            self.phase = Phase.ROUND_LEADERBOARD
            return
        self._unseat_everyone()
        self._open_question(now)

    def _unseat_everyone(self) -> None:
        for st in (self.barrier.states.values() if self.barrier else []):
            st.seated = False

    def next_round(self, now: float) -> None:
        """Organizer moves the room on from the round leaderboard."""
        if self.phase is not Phase.ROUND_LEADERBOARD:
            raise ValueError("not at a round leaderboard")
        if self.question_index >= len(self.questions):
            self.phase = Phase.FINAL
            return
        self.round_index += 1
        self._unseat_everyone()
        self._open_question(now)

    def remove_player(self, player_id: str) -> Player | None:
        """Take someone out of the room entirely.

        Also drops her from the open question's barrier — otherwise removing
        the person everyone is waiting on would achieve nothing, which is the
        main reason to reach for this.
        """
        player = self.players.pop(player_id, None)
        if player is None:
            return None
        if self.barrier is not None:
            self.barrier.states.pop(player_id, None)
        self.flagged = [f for f in self.flagged if f["player_id"] != player_id]
        return player

    def review(self, player_id: str, question_id: str, accept: bool) -> bool:
        """Organizer's verdict on one flagged answer. Idempotent.

        Re-scoring is a delta against what was actually awarded, not a
        recompute, so her decision cannot drift from the number the player
        already saw on the reveal.
        """
        for entry in self.flagged:
            if entry["player_id"] == player_id and entry["question_id"] == question_id:
                if entry["accepted"] == accept:
                    return False
                player = self.players.get(player_id)
                if player is None:
                    return False
                player.score += entry["points"] if accept else -entry["points"]
                entry["accepted"] = accept
                return True
        return False

    # ---- display -------------------------------------------------------

    def winner_ids(self) -> list[str]:
        """Everyone on the top score. Empty if nobody scored at all."""
        if not self.players:
            return []
        top = max(p.score for p in self.players.values())
        if top <= 0:
            return []
        return [p.id for p in self.players.values() if p.score == top]

    def leaderboard(self) -> list[Player]:
        return sorted(self.players.values(), key=lambda p: (-p.score, p.name))
