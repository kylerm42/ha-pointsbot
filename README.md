# PointsBot for Home Assistant

A Home Assistant custom integration for family points and allowance tracking. PointsBot automatically creates a points profile for every `person.*` entity in your Home Assistant instance, letting you manage chore lists, bonus tasks, and point balances entirely through the Home Assistant Actions interface — no additional frontend required.

## Features

- **Automatic person sync** — every `person.*` entity gets a points profile on setup; new family members are picked up on demand via the `sync_people` action
- **Two point types** — a `weekly_points` balance (current week) that resets every Monday, and a `total_points` balance (lifetime, rolls up on reset)
- **Per-person weekly allotment** — configure how many points each person starts with at the beginning of each week; defaults to 0 (opt-in)
- **Base tasks** — recurring informational chores with a done/not-done checkbox; no points on completion; checkboxes reset every Monday
- **Bonus tasks** — completable tasks worth a fixed point value; can be completed multiple times per week; completion counts reset every Monday
- **Manual point adjustments** — give or take points with a required reason; logged permanently in the audit trail
- **Permanent audit log** — every point-affecting event (adjustments, bonus completions, weekly rollovers) is written to an append-only history store that is never trimmed
- **Per-person sensor entity** — one `sensor.pointsbot_<person_slug>` entity per family member, with all current state in attributes, ready for dashboard cards

> **Note — Dashboard Cards (Phase 2):** No dashboard card component exists yet. Phase 2 (`ha-pointsbot-cards`, a separate repository) will deliver per-person Lovelace cards that consume these sensor entities. In the meantime, the full integration is usable via **Developer Tools → Actions** in the Home Assistant UI, and sensor state is inspectable via **Developer Tools → States**.

---

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant.
2. Go to **Integrations** → click the three-dot menu → **Custom repositories**.
3. Add `https://github.com/kylerm42/ha-pointsbot` as an **Integration** type repository.
4. Search for **PointsBot** in HACS Integrations and click **Download**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & Services → Add Integration** and search for **PointsBot**.

### Manual

1. Copy the `custom_components/pointsbot/` directory from this repository into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for **PointsBot**.

> PointsBot is a single-instance integration — only one config entry can exist at a time.

---

## Setup

After installation, adding the integration creates a config entry with no required input. PointsBot immediately syncs all existing `person.*` entities and creates one sensor entity per person. No restart is needed after adding a new family member — call the `pointsbot.sync_people` action to pick them up.

---

## Sensor Entities

One entity is created per person: `sensor.pointsbot_<person_slug>`

| Attribute | Description |
|---|---|
| State (`native_value`) | `total_points` — lifetime accumulated points, excluding the current week |
| `weekly_points` | Points earned in the current week |
| `weekly_allotment` | Points automatically granted at the start of each week |
| `base_tasks` | List of base task objects (`id`, `name`, `done`) |
| `bonus_tasks` | List of bonus task objects (`id`, `name`, `points_value`, `enabled`, `completions_this_week`) |
| `weekly_adjustments` | Manual point adjustments made this week (`id`, `amount`, `reason`, `timestamp`); cleared at rollover |
| `person_id` | The `person.*` entity ID this sensor corresponds to |
| `name` | Resolved live from the `person.*` entity |
| `picture` | Resolved live from the `person.*` entity |

Task `id` values (UUIDs) are required parameters for `update_task`, `delete_task`, `toggle_base_task`, and `complete_bonus_task`. Find them by inspecting the sensor's state attributes in **Developer Tools → States**.

---

## Services Reference

All nine services live under the `pointsbot` domain and are available in **Developer Tools → Actions**.

---

### `pointsbot.sync_people`

Re-synchronize all Home Assistant `person.*` entities into PointsBot user profiles. Creates a profile (weekly allotment = 0) for any person not yet known to PointsBot. Existing profiles are never modified or deleted. Run this after adding a new family member to Home Assistant.

*No parameters.*

---

### `pointsbot.adjust_points`

Apply a signed point adjustment to a person's current week's points. The adjustment is recorded permanently in the history log and appears in the person's weekly adjustments list until the next Monday rollover.

| Field | Type | Required | Description |
|---|---|---|---|
| `person_id` | string | Yes | The `person.*` entity ID (e.g. `person.alice`) |
| `amount` | integer | Yes | Signed delta; non-zero. Positive to award, negative to deduct. Range: -9999 to 9999 |
| `reason` | string | Yes | Human-readable explanation, recorded permanently (e.g. `Left dirty dishes in the sink`) |

---

### `pointsbot.set_weekly_allotment`

