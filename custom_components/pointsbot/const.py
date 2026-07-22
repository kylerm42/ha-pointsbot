"""Constants for the PointsBot integration."""

DOMAIN = "pointsbot"

# Storage
STORAGE_KEY_DATA = "pointsbot_data"
STORAGE_KEY_HISTORY = "pointsbot_history"
STORAGE_VERSION = 1

# Task types
TASK_TYPE_BASE = "base"
TASK_TYPE_BONUS = "bonus"

# Event types
EVENT_MANUAL_ADJUSTMENT = "manual_adjustment"
EVENT_BONUS_COMPLETION = "bonus_completion"
EVENT_BONUS_UNCOMPLETION = "bonus_uncompletion"
EVENT_WEEKLY_ROLLOVER = "weekly_rollover"

# Dispatcher signal format strings (format with entry_id)
SIGNAL_POINTSBOT_UPDATE = "pointsbot_update_{}"
SIGNAL_POINTSBOT_NEW_PERSON = "pointsbot_new_person_{}"

# Service names
SERVICE_SYNC_PEOPLE = "sync_people"
SERVICE_ADJUST_POINTS = "adjust_points"
SERVICE_SET_WEEKLY_ALLOTMENT = "set_weekly_allotment"
SERVICE_ADD_TASK = "add_task"
SERVICE_UPDATE_TASK = "update_task"
SERVICE_DELETE_TASK = "delete_task"
SERVICE_TOGGLE_BASE_TASK = "toggle_base_task"
SERVICE_COMPLETE_BONUS_TASK = "complete_bonus_task"
SERVICE_UNCOMPLETE_BONUS_TASK = "uncomplete_bonus_task"
SERVICE_RUN_WEEKLY_RESET = "run_weekly_reset"
