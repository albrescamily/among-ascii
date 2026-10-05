"""Broadcast permissions, meeting deadlines, and non-blocking UI."""
import os
from itertools import count
import unittest
from unittest.mock import Mock, patch

from src import Action, Game, GameConfig
from src.core.models import Body
from src.terminal.chat import ChatView, render_chat
from src.terminal.driver import Terminal
from src.terminal.runtime import conduct_meeting, play_one
from src.terminal.text import ANSI_SGR, display_width
from src.terminal.ui import render_meeting


class ChatTests(unittest.TestCase):
    def test_broadcast_reaches_distant_and_dead_players_without_mutable_aliases(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        game.entity('red').pos = (0, 0)
        game.entity('blue').alive = False
        game.meetings.call(game, 'cyan')
        self.assertTrue(game.apply_action('cyan', Action('chat', message='Hello world')))
        for actor in game.players:
            events = game.events.for_player(actor.id)
            self.assertIn('chat_message', [event['kind'] for event in events])
            self.assertEqual(events[-1]['data']['text'], 'Hello world')
        snapshot = game.chat.snapshot()
        snapshot[-1]['text'] = 'tampered'
        self.assertEqual(game.chat.snapshot()[-1]['text'], 'Hello world')

    def test_dead_and_ejected_players_cannot_send_in_game_or_meetings(self):
        game = Game(config=GameConfig(end_on_player_death=False, initial_kill_cooldown=0))
        attacker = game.entity(game.impostor_id)
        victim = next(actor for actor in game.npcs if actor.role == "crew")
        attacker.pos, victim.pos = (12, 7), (12, 8)
        self.assertTrue(game.kill(attacker.id, victim.id))
        self.assertFalse(game.apply_action(victim.id, Action("chat", message="dead")))
        game.meetings.call(game, "cyan")
        self.assertFalse(game.chat.send(game, victim.id, "still dead"))
        ejected = next(actor.id for actor in game.npcs if actor.alive and actor.role == "crew")
        game.meetings.resolve(game, dict.fromkeys(game.alive_ids(), ejected))
        self.assertFalse(game.apply_action(ejected, Action("chat", message="ejected")))

    def test_safe_bounded_history_and_message_validation(self):
        game = Game(config=GameConfig(chat_history=2, chat_max_length=20, npc_ai_enabled=False))
        game.meetings.call(game, "cyan")
        for message in ("old", "Hello\nworld", "\x1b[2J text"):
            self.assertTrue(game.chat.send(game, "cyan", message))
        messages = game.chat.snapshot()
        self.assertEqual([message["sequence"] for message in messages], [2, 3])
        self.assertEqual(messages[0]["text"], "Hello world")
        self.assertNotIn("\x1b", messages[-1]["text"])
        for value in ("", " \t\n", "x" * 21, None):
            self.assertFalse(game.chat.send(game, "cyan", value))
        for action in ({"kind": "chat"}, {"kind": "chat", "message": 4},
                       {"kind": "chat", "message": "ok", "target": "red"},
                       {"kind": "wait", "message": "ok"}):
            with self.assertRaises(ValueError):
                Action.parse(action)

    def test_builtin_bots_only_greet_once_per_voting_session(self):
        game = Game(config=GameConfig(kills_enabled=False, seed=7, player_role="crew"))
        game.tick(0.1)
        self.assertEqual(len(game.chat.messages), 0)
        game.npc_system.greet(game)
        self.assertEqual(len(game.chat.messages), 0)
        game.entity("red").alive = False
        game.meetings.call(game, "cyan")
        game.npc_system.greet(game)
        game.tick(0.2)
        self.assertEqual(len(game.chat.messages), 10)
        self.assertTrue(all(message.text == "Hello world" for message in game.chat.messages))
        self.assertEqual(sum(message.sender == "red" for message in game.chat.messages), 0)
        game.meetings.resolve(game, dict.fromkeys(game.alive_ids(), None))
        game.tick(0.1)
        self.assertEqual(len(game.chat.messages), 10)
        game.meetings.call(game, "cyan")
        self.assertEqual(len(game.chat.messages), 20)

    def test_chat_permissions_follow_voting_and_preserve_history(self):
        game = Game(config=GameConfig(npc_ai_enabled=False, voting_seconds=1))
        action = Action("chat", message="Meeting message")
        before = game.events.sequence
        for actor in game.players:
            self.assertFalse(game.chat.send(game, actor.id, "blocked"))
            self.assertFalse(game.apply_action(actor.id, action))
        self.assertEqual(game.events.sequence, before)
        self.assertEqual(game.chat.sequence, 0)
        self.assertFalse(ChatView.can_send(game))
        game.meetings.call(game, "cyan")
        self.assertTrue(ChatView.can_send(game))
        self.assertTrue(game.apply_action("cyan", "vote"))
        self.assertTrue(game.apply_action("cyan", action))
        history = game.chat.snapshot()
        game.tick(1)
        self.assertIsNone(game.pending_meeting)
        self.assertFalse(game.apply_action("cyan", action))
        self.assertFalse(ChatView.can_send(game))
        self.assertEqual(game.chat.snapshot(), history)
        screen = ANSI_SGR.sub("", render_chat(game, ChatView(), (60, 20)))
        self.assertIn("Read only", screen)
        self.assertIn("Meeting message", screen)
        self.assertNotIn("Enter send", screen)
        game.meetings.call(game, "cyan")
        self.assertTrue(ChatView.can_send(game))
        # A deadline also blocks sends before the engine resolves the ballot.
        game.meetings.elapsed = game.meetings.duration
        self.assertFalse(game.chat.send(game, "cyan", "too late"))
        self.assertFalse(ChatView.can_send(game))

    def test_new_game_clears_chat_and_preserves_determinism(self):
        game = Game()
        game.meetings.call(game, "cyan")
        before = game.chat.snapshot()
        game = Game()
        self.assertEqual(game.chat.snapshot(), [])
        game.meetings.call(game, "cyan")
        self.assertEqual(game.chat.snapshot(), before)


class VotingTimerTests(unittest.TestCase):
    def test_builtin_votes_change_status_during_meeting_and_match_results(self):
        game = Game(config=GameConfig(voting_seconds=30, seed=7))
        game.meetings.call(game, "cyan")
        self.assertEqual(game.meetings.votes, {})
        positions = [actor.pos for actor in game.players]
        cooldowns = [actor.kill_clock for actor in game.players]
        transitions = []
        previous = {}
        for _ in range(24):
            game.tick(1)
            self.assertIsNotNone(game.pending_meeting)  # Local player has not voted.
            ballots = dict(game.meetings.votes)
            for actor_id, target in previous.items():
                self.assertIn(actor_id, ballots)
                self.assertEqual(ballots[actor_id], target)
            if len(ballots) > len(previous):
                transitions.append(game.meetings.elapsed)
            screen = ANSI_SGR.sub("", render_meeting(game, "cyan", None, size=(60, 20)))
            self.assertEqual(screen.count("VOTED"), len(ballots))
            self.assertEqual(screen.count("WAITING"), 12 - len(ballots))
            previous = ballots
        self.assertGreater(len(transitions), 1)
        self.assertEqual(len(game.meetings.votes), 11)
        self.assertNotIn("cyan", game.meetings.votes)
        self.assertEqual([actor.pos for actor in game.players], positions)
        self.assertEqual([actor.kill_clock for actor in game.players], cooldowns)
        self.assertEqual(game.elapsed, 0)
        self.assertTrue(game.apply_action("cyan", "vote"))
        submitted = dict(game.meetings.votes)
        game.tick(0.1)
        self.assertIsNone(game.pending_meeting)
        result = next(event for event in reversed(game.events.snapshot()) if event["kind"] == "meeting_resolved")
        self.assertEqual(result["data"]["votes"], submitted)

    def test_builtin_vote_timing_is_seeded_and_resets_each_meeting(self):
        game = Game(config=GameConfig(voting_seconds=30, seed=7))

        def run_meeting():
            game.meetings.call(game, "cyan")
            history = []
            for _ in range(24):
                game.tick(1)
                history.append(dict(game.meetings.votes))
            return history

        first = run_meeting()
        game = Game(config=GameConfig(voting_seconds=30, seed=7))
        self.assertEqual(first, run_meeting())
        old_schedule = dict(game.npc_system.meeting_vote_times)
        game.meetings.resolve(game, dict.fromkeys(game.alive_ids(), None))
        game.meetings.call(game, "cyan")
        self.assertEqual(game.meetings.votes, {})
        self.assertNotEqual(old_schedule, game.npc_system.meeting_vote_times)
        game.tick(0.1)
        self.assertEqual(game.meetings.votes, {})

    def test_timeout_skips_missing_votes_and_freezes_world_clocks(self):
        game = Game(config=GameConfig(voting_seconds=0.3, npc_ai_enabled=False))
        game.meetings.call(game, 'cyan')
        positions = [actor.pos for actor in game.players]
        cooldown = game.kill_cooldown
        game.apply_action('cyan', Action('chat', message='Hello world'))
        game.tick(0.1)
        self.assertAlmostEqual(game.meetings.remaining, 0.2)
        self.assertEqual([actor.pos for actor in game.players], positions)
        self.assertEqual(game.kill_cooldown, cooldown)
        self.assertTrue(game.apply_action('cyan', Action('vote', 'red')))
        game.tick(0.2)
        self.assertIsNone(game.pending_meeting)
        self.assertEqual(game.elapsed, 0)
        self.assertEqual(game.meetings.last_result, (None, {'red': 1, None: 11}))
        game.meetings.call(game, 'cyan')
        self.assertEqual(game.meetings.remaining, 0.3)
        self.assertEqual(game.meetings.votes, {})


    def test_voting_and_chat_config_validation(self):
        for name, value in (("voting_seconds", 0), ("voting_seconds", float("nan")),
                            ("chat_history", 0), ("chat_max_length", -1), ("chat_history", 2.5)):
            with self.assertRaises(ValueError):
                GameConfig.from_dict({name: value})


class ChatLayoutTests(unittest.TestCase):
    def test_own_messages_sit_right_and_others_left_without_live_label(self):
        game = Game(config=GameConfig(npc_ai_enabled=False, seed=7, player_role="crew"))
        game.meetings.call(game, "red")
        game.chat.send(game, "red", "I saw Green vent")
        game.chat.send(game, "cyan", "its orange")
        for size in ((100, 30), (60, 20)):
            lines = ANSI_SGR.sub("", render_chat(game, ChatView(), size)).splitlines()
            mine = next(line for line in lines if "its orange" in line)
            theirs = next(line for line in lines if "I saw Green vent" in line)
            # Inside the frame: theirs starts at the left edge, mine is pushed right.
            self.assertLess(theirs.index("I saw Green vent"), 20, size)
            self.assertGreater(mine.index("its orange"), len(mine) // 3, size)
            self.assertNotIn("Live", "\n".join(lines))


class ChatTerminalTests(unittest.TestCase):
    def test_composer_preserves_case_backspace_and_blocks_commands(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        game.meetings.call(game, "cyan")
        view = ChatView()
        for key in [*"Hello worldq", "\x08", "\r"]:
            self.assertFalse(view.handle_key(game, key))
        self.assertEqual(game.chat.messages[-1].text, "Hello world")
        self.assertEqual(view.draft, "")
        self.assertIsNone(game.outcome)
        self.assertFalse(view.handle_key(game, "escape"))  # Esc would quit from the ballot
        self.assertTrue(view.handle_key(game, "\t"))  # Tab returns to voting
        game.player.alive = False
        for key in [*"blocked", "\r"]:
            view.handle_key(game, key)
        self.assertEqual(len(game.chat.messages), 1)

    def test_simulation_chat_is_read_only(self):
        game = Game(config=GameConfig(play_mode="simulation"))
        game.meetings.call(game, "cyan")
        view = ChatView()
        for key in [*"blocked", "\r"]:
            view.handle_key(game, key)
        self.assertEqual(len(game.chat.messages), 0)
        self.assertIn("Read only", render_chat(game, view))

    def test_chat_and_meeting_screens_fit_without_hiding_controls(self):
        game = Game()
        body = Body("red", "Red", (1, 1), "Navigation", 0, ["blue", "green"])
        game.meetings.call(game, "cyan", body)
        initial = render_meeting(game, "cyan", body, size=(60, 20))
        self.assertEqual(initial.count("WAITING"), len(game.alive_ids()))
        self.assertTrue(game.apply_action("cyan", "vote"))  # Skip is a submitted ballot.
        self.assertTrue(game.apply_action("red", Action("vote", "blue")))
        game.chat.send(game, "cyan", "Long text " * 20)
        view = ChatView()
        view.draft = "界" * 200
        for size in ((60, 20), (80, 24), (120, 38), (230, 49)):
            for screen in (render_chat(game, view, size), render_meeting(game, "cyan", body, size=size)):
                lines = ANSI_SGR.sub("", screen).splitlines()
                self.assertLessEqual(len(lines), size[1] - 1)
                self.assertTrue(all(display_width(line) <= size[0] - 1 for line in lines))
                self.assertTrue("Esc" in screen or "Tab voting" in screen)  # a way back is always shown
                self.assertTrue("╚" in screen or "╰" in screen)  # bottom frame still on screen
            voting = render_meeting(game, "cyan", body, size=size)
            plain_voting = ANSI_SGR.sub("", voting)
            self.assertIn("Red body found.", plain_voting)
            self.assertNotIn("Navigation", plain_voting)
            self.assertNotIn("Meeting called", plain_voting)
            self.assertNotIn("nearby", plain_voting)
            self.assertRegex(plain_voting, r"Cyan @\s+VOTED")
            self.assertRegex(plain_voting, r"Red\s+VOTED")
            self.assertRegex(plain_voting, r"Blue\s+WAITING")
            self.assertEqual(plain_voting.count("WAITING"), len(game.alive_ids()) - 2)
            self.assertEqual(plain_voting.count("VOTED"), 2)
            game.meetings.votes["red"] = "green"
            self.assertEqual(voting, render_meeting(game, "cyan", body, size=size))
            game.meetings.votes["red"] = "blue"
            self.assertNotIn("Hello world", voting)
            self.assertNotIn("Long text", voting)
            self.assertNotIn("BROADCAST CHAT", voting)
            self.assertIn("Tab chat", voting)
            self.assertIn("BROADCAST CHAT", render_chat(game, view, size))
            self.assertIn("Tab voting", render_chat(game, view, size))
        view.scroll = 1000
        self.assertIn("Red: Hello world", ANSI_SGR.sub("", render_chat(game, view, (60, 20))))

    def test_live_game_chat_is_read_only_and_keeps_ticking(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        term = Mock()
        term.read_keys.side_effect = [["t", *"QWERTY", "\r"], ["escape"], ["q"]]
        with patch("src.terminal.runtime.Game", return_value=game), \
                patch.object(game, "move_player") as move, patch.object(game, "interact") as interact, \
                patch.object(game, "tick", wraps=game.tick) as tick, \
                patch("src.terminal.runtime.time.sleep"):
            play_one(term)
        move.assert_not_called()
        interact.assert_not_called()
        self.assertEqual(game.chat.snapshot(), [])
        self.assertIn("Read only", term.draw.call_args_list[0].args[0])
        self.assertIn("only during voting", term.draw.call_args_list[0].args[0])
        self.assertGreaterEqual(tick.call_count, 2)

    def test_meeting_chat_does_not_pause_deadline_or_treat_text_as_votes(self):
        game = Game(config=GameConfig(voting_seconds=1, vote_result_seconds=3))
        game.meetings.call(game, "cyan")
        term = Mock()
        term.read_keys.side_effect = [["t", *"Q123Hello", "\r"], []]
        with patch.object(game.meetings, "bot_vote", return_value=None), \
                patch("src.terminal.runtime.time.monotonic", side_effect=[0, 0.1, 1, 1, 1.1, 5]), \
                patch("src.terminal.runtime.time.sleep"):
            conduct_meeting(term, game)
        self.assertEqual(game.chat.messages[-1].text, "Q123Hello")
        self.assertEqual(game.meetings.last_result, (None, {None: 12}))
        self.assertIsNone(game.outcome)
        self.assertEqual(game.elapsed, 0)

    def test_switching_between_voting_and_chat_preserves_draft_and_vote(self):
        game = Game(config=GameConfig(voting_seconds=2))
        game.meetings.call(game, "cyan")
        term = Mock()
        keys = iter([["t", *"Q123Hello"], ["\t"], ["\t"], ["\r", "\t"],
                     ["0"], ["t"], ["\t"]])
        term.read_keys.side_effect = lambda: next(keys, [])
        with patch.object(game.meetings, "bot_vote", return_value=None), \
                patch("src.terminal.runtime.time.monotonic", side_effect=count(0, 0.1)), \
                patch("src.terminal.runtime.time.sleep"):
            conduct_meeting(term, game)
        screens = [ANSI_SGR.sub("", call.args[0]) for call in term.draw.call_args_list]
        for index in (0, 2, 4, 5, 7):
            self.assertIn("EMERGENCY MEETING", screens[index])
            self.assertNotIn("Hello world", screens[index])
            self.assertNotIn("Q123Hello", screens[index])
        for index in (1, 3, 6):
            self.assertIn("BROADCAST CHAT", screens[index])
            self.assertIn("Tab voting", screens[index])
        self.assertIn("Q123Hello", screens[3])
        self.assertIn("Your vote: Skip", screens[7])
        self.assertEqual(game.chat.messages[-1].text, "Q123Hello")
        self.assertEqual(game.meetings.last_result, (None, {None: 12}))
        self.assertIsNone(game.outcome)
        self.assertEqual(game.elapsed, 0)

    @unittest.skipUnless(os.name == "nt", "Windows console input")
    def test_windows_keyboard_keeps_uppercase_for_chat(self):
        terminal = Terminal()
        with patch("msvcrt.kbhit", side_effect=[True, True, False]), \
                patch("msvcrt.getwch", side_effect=["H", "é"]):
            self.assertEqual(terminal._read_windows(), ["H", "é"])

    @unittest.skipUnless(os.name == "nt", "Windows console input")
    def test_windows_scroll_keys_never_leak_letters_into_the_draft(self):
        terminal = Terminal()
        # Scrolling fast: the code byte is not ready when the prefix is read
        # (kbhit is False in between). It used to type "HP" into the chat.
        with patch("msvcrt.kbhit", side_effect=[True, False, True, True, True, False]), \
                patch("msvcrt.getwch", side_effect=["\xe0", "H", "\xe0", "P", "\xe0", "I", "\x00", "Q"]):
            self.assertEqual(terminal._read_windows(), ["up"])
            self.assertEqual(terminal._read_windows(), ["down", "pageup", "pagedown"])

    def test_unix_escape_sequences_never_leak_as_text(self):
        terminal = Terminal()
        terminal.buffer = b"\x1b[A\x1b[5~\x1b[6~hi\x1b[3~\x1b[200~\x1b["
        self.assertEqual(terminal.parse_unix_buffer(), ["up", "pageup", "pagedown", "h", "i", "delete"])
        self.assertEqual(terminal.buffer, b"\x1b[")  # incomplete sequence waits for more bytes
        terminal.buffer += b"B"
        self.assertEqual(terminal.parse_unix_buffer(), ["down"])

    def test_unix_keyboard_preserves_split_utf8_and_case(self):
        terminal = Terminal()
        terminal.buffer = b"\xc3"
        with patch("src.terminal.driver.sys.stdin.isatty", return_value=True), \
                patch("select.select", return_value=([], [], [])):
            self.assertEqual(terminal._read_unix(), [])
            terminal.buffer += b"\xa9 Hello"
            self.assertEqual(terminal._read_unix(), list("é Hello"))
            self.assertEqual(terminal.buffer, b"")


if __name__ == "__main__":
    unittest.main()
