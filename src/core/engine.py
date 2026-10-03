"""Game coordinator. Tasks, NPC decisions, meetings, and event storage are separate systems."""
from __future__ import annotations
import math
import random
from collections import deque
from dataclasses import replace
from typing import Optional
from .config import GameConfig
from .events import EventLog
from .chat import ChatSystem
from .meetings import MeetingSystem
from .models import Action, Body, COLORS, MOVE_ACTIONS, Player, Pos
from ..npcs import NPCSystem
from .tasks import TaskSystem
from .vents import VentSystem
from .world import EMERGENCY_POS, MAP_H, MAP_W, SPAWNS, ShipMap, manhattan, room_at


class Game:
    def __init__(self, seed: Optional[int] = None, config: Optional[GameConfig] = None) -> None:
        self.config = config or GameConfig()
        if seed is not None:
            self.config = replace(self.config, seed=seed)
        if self.config.play_mode == "simulation":
            self.config = replace(self.config, npc_ai_enabled=False, end_on_player_death=False,
                                  task_win_mode="team" if self.config.task_win_mode == "player"
                                  else self.config.task_win_mode)
        self.rng = random.Random(self.config.seed)
        self.world = ShipMap()
        self.grid = self.world.grid
        chosen = next(color for color in COLORS if color.name == self.config.player_color)
        colors = [chosen] + [color for color in COLORS if color != chosen]
        self.players = [Player(color.name.lower(), color.name, color.symbol, color.ansi, SPAWNS[index],
                               kill_clock=self.config.initial_kill_cooldown,
                               emergencies_left=self.config.emergencies_per_player)
                        for index, color in enumerate(colors[:self.config.player_count])]
        self._entities = {actor.id: actor for actor in self.players}
        self.player = self.players[0]
        self.player_id = self.player.id
        self.npcs = self.players[1:] if self.config.play_mode == "game" else []
        pool = self.players if self.config.player_role == "random" else self.players[1:]
        impostors = []
        if self.config.player_role == "impostor":
            impostors.append(self.player)
        impostors += self.rng.sample(pool, self.config.impostor_count - len(impostors))
        for actor in impostors:
            actor.role = "impostor"
        self.impostor_ids = [actor.id for actor in impostors]
        self.events = EventLog(self.config.event_history)
        self.messages: deque[str] = deque(maxlen=5)
        self.chat = ChatSystem(self.config.chat_history, self.config.chat_max_length)
        self.tasks = TaskSystem(self)
        self.vents = VentSystem()
        self.npc_system = NPCSystem() if self.config.play_mode == "game" else None
        self.meetings = MeetingSystem()
        self.bodies: list[Body] = []
        self.elapsed = 0.0
        self.outcome: Optional[str] = None
        self.outcome_reason = ""
        self.winner: Optional[str] = None
        self.discovered_by = {actor.id: set() for actor in self.players}
        self.discovered = self.discovered_by[self.player_id]
        self.map_zoom: Optional[int] = None
        self.camera_pos: Pos = self.player.pos
        self.panel_visible = True
        self.observer_page = 0
        self.emit("game_started", "Systems online. Complete your tasks.",
                  data={"players": [actor.id for actor in self.players]})

    @property
    def player_pos(self) -> Pos:
        return self.player.pos

    @player_pos.setter
    def player_pos(self, value: Pos) -> None:
        self.player.pos = value

    @property
    def player_alive(self) -> bool:
        return self.player.alive

    @player_alive.setter
    def player_alive(self, value: bool) -> None:
        self.player.alive = value

    @property
    def assigned_tasks(self) -> list[Pos]:
        return self.tasks.states[self.player_id].assigned

    @property
    def completed_tasks(self) -> set[Pos]:
        return self.tasks.states[self.player_id].completed

    @property
    def active_task(self) -> Optional[Pos]:
        return self.tasks.states[self.player_id].active

    @active_task.setter
    def active_task(self, value: Optional[Pos]) -> None:
        self.tasks.states[self.player_id].active = value

    @property
    def task_progress(self) -> float:
        return self.tasks.states[self.player_id].progress

    @property
    def impostor_id(self) -> Optional[str]:
        return self.impostor_ids[0] if self.impostor_ids else None

    @property
    def kill_cooldown(self) -> float:
        return min((self.entity(i).kill_clock for i in self.impostor_ids), default=0.0)

    @kill_cooldown.setter
    def kill_cooldown(self, value: float) -> None:
        for actor_id in self.impostor_ids:
            self.entity(actor_id).kill_clock = value

    @property
    def emergency_available(self) -> bool:
        return self.player.emergencies_left > 0 and self.config.meetings_enabled

    @property
    def pending_meeting(self) -> Optional[tuple[str, Optional[Body]]]:
        return self.meetings.pending

    @pending_meeting.setter
    def pending_meeting(self, value: Optional[tuple[str, Optional[Body]]]) -> None:
        self.meetings.pending = value

    @property
    def meeting_number(self) -> int:
        return self.meetings.number

    @staticmethod
    def distance(a: Pos, b: Pos) -> int:
        return manhattan(a, b)

    def entity(self, entity_id: str) -> Player:
        if entity_id not in self._entities:
            raise ValueError(f"Unknown player ID: {entity_id!r}")
        return self._entities[entity_id]

    def npc(self, npc_id: str) -> Player:
        return self.entity(npc_id)

    def alive_npcs(self) -> list[Player]:
        return [actor for actor in self.npcs if actor.alive]

    def alive_ids(self) -> list[str]:
        return [actor.id for actor in self.players if actor.alive]

    def name_of(self, entity_id: str) -> str:
        return self.entity(entity_id).name

    def pos_of(self, entity_id: str) -> Pos:
        return self.entity(entity_id).pos

    def is_walkable(self, pos: Pos) -> bool:
        return self.world.is_walkable(pos)

    def occupied(self, pos: Pos, except_id: Optional[str] = None) -> bool:
        return any(actor.alive and actor.vent_id is None and actor.id != except_id and actor.pos == pos
                   for actor in self.players)

    def emit(self, kind: str, message: str, **kwargs):
        event = self.events.emit(self.elapsed, kind, message, **kwargs)
        if message and (event.visible_to is None or self.player_id in event.visible_to):
            self.messages.appendleft(message)
        return event

    def message(self, text: str) -> None:
        self.emit("message", text, visible_to=[self.player_id])

    def move_interval(self, actor: Player) -> float:
        if actor.id == self.player_id and self.config.play_mode == "game":
            return self.config.player_move_seconds
        return self.config.impostor_move_seconds if actor.role == "impostor" else self.config.crew_move_seconds

    def move_entity(self, player_id: str, dx: int, dy: int) -> bool:
        actor = self.entity(player_id)
        if (not actor.alive or actor.vent_id is not None or self.outcome or self.pending_meeting
                or abs(dx) + abs(dy) != 1):
            return False
        destination = (actor.pos[0] + dx, actor.pos[1] + dy)
        if not self.is_walkable(destination) or self.occupied(destination, player_id):
            return False
        actor.pos = destination
        state = self.tasks.states[player_id]
        if state.active and manhattan(destination, state.active) > 1:
            self.tasks.cancel(self, player_id, "Task interrupted.")
        self.emit("moved", "", actor=player_id, data={"position": list(destination)}, visible_to=[player_id])
        return True

    def move_player(self, dx: int, dy: int) -> bool:
        return self.move_entity(self.player_id, dx, dy)

    def interact_entity(self, player_id: str) -> bool:
        if (self.outcome or self.pending_meeting or not self.entity(player_id).alive
                or self.entity(player_id).vent_id is not None):
            return False
        return self.tasks.start(self, player_id) or self.call_emergency(player_id)

    def interact(self) -> bool:
        return self.interact_entity(self.player_id)

    def cancel_task(self, reason: str = "") -> None:
        self.tasks.cancel(self, self.player_id, reason)

    def call_emergency(self, player_id: str) -> bool:
        actor = self.entity(player_id)
        if actor.vent_id is not None or actor.emergencies_left <= 0 or manhattan(actor.pos, EMERGENCY_POS) > 1:
            return False
        if self.meetings.call(self, player_id):
            actor.emergencies_left -= 1
            return True
        return False

    def report_body(self, player_id: str, automatic: bool = False) -> bool:
        actor = self.entity(player_id)
        if actor.vent_id is not None:
            return False
        for body in self.bodies:
            if (manhattan(actor.pos, body.pos) <= 1
                    and (not automatic or self.elapsed - body.killed_at >= self.config.report_delay)):
                return self.meetings.call(self, player_id, body)
        return False

    def report(self) -> bool:
        return self.report_body(self.player_id)

    def apply_action(self, player_id: str, action: Action | str | dict) -> bool:
        action = Action.parse(action)
        actor = self.entity(player_id)
        if action.kind == "wait":
            return True
        if not actor.alive or self.outcome:
            return False
        if action.kind == "chat":
            return self.chat.send(self, player_id, action.message)
        if action.kind == "vote":
            return self.meetings.submit(self, player_id, action.target)
        if self.pending_meeting:
            return False
        if action.kind == "vent":
            return self.vents.use(self, player_id, action.target)
        if action.kind in MOVE_ACTIONS:
            if actor.move_clock > 1e-9:
                return False
            moved = self.move_entity(player_id, *MOVE_ACTIONS[action.kind])
            if moved:
                actor.move_clock = self.move_interval(actor)
            return moved
        if action.kind == "interact":
            return self.interact_entity(player_id)
        if action.kind == "emergency":
            return self.call_emergency(player_id)
        if action.kind == "report":
            return self.report_body(player_id)
        if action.kind == "kill":
            return self.kill(player_id, action.target)
        return False

    def tick(self, dt: float) -> None:
        if not isinstance(dt, (int, float)) or not math.isfinite(dt) or not 0 <= dt <= 60:
            raise ValueError("dt must be a finite number between 0 and 60 seconds")
        if self.pending_meeting and not self.outcome:
            self.meetings.tick(self, dt)
            return
        remaining = dt
        while remaining > 1e-9 and not self.outcome and not self.pending_meeting:
            step = min(remaining, 0.05)
            remaining -= step
            self.elapsed += step
            for actor in self.players:
                actor.move_clock = max(0.0, actor.move_clock - step)
                actor.rethink_clock -= step
                if actor.vent_id is None:
                    actor.kill_clock = max(0.0, actor.kill_clock - step)
            self.tasks.tick(self, step)
            if self.outcome:
                break
            if self.npc_system is not None:
                self.npc_system.tick(self)
            self.check_parity()
            if self.elapsed >= self.config.max_seconds and not self.outcome:
                self.outcome, self.outcome_reason = "timeout", "Episode time limit reached."
                self.emit("episode_ended", self.outcome_reason, data={"winner": None})

    def move_npc(self, npc: Player) -> None:
        if self.npc_system is not None:
            self.npc_system.move(self, npc)

    def choose_npc_goal(self, npc: Player) -> Pos:
        return self.npc_system.choose_goal(self, npc) if self.npc_system is not None else npc.pos

    def find_path(self, start: Pos, goal: Pos) -> list[Pos]:
        return self.world.find_path(start, goal)

    def kill(self, attacker_id: str, target_id: Optional[str] = None) -> bool:
        attacker = self.entity(attacker_id)
        if (not self.config.kills_enabled or self.outcome or self.pending_meeting or not attacker.alive
                or attacker.vent_id is not None or attacker.role != "impostor" or attacker.kill_clock > 1e-9):
            return False
        candidates = [actor for actor in self.players if actor.alive and actor.role == "crew"
                      and (target_id is None or actor.id == target_id)
                      and manhattan(attacker.pos, actor.pos) <= self.config.kill_radius
                      and self.can_see(attacker_id, actor.pos)
                      and self.has_line_of_sight(attacker.pos, actor.pos, self.config.kill_radius)]
        if not candidates:
            return False
        victim = self.rng.choice(candidates)
        witnesses = [actor.id for actor in self.players if actor.alive and actor not in (attacker, victim)
                     and self.can_see(actor.id, victim.pos)]
        self.bodies.append(Body(victim.id, victim.name, victim.pos, room_at(victim.pos), self.elapsed,
                                witnesses + [attacker_id]))
        victim.alive = False
        victim.path = []
        self.tasks.cancel(self, victim.id)
        attacker.kill_clock = self.config.kill_cooldown
        for witness_id in witnesses:
            witness = self.entity(witness_id)
            witness.suspicion[attacker_id] = witness.suspicion.get(attacker_id, 0) + 8.0
        self.emit("player_killed", f"{attacker.name} eliminated {victim.name}.",
                  actor=attacker_id, target=victim.id, data={"position": list(victim.pos)},
                  visible_to=[attacker_id, victim.id, *witnesses])
        if victim.id == self.player_id and self.config.end_on_player_death:
            self.lose(f"{attacker.name} eliminated {victim.name}.")
        else:
            self.check_parity()
        return True

    def try_impostor_kill(self, impostor: Player) -> bool:
        return self.kill(impostor.id)

    def check_parity(self) -> None:
        if self.outcome or not self.config.impostor_count:
            return
        impostors = sum(actor.alive and actor.role == "impostor" for actor in self.players)
        crew = sum(actor.alive and actor.role == "crew" for actor in self.players)
        if not impostors:
            self.finish("crew", "All impostors have been ejected.")
        elif impostors >= crew:
            self.finish("impostor", "The impostors have reached parity with the crew.")

    def resolve_meeting(self, human_vote: Optional[str]) -> tuple[Optional[str], dict]:
        votes = {self.player_id: human_vote} if self.player.alive else {}
        return self.meetings.resolve(self, votes)

    def reset_after_meeting(self) -> None:
        for actor in self.players:
            actor.vent_id = None
        for index, actor in enumerate(actor for actor in self.players if actor.alive):
            actor.pos = SPAWNS[index]
            actor.path, actor.goal = [], None
            actor.rethink_clock = 0.0
            actor.kill_clock = max(actor.kill_clock, self.config.post_meeting_kill_cooldown)
        self.emit("round_resumed", "Meeting over. Movement resumed.")

    def has_line_of_sight(self, start: Pos, end: Pos, radius: Optional[int] = None) -> bool:
        if not (0 <= start[0] < MAP_W and 0 <= start[1] < MAP_H
                and 0 <= end[0] < MAP_W and 0 <= end[1] < MAP_H):
            return False
        return self.world.has_line_of_sight(start, end, self.config.vision_radius if radius is None else radius)

    def can_see(self, player_id: str, pos: Pos) -> bool:
        actor = self.entity(player_id)
        return actor.alive and actor.vent_id is None and self.has_line_of_sight(actor.pos, pos)

    def visible_positions(self, player_id: Optional[str] = None) -> set[Pos]:
        actor = self.entity(player_id or self.player_id)
        visible: set[Pos] = set()
        if actor.alive and actor.vent_id is None:
            radius = self.config.vision_radius
            for y in range(max(0, actor.pos[1] - radius), min(MAP_H, actor.pos[1] + radius + 1)):
                for x in range(max(0, actor.pos[0] - radius), min(MAP_W, actor.pos[0] + radius + 1)):
                    if self.has_line_of_sight(actor.pos, (x, y)):
                        visible.add((x, y))
        self.discovered_by[actor.id].update(visible)
        return visible

    def finish(self, winner: str, reason: str) -> None:
        if self.outcome:
            return
        self.winner = winner
        self.outcome = "victory" if self.player.role == winner else "defeat"
        self.outcome_reason = reason
        self.emit("episode_ended", reason, data={"winner": winner})

    def win(self, reason: str) -> None:
        self.finish(self.player.role, reason)

    def lose(self, reason: str) -> None:
        self.finish("impostor" if self.player.role == "crew" else "crew", reason)
