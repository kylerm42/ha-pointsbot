# AGENTS.md

This file provides guidance to AI agents and developers working with code in this repository.

## Project Overview

**PointsBot** is a Home Assistant custom integration (domain: `pointsbot`) for family points and allowance tracking. It auto-syncs every `person.*` entity in Home Assistant into a points profile, supports per-person weekly allotments, base and bonus task lists, manual point adjustments with audit logging, and a weekly Monday-midnight rollover — all operable entirely through HA service calls (Developer Tools → Actions) with no required frontend.

**Backend:** Fully functional, including person-owned rewards and banked-only redemption.
**Frontend:** The companion cards repository includes the standard person card and the dedicated person-rewards card.

**Relationship to `ha-chorebot`:** PointsBot is a sibling integration by the same author. It intentionally drops OAuth/sync backends, template/instance recurrence, and multi-file storage sprawl from ChoreBot. Patterns reused: `Store`-backed JSON persistence, `async_track_time_change` for scheduled resets, `person.*` auto-sync, and the Docker Compose dev workflow. See the overview spec for a full list of accepted/rejected patterns.

---

## Module Layout

All integration source files live under `custom_components/pointsbot/`.

| File | Responsibility |
|---|---|
| `manifest.json` | `domain=pointsbot`, `single_config_entry: true`, no external requirements |
| `const.py` | All constants: `DOMAIN`, storage keys, `STORAGE_VERSION`, task type strings, event type strings, service name strings, dispatcher signal format strings |
| `config_flow.py` | `PointsBotConfigFlow(ConfigFlow)`: single-instance setup step that prompts for `title` (TextSelector, default `"PointsBot"`) and `icon` (IconSelector, default `"mdi:star-circle"`); both required and validated non-empty, then stored on `entry.data`. The `icon` is exposed as a sensor attribute so the frontend card can render it next to weekly points. Aborts with `single_instance_allowed` if an entry already exists. No options flow. |
| `store.py` | `PointsBotStore`: owns the `pointsbot_data` Store file; in-memory dict cache; `asyncio.Lock` on all mutation methods; CRUD for users, tasks, weekly adjustments, and person-owned rewards; banked-only redemption; rollover application |
| `history_log.py` | `PointsBotHistoryLog`: owns the `pointsbot_history` Store file; append-only `async_append(event: dict)` with auto-assigned UUID and UTC timestamp; no size cap, never trimmed |
| `people_sync.py` | `async_sync_people(hass, store, entry_id)`: enumerates `hass.states.async_all("person")`; upserts a `PointsBotUser` profile for each (create with `weekly_allotment: 0` if new, no-op if already exists); dispatches `SIGNAL_POINTSBOT_NEW_PERSON` for newly discovered persons; never deletes a user |
| `weekly_reset.py` | `async_perform_weekly_reset(hass, store, history_log, entry_id)`: iterates all users; applies rollover (see data model); appends a `weekly_rollover` event per user to the history log; dispatches `SIGNAL_POINTSBOT_UPDATE` to trigger sensor refresh |
| `sensor.py` | `PointsBotUserSensor(SensorEntity)`: one instance per user; `unique_id = f"pointsbot_{person_id}"`; `native_value = total_points` (banked balance); attributes include weekly state, tasks, adjustments, defensive reward snapshots, `person_id`, and live `name`/`picture`; dynamic entity creation via `SIGNAL_POINTSBOT_NEW_PERSON` |
| `services.py` | Handler functions for all 12 services, including `manage_reward`, `redeem_reward`, and `delete_reward`; centralized validation, history append, and update dispatch helpers |
| `services.yaml` | Declarative service schema (field names, selectors, descriptions, examples) for all 10 services; this is the primary end-user documentation for Phase 1 |
| `__init__.py` | `async_setup_entry`: instantiate `PointsBotStore` and `PointsBotHistoryLog` → load both stores → `async_sync_people` → `async_forward_entry_setups` (SENSOR platform) → `async_register_services` → register `async_track_time_change` callback (daily at 00:00:00; Monday guard inside callback) → store unsubscribe callback for unload cleanup |