Set the number of points automatically granted to a person at the start of each week (during the Monday rollover). Takes effect at the **next rollover** — current-week points are not affected. Use `0` to opt a person out of the automatic weekly grant.

| Field | Type | Required | Description |
|---|---|---|---|
| `person_id` | string | Yes | The `person.*` entity ID |
| `amount` | integer | Yes | Non-negative integer (0–9999) |

---

### `pointsbot.add_task`

Add a new task to a person's task list.

- **Base tasks** are informational chores tracked with a done/not-done flag. Completing one awards no points; the flag resets every Monday.
- **Bonus tasks** are optional tasks worth a fixed point value. Completing one via `complete_bonus_task` immediately awards points. Completion counts reset every Monday.

| Field | Type | Required | Description |
|---|---|---|---|
| `person_id` | string | Yes | The `person.*` entity ID |
| `task_type` | `base` or `bonus` | Yes | Task type |
| `name` | string | Yes | Display name for the task |
| `points_value` | integer | If bonus | Point value awarded on completion (1–9999). Required for bonus tasks; must not be provided for base tasks. |

---

### `pointsbot.update_task`

Partially update an existing task. At least one of `name`, `points_value`, or `enabled` must be provided. Omitted fields are left unchanged. Providing `points_value` or `enabled` for a base task returns an error.

| Field | Type | Required | Description |
|---|---|---|---|
| `person_id` | string | Yes | The `person.*` entity ID |
| `task_type` | `base` or `bonus` | Yes | Must match the task's actual type |
| `task_id` | string (UUID) | Yes | The task's `id` field from sensor attributes |
| `name` | string | No | Updated display name |
| `points_value` | integer | No | Updated point value (bonus tasks only; 1–9999) |
| `enabled` | boolean | No | Set `false` to disable a bonus task without deleting it; `true` to re-enable (bonus tasks only) |

---

### `pointsbot.delete_task`

Permanently remove a task from a person's task list. This cannot be undone. Historical records in the point history log are retained. To temporarily prevent a bonus task from being completed without losing its definition, use `update_task` with `enabled: false` instead.

| Field | Type | Required | Description |
|---|---|---|---|
| `person_id` | string | Yes | The `person.*` entity ID |
| `task_type` | `base` or `bonus` | Yes | Must match the task's actual type |
| `task_id` | string (UUID) | Yes | The task's `id` field from sensor attributes |

---

### `pointsbot.toggle_base_task`

Flip the done/not-done state of a base task. No effect on points — base tasks are informational checkmarks only. All base task done flags reset every Monday.

| Field | Type | Required | Description |
|---|---|---|---|
| `person_id` | string | Yes | The `person.*` entity ID |
| `task_id` | string (UUID) | Yes | The base task's `id` field from sensor attributes |

---

### `pointsbot.complete_bonus_task`

Record a completion of a bonus task. Immediately adds the task's `points_value` to the person's current week's points and increments the task's weekly completion counter. The completion is recorded permanently in the history log. Returns an error if the task is currently disabled — use `update_task` with `enabled: true` to re-enable it first.

| Field | Type | Required | Description |
|---|---|---|---|
| `person_id` | string | Yes | The `person.*` entity ID |
| `task_id` | string (UUID) | Yes | The bonus task's `id` field from sensor attributes |

---

### `pointsbot.run_weekly_reset`

Manually trigger the weekly rollover for all registered persons, exactly as it runs automatically every Monday at midnight. For each person:

- `weekly_points` is added to `total_points`
- `weekly_points` resets to the person's `weekly_allotment` (or 0 if not set)
- All base task done flags reset to `false`
- All bonus task weekly completion counts reset to `0`
- The current week's manual adjustment list is cleared (the permanent history log is never modified)
- A `weekly_rollover` event is appended to the permanent history log

Use this for testing, to recover from a missed Monday rollover, or to manually advance the week during onboarding.

*No parameters.*

---

## Weekly Rollover

The automatic rollover fires every **Monday at local midnight**. It is equivalent to calling `pointsbot.run_weekly_reset` manually and follows the same logic described above for every registered person.

---

## Point History Log

Every point-affecting event — manual adjustments, bonus task completions, and weekly rollovers — is written to a separate, append-only history store (`pointsbot_history`). This log is never trimmed or cleared. It is not surfaced directly on any entity in Phase 1; Phase 2 dashboard cards will provide a UI for browsing it.

---

## Development

See [AGENTS.md](AGENTS.md) for module layout, data model details, and developer guidance.

```bash
./dev.sh up      # Start HA dev instance with integration mounted
./dev.sh logs    # Tail logs
./dev.sh restart # Restart HA
./dev.sh down    # Stop everything
```

Access the dev instance at `http://localhost:8123`.
