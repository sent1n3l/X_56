# X_56

Auto-configurator for Star Citizen X56 joystick + throttle profiles.

## Features
- Detects connected X56 joystick and throttle (best effort, Windows-friendly).
- Cleans stale entries and applies versioned default bindings.
- Keeps a version-history template set in `templates/version_history.json`.
- Stores integrity snapshots and warns when profile drift is detected.
- Includes GUI debug panel and progress bar.

## Run
```bash
python /home/runner/work/X_56/X_56/x56_configurator.py
```

## Test
```bash
cd /home/runner/work/X_56/X_56
python -m unittest discover -s tests
```
