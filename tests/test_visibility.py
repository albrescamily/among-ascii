"""Public ship layout with live, occlusion-aware player vision."""
import unittest

from src import Game, GameConfig
from src.core.models import Body
from src.core.world import DOOR_CELLS, EMERGENCY_POS, ROOM_LABELS, VENT_POSITIONS
from src.terminal.palette import BLUE, GRAY, BOLD, RESET
from src.terminal.text import ANSI_SGR
from src.terminal.ui import map_cell, render_game, render_observer


class KnownMapTests(unittest.TestCase):
    def test_god_view_reveals_players_and_bodies_without_changing_normal_vision(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        game.panel_visible = False
        for zoom in (1, 2):
            with self.subTest(zoom=zoom):
                game.map_zoom = zoom
                game.player_pos = (53, 30)
                hidden = game.npcs[0]
                hidden.pos = (53, 36)
                game.bodies = [Body("blue", "Blue", (54, 36), "Storage", 0)]
                normal = render_game(game, (230, 49))
                self.assertIn("FIELD OF VIEW", normal)
                self.assertIn(GRAY + "#", normal)
                self.assertNotIn("†", ANSI_SGR.sub("", normal))
                hidden_symbol = BOLD + hidden.color + hidden.symbol + RESET
                self.assertNotIn(hidden_symbol, normal)
                screen = render_observer(game, (230, 49))
                self.assertIn("GOD VIEW", screen)
                self.assertIn("FULL MAP", screen)
                self.assertNotIn("FIELD OF VIEW", screen)
                self.assertNotIn(GRAY + "#", screen)
                self.assertIn("†", ANSI_SGR.sub("", screen))
                self.assertIn(hidden_symbol, screen)
                self.assertEqual(render_game(game, (230, 49)), normal)
        simulation = Game(config=GameConfig(play_mode="simulation"))
        self.assertIn("SIMULATION", render_observer(simulation, (230, 49)))
        self.assertNotIn("FIELD OF VIEW", render_observer(simulation, (230, 49)))

    def test_layout_and_landmarks_are_known_before_exploring(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        self.assertEqual(game.discovered, set())
        for pos, glyph in ROOM_LABELS.items():
            self.assertEqual(ANSI_SGR.sub("", map_cell(game, pos, set())), glyph)
            self.assertIn(GRAY, map_cell(game, pos, set()))
        for pos, glyph in (((1, 16), "#"), (EMERGENCY_POS, "◉"),
                           (VENT_POSITIONS["navigation_upper"], "▣")):
            self.assertIn(GRAY, map_cell(game, pos, set()))
            self.assertEqual(ANSI_SGR.sub("", map_cell(game, pos, set())), glyph)
        screen = ANSI_SGR.sub("", render_game(game, (230, 49)))
        for label in ("R E A C T O R", "N A V", "S T O R A G E"):
            self.assertIn(label, screen)

    def test_floor_dots_only_mark_the_field_of_view(self):
        game = Game(config=GameConfig(npc_ai_enabled=False, initial_sabotage_cooldown=0))
        floor = (53, 30)
        for sabotage in (False, True):
            if sabotage:
                game.apply_action(game.impostor_id, {"kind": "sabotage", "target": "admin"})
            with self.subTest(sabotage=sabotage):
                self.assertEqual(ANSI_SGR.sub("", map_cell(game, floor, {floor})), "·")
                self.assertEqual(ANSI_SGR.sub("", map_cell(game, floor, set())), " ")

    def test_static_geometry_switches_between_gray_and_blue(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        samples = ((1, 16), (53, 31), next(iter(DOOR_CELLS)), next(iter(ROOM_LABELS)))
        for pos in samples:
            with self.subTest(pos=pos):
                unseen = map_cell(game, pos, set())
                seen = map_cell(game, pos, {pos})
                self.assertIn(GRAY, unseen)
                self.assertIn(BLUE, seen)
                self.assertEqual(ANSI_SGR.sub("", unseen), ANSI_SGR.sub("", seen))
                game.discovered.add(pos)
                self.assertEqual(map_cell(game, pos, set()), unseen)

    def test_container_occludes_characters_and_bodies_but_not_the_layout(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        actor = game.npcs[0]
        actor.pos = (53, 36)
        body = Body("blue", "Blue", (54, 36), "Storage", 0)
        game.bodies.append(body)
        game.player_pos = (53, 30)
        visible = game.visible_positions()
        self.assertIn(BLUE, map_cell(game, (53, 31), visible))
        self.assertIn(GRAY, map_cell(game, (53, 35), visible))
        for pos in (actor.pos, body.pos):
            self.assertNotIn(pos, visible)
            self.assertEqual(ANSI_SGR.sub("", map_cell(game, pos, visible)), " ")
            self.assertIn(GRAY, map_cell(game, pos, visible))
        game.player_pos = (58, 36)
        visible = game.visible_positions()
        self.assertEqual(ANSI_SGR.sub("", map_cell(game, actor.pos, visible)), actor.symbol)
        self.assertEqual(ANSI_SGR.sub("", map_cell(game, body.pos, visible)), "†")
        game.player_pos = (53, 30)
        visible = game.visible_positions()
        for pos in (actor.pos, body.pos):
            self.assertIn(pos, game.discovered)
            self.assertEqual(ANSI_SGR.sub("", map_cell(game, pos, visible)), " ")


if __name__ == "__main__":
    unittest.main()
