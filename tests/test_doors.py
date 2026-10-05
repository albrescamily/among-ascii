"""Impostor door sabotage: lock rules, timing, rendering and mini map controls."""
import unittest
from unittest.mock import Mock, patch

from src import Action, Game, GameConfig
from src.core.doors import DOOR_KEYS, room_doors
from src.core.world import EMERGENCY_POS
from src.terminal.runtime import play_one
from src.terminal.text import ANSI_SGR
from src.terminal.ui import map_cell, render_minimap


def make_game(**overrides):
    options = dict(npc_ai_enabled=False, kills_enabled=False, player_role="impostor",
                   task_win_mode="disabled", initial_sabotage_cooldown=0, initial_door_cooldown=0)
    options.update(overrides)
    return Game(config=GameConfig(**options))


def close(game, room):
    return game.apply_action(game.player_id, Action("doors", room))


class DoorTests(unittest.TestCase):
    def test_closed_doors_block_movement_and_sight_then_reopen(self):
        game = make_game()
        door = room_doors("electrical")[0]
        inside, outside = (door.cells[0][0], door.cells[0][1] - 1), (door.cells[0][0], door.cells[0][1] + 1)
        self.assertTrue(game.find_path(inside, outside))
        self.assertTrue(close(game, "electrical"))
        self.assertFalse(game.is_walkable(door.cells[0]))
        self.assertFalse(game.has_line_of_sight(inside, outside))
        self.assertEqual(game.find_path(inside, outside), [])
        self.assertEqual(ANSI_SGR.sub("", map_cell(game, door.cells[0], {door.cells[0]})), "+")
        game.tick(game.config.door_close_seconds)
        self.assertTrue(game.is_walkable(door.cells[0]))
        self.assertEqual(ANSI_SGR.sub("", map_cell(game, door.cells[0], {door.cells[0]})), "-")

    def test_at_most_three_rooms_closed(self):
        game = make_game()
        # The limit counts rooms: Cafeteria and Storage have 3 doors each and still fit.
        for room in ("cafeteria", "storage", "upper_engine"):
            self.assertTrue(close(game, room), room)
        self.assertEqual(game.doors.closed_count, 3)
        self.assertFalse(close(game, "medbay"))
        self.assertIn("At most 3 rooms", game.doors.refusal(game, game.player_id, "medbay"))
        game.tick(game.config.door_close_seconds)
        self.assertEqual(game.doors.closed_count, 0)
        self.assertTrue(close(game, "medbay"))

    def test_no_doors_during_sabotage_and_sabotage_opens_them(self):
        game = make_game()
        self.assertTrue(close(game, "storage"))
        self.assertTrue(game.apply_action(game.player_id, Action("sabotage", "lights")))
        self.assertFalse(game.doors.closed)
        self.assertTrue(all(game.is_walkable(cell) for door in room_doors("storage") for cell in door.cells))
        self.assertFalse(close(game, "medbay"))
        self.assertIn("sabotage", game.doors.refusal(game, game.player_id, "medbay"))

    def test_only_living_impostors_and_room_cooldown(self):
        crew = make_game(player_role="crew")
        self.assertFalse(close(crew, "medbay"))
        game = make_game()
        self.assertTrue(close(game, "medbay"))
        self.assertFalse(close(game, "medbay"))
        game.tick(game.config.door_close_seconds)
        self.assertFalse(close(game, "medbay"))  # recharging
        game.tick(game.config.door_cooldown)
        self.assertTrue(close(game, "medbay"))
        self.assertFalse(close(game, "nowhere"))

    def test_meetings_open_every_door(self):
        game = make_game(player_role="crew")
        impostor = game.entity(game.impostor_id)
        game.doors.close(game, impostor.id, "cafeteria")
        game.player_pos = EMERGENCY_POS
        self.assertTrue(game.call_emergency(game.player_id))
        self.assertFalse(game.doors.closed)

    def test_npcs_wait_instead_of_walking_through_closed_doors(self):
        game = make_game(npc_ai_enabled=True, player_role="impostor")
        close(game, "cafeteria")
        cells = {cell for door in room_doors("cafeteria") for cell in door.cells}
        for _ in range(30):
            game.tick(0.2)
            self.assertFalse(any(actor.pos in cells for actor in game.npcs))


class InitialCooldownTests(unittest.TestCase):
    def test_doors_wait_ten_seconds_at_the_start(self):
        game = make_game(initial_door_cooldown=GameConfig().initial_door_cooldown)
        self.assertEqual(game.config.initial_door_cooldown, 10)
        self.assertFalse(close(game, "medbay"))
        self.assertIn("ready in 10s", game.doors.refusal(game, game.player_id, "medbay"))
        self.assertIn("Doors ready in 10s", ANSI_SGR.sub("", render_minimap(game, (130, 42))))
        game.tick(9.9)
        self.assertFalse(close(game, "medbay"))
        game.tick(0.1)
        self.assertTrue(close(game, "medbay"))

    def test_test_mode_starts_with_doors_ready(self):
        game = make_game(test_mode=True, initial_door_cooldown=10)
        self.assertTrue(close(game, "medbay"))