---

## Data Model: Two-Store Design

PointsBot uses exactly two `homeassistant.helpers.storage.Store` files, both at `STORAGE_VERSION = 1`.

### `pointsbot_data` — current state, entity-facing

Keyed by `person_id`. This is the source of truth for all sensor entity state.

```jsonc
{
  "users": {
    "person.alice": {
      "weekly_allotment": 50,        // opt-in per user; defaults to 0 on creation; no global default
      "total_points": 340,           // banked balance; current-week points join at rollover
      "weekly_points": 12,           // current week only
      "base_tasks": [
        { "id": "uuid", "name": "Make bed", "done": false }
      ],
      "bonus_tasks": [
        { "id": "uuid", "name": "Vacuum living room", "points_value": 10,
          "enabled": true, "completions_this_week": 2 }
      ],
      "weekly_adjustments": [
        { "id": "uuid", "amount": -5, "reason": "Left dishes out",
          "timestamp": "2026-07-10T14:00:00+00:00" }
      ],
      "rewards": [
        { "id": "uuid", "person_id": "person.alice", "name": "Movie night", "cost": 50,
          "icon": "mdi:movie-open", "enabled": true, "description": "Choose a film",
          "created": "2026-07-29T00:00:00+00:00", "modified": "2026-07-29T00:00:00+00:00" }
      ]
    }
  }
}
```

**Important:** `name`, `picture`, and `entity_id` are intentionally absent from stored user profiles. They are resolved live from `hass.states.get(person_id)` on every sensor render to avoid stale cached values.

### `pointsbot_history` — audit log, never entity-facing

Append-only. Never trimmed. Not surfaced on any entity. One event per point-affecting operation across all users.

```jsonc
{
  "events": [
    {
      "id": "uuid",
      "person_id": "person.alice",
      "event_type": "manual_adjustment",   // | bonus_completion | weekly_rollover | reward_redemption
      "amount": -5,
      "reason": "Left dishes out",          // manual_adjustment only
      "task_id": "uuid",                    // bonus_completion only
      "task_name": "Vacuum living room",    // bonus_completion only
      "rolled_over_amount": 42,             // weekly_rollover only (total_points after rollover)
      "new_allotment": 50,                  // weekly_rollover only
      "reward_id": "uuid",                 // reward_redemption only
      "reward_name": "Movie night",        // reward_redemption only
      "cost": 50,                           // reward_redemption only
      "timestamp": "2026-07-10T14:00:00+00:00"
    }
  ]
}
```

### Consistency invariant

Every operation that appends to `pointsbot_history` also updates the relevant fields in `pointsbot_data` in the same call. The two stores are always updated together and cannot drift relative to each other.

---

## Weekly Rollover Mechanism

The scheduled rollover fires every **Monday at local midnight** via `async_track_time_change(hass, callback, hour=0, minute=0, second=0)`. The callback guards against non-Monday days with `date.weekday() == 0` before proceeding. The identical logic is also exposed as the `pointsbot.run_weekly_reset` service for manual triggering.

