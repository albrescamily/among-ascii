# Simulation: starting from scratch

This folder is reserved for your new simulation agents. It contains no policies,
controllers, observations, rewards, environment API or agent protocol.

Simulation currently displays the shared map and roster without controllers.
Players do not move, start tasks, hunt, report, chat or vote automatically.
Use WASD or the arrow keys to pan the camera across the entire map, Z to zoom,
and P to toggle the side panel. Chat remains read-only. Shared game rules
remain in `src/core/`; actions can still be applied explicitly through `Game`.

Game mode uses a human player and built-in NPCs implemented in `src/npcs.py`.
Those NPCs are exclusive to Game mode and are not simulation agents.
Shared configuration is in `src/core/config.py`.

Run `python game.py` and choose Game or Simulation in the menu.
Start the agents implementation here from scratch.