class NpcDoorTests(unittest.TestCase):
    def setup_medbay(self, **overrides):
        game = make_game(npc_ai_enabled=True, player_role="crew", initial_sabotage_cooldown=1000, **overrides)
        impostor = game.entity(game.impostor_id)
        victim = next(actor for actor in game.npcs if actor.role == "crew")
        impostor.pos, victim.pos = (30, 18), (34, 18)
        return game, impostor, victim

    def test_npc_impostor_traps_a_crewmate_in_its_room(self):
        game, impostor, victim = self.setup_medbay()
        game.tick(0.01)
        self.assertEqual(list(game.doors.closed), ["medbay"])

    def test_no_trap_without_a_victim_in_sight_or_during_sabotage(self):
        game, impostor, victim = self.setup_medbay()
        victim.pos = (49, 15)  # hallway, out of MedBay
        game.tick(0.01)
        self.assertFalse(game.doors.closed)
        game, impostor, victim = self.setup_medbay()
        game.sabotage.cooldown = 0
        self.assertTrue(game.sabotage.start(game, impostor.id, "lights"))
        game.tick(0.01)
        self.assertFalse(game.doors.closed)

    def test_npc_partners_never_sabotage_for_a_human_impostor(self):
        game = make_game(npc_ai_enabled=True, player_role="impostor", impostor_count=2,
                         initial_sabotage_cooldown=0)
        partner = next(actor for actor in game.npcs if actor.role == "impostor")
        victim = next(actor for actor in game.npcs if actor.role == "crew")
        partner.pos, victim.pos = (30, 18), (34, 18)
        game.tick(0.01)
        self.assertIsNone(game.sabotage.kind)
        self.assertFalse(game.doors.closed)
        # A human impostor still triggers them by hand.
        self.assertTrue(game.apply_action(game.player_id, Action("sabotage", "lights")))
        crew = make_game(npc_ai_enabled=True, player_role="crew", initial_sabotage_cooldown=0)
        crew.tick(0.01)
        self.assertIsNotNone(crew.sabotage.kind)

    def test_test_mode_npcs_stay_idle(self):
        game = make_game(test_mode=True, player_role="crew")
        impostor = game.entity(game.impostor_id)
        victim = next(actor for actor in game.npcs if actor.role == "crew")
        impostor.pos, victim.pos = (30, 18), (34, 18)
        game.tick(0.01)
        self.assertFalse(game.doors.closed)


class DoorControlsTests(unittest.TestCase):
    def run_keys(self, game, keys):
        term = Mock()
        term.read_keys.side_effect = keys
        term.caps_lock.return_value = False
        with patch("src.terminal.runtime.Game", return_value=game), patch(
                "src.terminal.text.shutil.get_terminal_size", return_value=(130, 42)):
            play_one(term)

    def test_number_keys_in_the_minimap_close_rooms(self):
        game = make_game()
        key = next(k for k, room in DOOR_KEYS.items() if room == "medbay")
        self.run_keys(game, [[key], ["\t"], [key], ["q"]])
        # Outside the map the number did nothing; inside it, MedBay closed.
        self.assertEqual(list(game.doors.closed), ["medbay"])

    def test_simulation_spectator_closes_doors_through_a_living_impostor(self):
        game = make_game(play_mode="simulation")
        key = next(k for k, room in DOOR_KEYS.items() if room == "storage")
        self.run_keys(game, [[key], ["\t"], [key], ["q"]])
        self.assertEqual(list(game.doors.closed), ["storage"])
        screen = ANSI_SGR.sub("", render_minimap(game, (130, 42)))
        self.assertIn("MODE SIMULATION / MINI MAP", screen)
        self.assertIn("Acting as", screen)
        self.assertNotIn("@", screen.split("MISSION CONTROL")[0].split("FULL MAP")[-1])
        lonely = make_game(play_mode="simulation", impostor_count=0, player_role="crew")
        self.run_keys(lonely, [["\t"], [key], ["q"]])
        self.assertFalse(lonely.doors.closed)
        self.assertIn("No living impostor", ANSI_SGR.sub("", render_minimap(lonely, (130, 42))))

    def test_minimap_lists_numbered_rooms_for_impostors_only(self):
        game = make_game()
        screen = ANSI_SGR.sub("", render_minimap(game, (130, 42)))
        self.assertIn("DOORS 0/3 ROOMS CLOSED", screen)
        self.assertIn("1 Cafeteria", screen)
        crew = ANSI_SGR.sub("", render_minimap(make_game(player_role="crew"), (130, 42)))
        self.assertNotIn("DOORS", crew)


if __name__ == "__main__":
    unittest.main()
