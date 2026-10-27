"""Built-in NPC controller: navigation, tasks, hunting, chat and voting.

Game mode only: `session.new_game` attaches it; simulation mode runs without it.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from ..core.models import EPSILON, Player, Pos
from ..core.world import TASK_POSITIONS, VENT_IDS, room_at
from ..core.systems.sabotage import PANELS
from ..core.systems.doors import DOOR_ROOMS

ROOM_IDS = {name: room_id for room_id, name in DOOR_ROOMS.items()}
VOTE_START, VOTE_SPREAD = 0.15, 0.60  # Ballots land between 15% and 75% of the voting phase.
SKIP_VOTE_CHANCE = 0.16
BASE_VOTE_WEIGHT = 0.7  # Weight of a candidate nobody suspects yet.
KEEP_GOAL_CHANCE = 0.55  # A wandering NPC keeps its current goal this often.
IMPOSTOR_RETHINK_SECONDS = 0.8
CREW_RETHINK_SECONDS = (1.5, 3.2)  # Random range between route re-plans.
if TYPE_CHECKING:
    from ..core.engine import Game


class NPCController:
    """Decides for every player in `game.npcs`; a `core.controller.Controller`."""

    def __init__(self) -> None:
        self.meeting_vote_times: dict[str, float] = {}
        self.greetings: set[tuple[str, int]] = set()

    def meeting_started(self, game: "Game") -> None:
        self.start_meeting(game)
        self.greet(game)

    def start_meeting(self, game: "Game") -> None:
        """Spread built-in decisions across the meeting, using the episode RNG."""
        self.meeting_vote_times.clear()
        if not game.config.npc_ai_enabled:
            return
        voters = [actor.id for actor in game.npcs if actor.alive]
        game.rng.shuffle(voters)
        for index, actor_id in enumerate(voters):
            fraction = VOTE_START + VOTE_SPREAD * (index + game.rng.uniform(0.1, 0.9)) / len(voters)
            # Ballots only open after the discussion; spread votes across the voting window.
            voting = game.meetings.duration - game.meetings.discussion
            self.meeting_vote_times[actor_id] = game.meetings.discussion + voting * fraction

    def greet(self, game: "Game") -> None:
        """Greet once per voting session; gameplay chat is read-only."""
        if not game.config.npc_ai_enabled or not game.pending_meeting:
            return
        for actor in game.npcs:
            key = (actor.id, game.meeting_number)
            if actor.alive and key not in self.greetings:
                if game.chat.send(game, actor.id, "Hello world"):
                    self.greetings.add(key)

    def meeting_tick(self, game: "Game") -> None:
        if not game.config.npc_ai_enabled:
            return
        for actor in game.npcs:
            if (actor.alive and actor.id not in game.meetings.votes
                    and game.meetings.elapsed + EPSILON >= self.meeting_vote_times.get(actor.id, game.meetings.duration)):
                game.meetings.submit(game, actor.id, self.vote(game, actor.id))

    def vote(self, game: "Game", voter_id: str) -> str | None:
        voter = game.get_player(voter_id)
        options = [player_id for player_id in game.alive_ids() if player_id != voter_id]
        if not options or game.rng.random() < SKIP_VOTE_CHANCE:
            return None
        if voter.role == "impostor":
            options = [i for i in options if game.get_player(i).role == "crew"] or options
        weights = [BASE_VOTE_WEIGHT + voter.suspicion.get(candidate, 0.0) for candidate in options]
        return game.rng.choices(options, weights=weights, k=1)[0]

    def sabotage_goal(self, game: "Game", actor_id: str) -> Pos | None:
        """Send only the crew a repair needs, each to a different panel."""
        sabotage = game.sabotage
        for assigned, index in list(sabotage.responders.items()):
            player_holding = sabotage.workers.get(game.player_id) == index
            if not game.get_player(assigned).alive or index in sabotage.completed or player_holding:
                sabotage.responders.pop(assigned)
                sabotage.cancel_worker(assigned)
        taken = set(sabotage.responders.values()) | set(sabotage.workers.values()) | sabotage.completed
        for index, panel in enumerate(sabotage.panels):
            if index in taken:
                continue
            candidates = [a for a in game.npcs if a.alive and a.role == "crew"
                          and a.id not in sabotage.responders and a.id not in sabotage.workers]
            if candidates:
                actor = min(candidates, key=lambda a: len(game.find_path(a.pos, panel.pos)) or 10000)
                sabotage.responders[actor.id] = index
                actor.path, actor.goal, actor.rethink_clock = [], None, 0.0
        index = sabotage.responders.get(actor_id)
        return sabotage.panels[index].pos if index is not None else None

    def choose_goal(self, game: "Game", actor: Player) -> Pos:
        if actor.role == "crew" and game.sabotage.kind:
            goal = self.sabotage_goal(game, actor.id)
            if goal is not None:
                return goal
        if actor.role == "impostor" and game.elapsed >= game.config.hunt_delay:
            # Targets must be in sight: the built-in hunter gets no unseen positions.
            victims = [other for other in game.players if other.alive and other.role == "crew"
                       and game.can_see(actor.id, other.pos)]
            if victims:
                return min(victims, key=lambda other: game.distance(actor.pos, other.pos)).pos
        if actor.role == "crew":
            state = game.tasks.states[actor.id]
            remaining = [pos for pos in state.assigned if pos not in state.completed]
            if remaining:
                return min(remaining, key=lambda pos: game.distance(actor.pos, pos))
        if actor.goal and actor.pos != actor.goal and game.rng.random() < KEEP_GOAL_CHANCE:
            return actor.goal
        return game.rng.choice(TASK_POSITIONS)

    def move(self, game: "Game", actor: Player) -> None:
        if not actor.alive or game.pending_meeting or game.outcome:
            return
        if actor.vent_id is not None:
            if actor.path and actor.path[0] == actor.pos:
                actor.path.pop(0)
            if actor.path and game.distance(actor.pos, actor.path[0]) > 1:
                game.vents.use(game, actor.id, VENT_IDS.get(actor.path[0]))
            else:
                game.vents.use(game, actor.id)
            return
        if actor.rethink_clock <= 0 or not actor.path:
            actor.goal = self.choose_goal(game, actor)
            actor.path = game.world.find_path(actor.pos, actor.goal, use_vents=actor.role == "impostor")
            actor.rethink_clock = (IMPOSTOR_RETHINK_SECONDS if actor.role == "impostor"
                                  else game.rng.uniform(*CREW_RETHINK_SECONDS))
        if actor.path and actor.path[0] == actor.pos:
            actor.path.pop(0)
        if actor.path:
            step = actor.path[0]
            if actor.role == "impostor" and game.distance(actor.pos, step) > 1:
                game.vents.use(game, actor.id)
                return
            if game.move(actor.id, step[0] - actor.pos[0], step[1] - actor.pos[1]):
                actor.path.pop(0)
            else:
                # Break traffic jams without teleporting or stacking players.
                free = [(dx, dy) for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))
                        if game.is_walkable((actor.pos[0] + dx, actor.pos[1] + dy))
                        and not game.occupied((actor.pos[0] + dx, actor.pos[1] + dy), actor.id)]
                if free:
                    game.move(actor.id, *game.rng.choice(free))
                actor.path = []

    def trap(self, game: "Game", impostor: Player) -> None:
        """Close the room's doors when a crewmate in sight shares it with the impostor.
        Door rules (room limit, cooldown, no doors during sabotage) still apply."""
        room = room_at(impostor.pos)
        room_id = ROOM_IDS.get(room)
        if room_id is None or impostor.vent_id is not None or room_id in game.doors.closed:
            return
        if any(other.alive and other.role == "crew" and other.vent_id is None
               and room_at(other.pos) == room and game.can_see(impostor.id, other.pos)
               for other in game.players):
            game.doors.close(game, impostor.id, room_id)

    def tick(self, game: "Game") -> None:
        if not game.config.npc_ai_enabled:
            return
        npc_sabotage = game.player.role != "impostor"
        if (npc_sabotage and not game.sabotage.kind and game.sabotage.cooldown <= EPSILON
                and game.config.sabotage_enabled):
            impostor = next((a for a in game.npcs if a.alive and a.role == "impostor"), None)
            if impostor is not None:
                game.sabotage.start(game, impostor.id, game.rng.choice(tuple(PANELS)))
        for actor in game.npcs:
            if npc_sabotage and actor.alive and actor.role == "impostor":
                self.trap(game, actor)
        for actor in game.npcs:
            if not actor.alive:
                continue
            if actor.role == "crew" and game.report_body(actor.id, automatic=True):
                return
            if actor.role == "crew" and game.sabotage.kind:
                goal = self.sabotage_goal(game, actor.id)
                if goal is not None:
                    game.tasks.cancel(game, actor.id)
                    if game.distance(actor.pos, goal) <= 1:
                        game.sabotage.interact(game, actor.id)
                    elif actor.move_clock <= 0:
                        self.move(game, actor)
                        actor.move_clock = game.move_interval(actor)
                    continue
            if actor.role == "crew" and game.tasks.start(game, actor.id):
                continue
            if actor.move_clock <= 0:
                self.move(game, actor)
                actor.move_clock = game.move_interval(actor)
            if actor.role == "impostor":
                game.kill(actor.id)
            if game.pending_meeting or game.outcome:
                return
