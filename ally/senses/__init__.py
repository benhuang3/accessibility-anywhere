"""Senses: notice when the user is struggling, from their face (squint, lean) and their mouse and
keyboard (rage clicks, retyping, hesitation), and report one Struggle event for the brain to act on.

    face.py          webcam frame -> face numbers (runs inside the camera helper, ally.gaze_helper)
    detectors.py     face numbers and input events -> Signals (pure Python, unit tested)
    fusion.py        Signals -> Struggle (two signals must agree unless a kind is listed in [senses] solo)
    input_helper.py  separate process: mouse clicks/moves and key presses (never which keys)
    manager.py       Senses, the agent-side owner of all of the above

Privacy: webcam frames are processed in memory inside the camera helper and thrown away. Only a few
numbers per frame (like a squint score) reach the agent, and nothing in this package uses the network,
writes images or records video (tests/test_senses.py checks that).

Nothing is imported here, so the helper processes stay light: use ally.senses.manager.Senses.
"""