For each user, the rollover performs the following atomically (under `PointsBotStore`'s `asyncio.Lock`):

1. `total_points += weekly_points` (roll current week into lifetime total)
2. `weekly_points = weekly_allotment` (reset to this user's configured allotment, or 0 if unset)
3. All `base_tasks[].done` → `false`
4. All `bonus_tasks[].completions_this_week` → `0`
5. `weekly_adjustments` list → cleared (current week's adjustments are gone from the entity; permanent record remains in `pointsbot_history` unmodified)
6. A `weekly_rollover` event is appended to `pointsbot_history`
7. `SIGNAL_POINTSBOT_UPDATE` is dispatched so affected sensor entities call `async_write_ha_state()`

---

## Service Catalog

Twelve services are registered under the `pointsbot` domain. See `custom_components/pointsbot/services.yaml` for the full declarative schema and field descriptions.

| Service | Parameters | Notes |
|---|---|---|
| `sync_people` | *(none)* | Manual re-sync trigger; additive upsert only, never deletes |
| `adjust_points` | `person_id`, `amount` (non-zero signed int), `reason` (non-empty string) | Applies to `weekly_points` immediately; logs `manual_adjustment` to both `weekly_adjustments` and `pointsbot_history` |
| `set_weekly_allotment` | `person_id`, `amount` (int ≥ 0) | Takes effect at next rollover only; no retroactive effect on current `weekly_points` |
| `add_task` | `person_id`, `task_type` (`base`\|`bonus`), `name`, `points_value` (required if bonus) | Creates task with appropriate defaults |
| `update_task` | `person_id`, `task_type`, `task_id`, `name` (opt), `points_value` (opt, bonus only), `enabled` (opt, bonus only) | Partial update; passing `points_value` or `enabled` for a base task raises `ServiceValidationError` |
| `delete_task` | `person_id`, `task_type`, `task_id` | Hard delete; historical `pointsbot_history` entries are retained |
| `toggle_base_task` | `person_id`, `task_id` | Flips `done`; no point effect |
| `complete_bonus_task` | `person_id`, `task_id` | Rejects if `enabled: false`; increments `completions_this_week`, adds `points_value` to `weekly_points`, logs `bonus_completion` |
| `uncomplete_bonus_task` | `person_id`, `task_id` | Rejects if no completion to undo; decrements `completions_this_week`, subtracts `points_value` from `weekly_points`, logs `bonus_uncompletion` |
| `run_weekly_reset` | *(none)* | Manually triggers the identical rollover path as the scheduled job, for all users |
| `manage_reward` | `person_id`, `name`, `cost`, `icon`; optional `reward_id`, `description` | Creates or updates a person-owned reward; updates may reassign ownership |
| `redeem_reward` | `person_id`, `reward_id` | Deducts cost from banked `total_points` only and logs `reward_redemption` |
| `delete_reward` | `reward_id` | Permanently deletes a reward definition; history is retained |

---

## Design Decisions

Key decisions and their rationale are documented in the specs. Consult these before making architectural changes:

| Decision | Summary |
|---|---|
| No global weekly allotment default | Each user defaults to `0` on creation; no integration-wide fallback; no options flow needed |
| No cached name/picture | Resolved live from `person.*` entity at render time to avoid staleness |
| No auto-deletion of users | A user whose `person.*` entity disappears is never auto-deleted (prevents accidental data loss) |
| `weekly_adjustments` cleared at rollover | Current week's adjustments are display-only; permanent record lives in `pointsbot_history` |
| `update_task` raises on base+bonus-only fields | Explicit `ServiceValidationError` for mismatched field combinations (better UX than silent ignore) |
| No `DataUpdateCoordinator` | No external polling; entities refresh via `async_write_ha_state()` directly after each store mutation |
| Concurrent write safety | `PointsBotStore` uses a single `asyncio.Lock` across all mutation methods |
| Configurable card accent color | `CardConfig.accent_color` (#RRGGBB) propagates to all child components via `--pointsbot-accent-color` / `--pointsbot-accent-text-color` CSS custom properties; defaults to `#B29FE8`; contrast text auto-flips via WCAG luminance (>0.5 → `#17151d`, else `#ffffff`) |

---

## Development Environment

```bash
./dev.sh up      # Start HA with integration mounted
./dev.sh logs    # View logs
./dev.sh restart # Restart HA service
./dev.sh down    # Stop everything
```

Access at `http://localhost:8123`. Configuration and runtime data live in `dev-config/` (gitignored).

**Testing Python changes:** Restart HA from Developer Tools → Server Controls.
**Testing services:** Developer Tools → Actions.

---

## Tests

Tests live under `tests/`. The suite uses `pytest-asyncio` (`asyncio_mode = auto`) with plain `MagicMock` hass and `FakeStore` fixtures (no `pytest-homeassistant-custom-component` dependency).

| File | Coverage |
|---|---|
| `tests/test_store.py` | `PointsBotStore` CRUD, rollover math, edge cases |
| `tests/test_history_log.py` | `PointsBotHistoryLog` load, append, uncapped growth, UUID uniqueness |
| `tests/test_phase1b.py` | `people_sync`, `weekly_reset`, sensor entity behavior, `async_setup_entry` orchestration |
| `tests/test_services.py` | All 12 service handlers, reward validation/history/dispatch, `ServiceValidationError` cases, sensor round-trip assertions, concurrent write edge cases |

Run tests: `pytest` from the repo root.

---

## Frontend Submodule (`frontend/`)

The `frontend/` directory is a git submodule pointing at [`ha-pointsbot-cards`](https://github.com/kylerm42/ha-pointsbot-cards) — the companion Lovelace card repository. It is not part of the Python integration; it is wired here solely to support the local development workflow.

### Submodule basics

```bash
# Initialize after a fresh clone (dev.sh up does this automatically)
git submodule update --init --recursive

# Update to latest ha-pointsbot-cards commit
git submodule update --remote frontend

# After updating, commit the new gitlink in ha-pointsbot
git add frontend && git commit -m "chore: update frontend submodule to <hash>"
```

### Card-builder dev workflow

`docker-compose.yml` defines a `card-builder` service that mounts `frontend/` and runs `npm run watch` inside a `node:20` container. The build output (`frontend/dist/`) is mounted into the HA container at `/config/www/community/pointsbot-cards/`, mimicking the path HACS would use for a frontend plugin.

When adding PointsBot Cards as a Lovelace resource in the dev instance, use:

```yaml
url: /local/community/pointsbot-cards/pointsbot-cards.js
type: module
```

`./dev.sh up` auto-initializes the submodule if `frontend/src` is missing, so a fresh clone requires no manual submodule step before starting the dev environment.

### Frontend module layout

All card source files live under `frontend/src/` (i.e., `ha-pointsbot-cards/src/`).

| File | Responsibility |
|---|---|
| `pointsbot-person-card.ts` | Main card element; registers `custom:pointsbot-person-card`; reads `hass.states` on every `hass` setter update; dispatches all writes via `hass.callService`; exposes `getConfigForm()` for the Lovelace visual editor |
| `pointsbot-person-rewards-card.ts` | Selector card; discovers PointsBot person sensors, filters/sorts reward snapshots, and calls only the three reward services; supports visual-editor configuration and banked-only redemption feedback |
| `collapsible-section.ts` | Reusable expand/collapse element used for base tasks, bonus tasks, and adjustments sections |
| `adjust-points-dialog.ts` | Dialog element for the manual point adjustment form; validates amount (non-zero integer) and reason (non-empty) before calling `pointsbot.adjust_points` |
| `types.ts` | Shared TypeScript interfaces mirroring the Phase 1 sensor attribute contract |

Tests live alongside sources as `*.test.ts` files and run via `pnpm test` (Vitest + happy-dom, no browser required).

---

## Spec Files

For full architecture rationale, data model decisions, and implementation history:

- `~/.local/share/specs/ha-pointsbot/proposed/20260711-pointsbot-overview/feature-spec.md` — High-level architecture, phasing, rejected patterns
- `~/.local/share/specs/ha-pointsbot/proposed/20260711-pointsbot-phase1-backend/feature-spec.md` — Phase 1 detailed spec; Section 8 (Implementation Notes) contains a per-phase record of decisions, deviations, and test results
- `~/.local/share/specs/ha-pointsbot/proposed/20260715-pointsbot-phase2-frontend/feature-spec.md` — Phase 2 frontend spec; Section 8 covers Phase 2a–2c implementation notes, deviations, and the manual QA checklist
- `~/.local/share/specs/ha-pointsbot/proposed/20260729-pointsbot-rewards/feature-spec.md` — Rewards implementation, banked-only semantics, and Phase 4 validation notes
