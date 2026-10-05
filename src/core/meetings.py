"""Meeting triggers, voting, ejections, and round resets."""
from __future__ import annotations
from collections import Counter
from typing import TYPE_CHECKING, Optional
from .models import Body
if TYPE_CHECKING:
    from .engine import Game


class MeetingSystem:
    def __init__(self) -> None:
        self.pending: Optional[tuple[str, Optional[Body]]] = None
        self.number = 0
        self.elapsed = 0.0
        self.duration = 0.0
        self.votes: dict[str, Optional[str]] = {}
        self.last_result: Optional[tuple[Optional[str], dict]] = None

    @property
    def remaining(self) -> float:
        return max(0.0, self.duration - self.elapsed)

    def call(self, game: "Game", reporter_id: str, body: Optional[Body] = None) -> bool:
        if not game.config.meetings_enabled or self.pending or game.outcome:
            return False
        reporter = game.entity(reporter_id)
        if not reporter.alive or reporter.vent_id is not None:
            return False
        if game.sabotage.kind:
            if body is None and game.sabotage.critical:
                return False
            game.sabotage.clear(game, reported=True)
        self.pending = (reporter_id, body)
        self.elapsed = 0.0
        self.duration = game.config.voting_seconds
        self.votes.clear()
        self.last_result = None
        if game.npc_system is not None:
            game.npc_system.start_meeting(game)
        for actor in game.players:
            game.tasks.cancel(game, actor.id)
            actor.vent_id = None
        game.emit("meeting_called", f"{reporter.name} called a meeting.", actor=reporter_id,
                  data={"body": body.victim_id if body else None,
                        "room": body.room if body else "Cafeteria",
                        "voting_seconds": self.duration})
        if game.npc_system is not None:
            game.npc_system.greet(game)
        return True

    def submit(self, game: "Game", voter_id: str, target: Optional[str]) -> bool:
        if (not self.pending or game.outcome or voter_id not in game.alive_ids()
                or voter_id in self.votes or (target is not None and target not in game.alive_ids())):
            return False
        self.votes[voter_id] = target
        return True

    def tick(self, game: "Game", dt: float) -> None:
        if not self.pending or game.outcome:
            return
        self.elapsed = min(self.duration, self.elapsed + dt)
        if game.npc_system is not None:
            game.npc_system.meeting_tick(game)
        if self.remaining <= 1e-9 or set(game.alive_ids()) <= self.votes.keys():
            # Missing ballots are skips, including players that time out.
            ballots = {actor: self.votes.get(actor) for actor in game.alive_ids()}
            self.resolve(game, ballots)

    def bot_vote(self, game: "Game", voter_id: str) -> Optional[str]:
        if (game.npc_system is None or not game.config.npc_ai_enabled
                or voter_id not in {actor.id for actor in game.npcs}):
            return None
        return game.npc_system.vote(game, voter_id)

    def resolve(self, game: "Game", votes: dict[str, Optional[str]]) -> tuple[Optional[str], dict]:
        if not self.pending:
            raise ValueError("There is no meeting to resolve")
        alive = game.alive_ids()
        if any(voter not in alive or (target is not None and target not in alive)
               for voter, target in votes.items()):
            raise ValueError("Votes must name living voters and living targets (or null to skip)")
        ballots = {actor: votes[actor] if actor in votes else self.bot_vote(game, actor) for actor in alive}
        counts = Counter(ballots.values())
        ranked = [(target, count) for target, count in counts.items() if target is not None]
        high = max((count for _, count in ranked), default=0)
        leaders = [target for target, count in ranked if count == high]
        ejected = leaders[0] if len(leaders) == 1 and high > counts.get(None, 0) else None
        if ejected:
            actor = game.entity(ejected)
            actor.alive = False
            actor.path = []
        self.number += 1
        self.pending = None
        game.bodies.clear()
        game.emit("meeting_resolved", f"{game.name_of(ejected)} was ejected." if ejected else "Nobody was ejected.",
                  target=ejected, data={"votes": ballots, "ejected": ejected})
        if ejected == game.player_id and game.config.end_on_player_death:
            game.lose(f"{game.player.name} was ejected by the crew.")
        else:
            game.check_parity()
        if not game.outcome:
            game.reset_after_meeting()
        self.last_result = (ejected, dict(counts))
        return self.last_result
