"""The Go2's start ramp per axis, calibrated on the robot (robot/docs/robot.md): the commanded time each Move
sequence loses before covering ground. Skills add it as lead time; the sim models it as dead time (service.md)."""

LINEAR_S = 1.1  # walk (tunable)
YAW_S = 0.2  # turn, used only by its open-loop fallback (tunable)
