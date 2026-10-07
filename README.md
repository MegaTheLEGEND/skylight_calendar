# Skylight for Home Assistant

> ⚠️ **v3.0.0 is a breaking change.** The integration domain was renamed `skylight_calendar` → `skylight`. Every entity ID, service name, and config entry needs to be redone.
>
> **Upgrade procedure:**
> 1. In HA: **Settings → Devices & Services → Skylight → three-dot menu → Delete**
> 2. HACS: **Skylight → three-dot menu → Redownload → v3.0.0**
> 3. Restart Home Assistant
> 4. **Settings → Devices & Services → Add Integration → Skylight** — sign in again
> 5. Update any automations/dashboards referencing `skylight_calendar.*` entity IDs or the `skylight_calendar.upload_media` service to use `skylight.*` and `skylight.upload_media`
>
> The rename brings the domain in line with the display name and repo. It also removes the last "calendar" naming artifact from what is now a full-platform integration (calendar + chores + meals + tasks + rewards + lists + frame device controls + photo upload).

Full [Skylight Calendar Frame](https://www.ourskylight.com/) integration for Home Assistant — calendar events, chores (whole-frame + per-family-member), meals (per-slot + full recipe detail), task box, shopping / to-do lists, reward stars, and frame device controls (brightness, slideshow speed, sleep mode).

> ⚠️ v2.x is a breaking change from any pre-fork release. Auth is now proper OAuth2 (authorization code + PKCE) — you sign in through your browser during the config flow, no more manual token capture. Existing installations must be removed and re-added.

---

## Features

| HA Platform | Entities | Notes |
|---|---|---|
| `calendar` | one aggregate + one per Skylight source calendar | Rolling ±14 / +60 day window. Standard `calendar.get_events` service works. Create events on the aggregate entity; edit and delete on any writable source calendar. |
| `todo` | one per Skylight list (Grocery, To-Do, custom lists) + one chore queue per family member | Full create / rename / complete / delete round-trip. Chores created on a member's queue are assigned to them, and the due date sets the chore's day. |
| `sensor` | see below | Rich `extra_state_attributes` for use in Lovelace + automations. |
| `switch` | sleep mode, night light, show captions, show heart, blur effect, start sound, side by side | Direct write to the device. |
| `number` | brightness (0–255), slideshow speed (0–240s), night light brightness, sleep sound volume | Direct write to the device. |
| `time` | sleeps at, wakes at | The frame's sleep schedule. |
| `binary_sensor` | screen asleep, activated | Read-only. "Screen asleep" is whether the display is *currently* off; the sleep mode switch is whether the schedule is armed. |
| `image` | current frame photo | |

Entities are only created for settings your frame actually reports — Skylight's device payload varies by hardware, and an absent key means "unsupported here" rather than "off". A handful of settings (`sleep_mode`, `nightlight_color`, `sleep_sound`, `slideshow_style`) are exposed as read-only diagnostic sensors rather than selects, because only one value of each enum has ever been observed and a guessed option list would produce controls that fail.

### Skylight Buddy

A Buddy paired to the frame (a second device with `role: buddy`) gets its own HA device, named as in the app:

| HA Platform | Entities | Notes |
|---|---|---|
| `switch` | night light; one `<label> alarm` per alarm | The alarm switch enables or disables that alarm. Its attributes carry `time`, `rrule`, `fires_on`, `sound`, `volume` and `snoozable`. |
| `number` | night light brightness, sleep sound volume; `<label> alarm volume` per alarm | |
| `time` | `<label> alarm time` per alarm | Sent as `HH:MM`. |

Alarms come from `/api/frames/{fid}/devices/{did}/alarms`. Alarms created in the app appear on the next frame poll. A Buddy's `wakes_at` / `sleeps_at` are separate from its alarms (they read `null` on a Buddy).

### Sensor entities

| Entity | Native value | Attributes |
|---|---|---|
| `sensor.<frame>_chores_today` | count | `chores[]` (id, summary, description, emoji, status, reward_points, start_date, start_time, completed_on, recurrence_rrule, routine, assignee_id, assignee) + `by_status` |
| `sensor.<person>_chores_today` | count per family member | Same `chores[]` shape, filtered to that person |
| `sensor.<frame>_meals_today` | count | `meals[]` (id, summary, category, recipe_title, recipe_description, note, recurrence_rrule, instances) + `by_slot` |
| `sensor.<frame>_breakfast_today`<br>`sensor.<frame>_lunch_today`<br>`sensor.<frame>_dinner_today`<br>`sensor.<frame>_snack_today` | comma-joined meal names, or `"none"` | Full per-meal detail in `meals[]` |
| `sensor.<person>_stars` | current star balance | Per family member |
| `sensor.<frame>_task_box` | count | `items[]` — reusable chore templates the frame pulls from when adding an ad-hoc chore (summary, emoji, reward_points, routine) |
| `image.<frame>_latest_photo` | most recent photo | Skylight scopes `/messages` to the authenticated user — you only see photos uploaded by the account HA authorized with, not photos from other family members using the same frame. |

---

## Installation (HACS)

1. HACS → Integrations → three-dot menu → **Custom repositories**
2. Repository: `https://github.com/MegaTheLEGEND/skylight_calendar` — Category: **Integration**

   [![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=megathelegend&repository=skylight_calendar&category=integration)
3. Search **Skylight** → Download → pick the latest release.
4. Restart Home Assistant.
5. Settings → Devices & Services → **Add Integration** → **Skylight**.
6. Sign in through the browser popup when prompted.

## Manual installation

Copy `custom_components/skylight/` into `config/custom_components/skylight/` and restart HA.

## Polling intervals

Settings → Devices & Services → Skylight → **Configure** sets how often each kind of data is fetched, in seconds (30–7200):

| Option | Default | Covers |
|---|---|---|
| Calendar | 300 | Calendar events and connected calendars |
| Lists | 120 | Shopping and to-do lists |
| Chores, meals and rewards | 300 | Chores, meal plans, star totals, task box |
| Frame settings | 600 | Brightness, sleep schedule, other device settings |
| Photos | 900 | Latest frame photo |

Saving reloads the integration. Frame settings and connected-calendar lists are fetched conditionally (`If-None-Match`), so polling those more often costs little; chores, lists, rewards and photos always fetch in full, because Skylight's cache validators don't reliably move when those records change.

---

## Reading item details in Lovelace

Every sensor stashes the full record for each item in `extra_state_attributes`. Use a **Markdown card** with Jinja templates to render whatever detail you want — the same pattern works for chores, meals, task box, todo lists, and calendar events.

Replace `slickfam` in every example below with your own frame name (whatever the sensor entity ID actually says — check Developer Tools → States if you're not sure).

### Today's chores (whole family)

```yaml
type: markdown
title: Today's chores
content: |
  {% for c in state_attr('sensor.slickfam_chores_today', 'chores') or [] %}
  ### {{ c.emoji or '📋' }} {{ c.summary }}
  {% if c.description %}> {{ c.description }}{% endif %}
  - **Assigned to:** {{ c.assignee or 'Anyone' }}
  - **Reward:** ⭐ {{ c.reward_points or 0 }}
  - **Status:** {{ c.status }}{% if c.start_time %} · Due at {{ c.start_time }}{% endif %}
  {% if c.recurrence_rrule %}- **Repeats:** `{{ c.recurrence_rrule }}`{% endif %}

  {% endfor %}
```

### Today's chores (just one person)

```yaml
type: markdown
title: Devin's chores today
content: |
  {% set chores = state_attr('sensor.devin_chores_today', 'chores') or [] %}
  {% if chores | length == 0 %}
  🎉 Nothing on your list today.
  {% else %}
  {% for c in chores %}
  - {{ c.emoji or '☐' }} **{{ c.summary }}** {% if c.status == 'complete' %}✅{% endif %}
    {% if c.reward_points %}⭐ {{ c.reward_points }}{% endif %}
    {% if c.start_time %}· {{ c.start_time }}{% endif %}
  {% endfor %}
  {% endif %}
```

### Today's meals (all slots)

```yaml
type: markdown
title: Today's meals
content: |
  {% for m in state_attr('sensor.slickfam_meals_today', 'meals') or [] %}
  ### {{ m.category }} — {{ m.summary }}
  {% if m.recipe_title and m.recipe_title != m.summary %}*Recipe:* **{{ m.recipe_title }}**{% endif %}
  {% if m.recipe_description %}{{ m.recipe_description }}{% endif %}
  {% if m.description %}{{ m.description }}{% endif %}
  {% if m.note %}> Note: {{ m.note }}{% endif %}

  {% endfor %}
```

### Per-slot meals (dashboard-friendly)

The per-slot sensors expose the meal name as their state, so entity cards work directly:

```yaml
type: glance
title: Today's menu
entities:
  - entity: sensor.slickfam_breakfast_today
    name: Breakfast
  - entity: sensor.slickfam_lunch_today
    name: Lunch
  - entity: sensor.slickfam_dinner_today
    name: Dinner
```

For full detail on a single slot:

```yaml
type: markdown
title: Dinner tonight
content: |
  {% for m in state_attr('sensor.slickfam_dinner_today', 'meals') or [] %}
  ### {{ m.recipe_title or m.summary }}
  {% if m.recipe_description %}{{ m.recipe_description }}{% endif %}
  {% if m.note %}> {{ m.note }}{% endif %}
  {% endfor %}
```

### Task box (reusable chore templates)

```yaml
type: markdown
title: Task Box ({{ states('sensor.slickfam_task_box') }} templates)
content: |
  {% for t in state_attr('sensor.slickfam_task_box', 'items') or [] %}
  - {{ t.emoji or '☐' }} **{{ t.summary }}**{% if t.reward_points %} · ⭐ {{ t.reward_points }}{% endif %}{% if t.routine %} · _routine_{% endif %}
  {% endfor %}
```

### To-do / grocery lists

The `todo` platform is best consumed via the native **Todo list** card, which supports adding, checking, and deleting items directly:

```yaml
type: todo-list
entity: todo.grocery_list
```

For a read-only view alongside other data in the same dashboard, use a markdown template:

```yaml
type: markdown
title: Grocery list
content: |
  {% for item in state_attr('todo.grocery_list', 'items') or [] %}
  - {% if item.status == 'completed' %}~~{{ item.summary }}~~{% else %}☐ {{ item.summary }}{% endif %}
  {% endfor %}
```

> **Note on todo detail:** Skylight list items only carry a `summary` label — there is no per-item description, due date, or reward points on Skylight's side. If you need richer item metadata (recurrence, points, assignment), use a chore instead.

### Reward stars

The star sensors have a numeric state that's already dashboard-friendly:

```yaml
type: glance
title: Family stars
entities:
  - sensor.devin_stars
  - sensor.tabi_stars
  - sensor.logan_stars
```

### Calendar events

Use the built-in Calendar card or a community card like `calendar-card-pro`:

```yaml
type: calendar
entities:
  - calendar.slickfam_calendar
```

---

## Automation examples

**Announce chores at breakfast:**
```yaml
alias: Morning chores announcement
trigger:
  - platform: time
    at: "07:30:00"
action:
  - service: tts.google_translate_say
    data:
      entity_id: media_player.kitchen_display
      message: >
        Good morning. Today's chores:
        {% for c in state_attr('sensor.slickfam_chores_today', 'chores') %}
        {{ c.assignee or 'Someone' }}: {{ c.summary }}.
        {% endfor %}
```

**Notify when someone hits a star milestone:**
```yaml
alias: Star milestone
trigger:
  - platform: numeric_state
    entity_id: sensor.logan_stars
    above: 100
action:
  - service: notify.family
    data:
      message: "🎉 Logan just crossed 100 stars!"
```

**Dinner reminder with the actual dish name:**
```yaml
alias: Dinner reminder
trigger:
  - platform: time
    at: "17:00:00"
condition:
  - condition: template
    value_template: "{{ states('sensor.slickfam_dinner_today') != 'none' }}"
action:
  - service: notify.family
    data:
      message: "Dinner in one hour: {{ states('sensor.slickfam_dinner_today') }}"
```

**Auto-dim the frame at night:**
```yaml
alias: Skylight night dim
trigger:
  - platform: time
    at: "21:00:00"
action:
  - service: number.set_value
    target:
      entity_id: number.slickfam_brightness
    data:
      value: 40
```

---

## Uploading photos & videos

The integration exposes a `skylight.upload_media` service that pushes an image or short video to your frame — same path the mobile app uses (temp AWS credentials → SigV4-signed PUT to S3 → notification to Skylight).

**Supported formats:** jpg, jpeg, png, gif, heic, heif, webp, mp4, mov, m4v.

**Path requirements:** The file must be readable by Home Assistant. Anything under `/config` works, or add the parent directory to `homeassistant.allowlist_external_dirs` in `configuration.yaml`.

```yaml
service: skylight.upload_media
data:
  file_path: /config/www/family_photo.jpg
  caption: "Sunday brunch"      # optional
  frame_id: "5377413"           # optional, only needed with multiple frames
```

**Automation example — send a snapshot from a doorbell trigger:**

```yaml
alias: Doorbell → Skylight
trigger:
  - platform: state
    entity_id: binary_sensor.front_door
    to: "on"
action:
  - service: camera.snapshot
    target:
      entity_id: camera.front_door
    data:
      filename: "/config/www/skylight_doorbell.jpg"
  - service: skylight.upload_media
    data:
      file_path: "/config/www/skylight_doorbell.jpg"
      caption: "Someone at the door!"
```

**Caveat on captions:** Free Skylight accounts have captions server-side-disabled (`plus_gated_content.captions = false`). The caption field is still sent, but the frame ignores it. Skylight Plus subscribers get real caption display.

After a successful upload the `image.<frame>_latest_photo` entity refreshes within a few seconds (the service kicks the photos coordinator immediately instead of waiting for the next 30s poll).

---

## Services

Every service takes an optional `frame_id`, which is only required when you have more than one frame configured. Each one refreshes the relevant coordinator on success, so entities catch up immediately instead of waiting for the next poll.

| Service | What it does |
|---|---|
| `skylight.upload_media` | Push a photo or short video to the frame (see above). |
| `skylight.create_chore` | Add a chore to the chore chart, assigned to one or more family members (required). Skylight creates one chore per assignee. |
| `skylight.create_task` | Add an unscheduled item to the Task Box for the frame to assign to a day later. |
| `skylight.create_list` | Create a new shopping or to-do list. |
| `skylight.delete_list` | Permanently delete a list and its items. |
| `skylight.create_reward` | Add a reward redeemable with earned stars. Skylight stores one reward per family member, so this creates one per ID given. |
| `skylight.redeem_reward` | Spend a family member's stars on a reward. |
| `skylight.create_recipe` | Add a recipe to the meal planner. |
| `skylight.plan_meal` | Schedule a meal into a slot on a given day. |
| `skylight.add_recipe_to_grocery_list` | Push a recipe's ingredients onto the default grocery list. |

**Finding IDs.** The `category_id` for a family member, `meal_category_id` for a meal slot, and `recipe_id` all come off the sensor attributes documented above — for example `sensor.<frame>_chores_today` exposes `assignee_id` per chore, and `sensor.<frame>_meals_today` exposes the recipe and slot for each planned meal. A list's `list_id` is the trailing segment of the matching todo entity's unique ID.

```yaml
# Assign Saturday's bin duty — one chore each for two family members
service: skylight.create_chore
data:
  summary: Take out the bins
  start: "2026-09-05"
  start_time: "17:30:00"
  assignees: ["Robbie", "Josh"]   # or category_ids: ["23469502"]
  description: Green bin this week
```

Names are matched case-insensitively against the frame's profiles, and only against real people — a frame's category list also contains calendar buckets like `US Holidays` or `Robbie's Calendar`, and those are rejected rather than silently assigned. An unknown name lists the valid ones back at you.

```yaml
# Plan Tuesday dinner and send the ingredients to the grocery list
- service: skylight.plan_meal
  data:
    date: "2026-09-01"
    meal_category_id: "9115872"
    recipe_id: "1234567"
- service: skylight.add_recipe_to_grocery_list
  data:
    recipe_id: "1234567"
```

**Creating calendar events** uses the standard HA `calendar.create_event` service against the aggregate `calendar.<frame>_calendar` entity. Skylight puts events with no explicit calendar target on whichever source calendar is flagged `default_for_new_events`. Editing and deleting work against any source calendar Skylight reports as writable; read-only subscriptions (holiday feeds, `webcal` imports like a council rubbish schedule) don't advertise those features at all.

```yaml
service: calendar.create_event
target:
  entity_id: calendar.skylight_frame_calendar
data:
  summary: Dentist
  start_date_time: "2026-09-01 14:00:00"
  end_date_time: "2026-09-01 15:00:00"
  location: Dr Smith's office
```

---

## Authentication

The config flow runs a normal OAuth2 authorization-code + PKCE grant. You'll be redirected to Skylight's sign-in page, log in with your Skylight credentials, and consent — HA takes it from there and stores the refresh token. On 401s it rotates automatically and persists the new pair back to the config entry. You should never need to redo this unless you revoke the grant server-side.

### Adding a second frame

Each frame gets its own HA config entry. Run **Add Integration → Skylight** again — the picker step lists frames not already configured.

---

## Troubleshooting

Enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.skylight: debug
```

| Symptom | Cause / fix |
|---|---|
| Auth fails on config flow | Try a different browser / clear Skylight cookies. |
| Calendar entity has no events | Verify events exist in the ±14 / +60 day window on your frame. |
| Todo entities missing after adding a new list on the frame | Reload the integration to force a refresh, or wait for the 2-minute polling interval. |
| Star sensors missing for a profile | The profile must have `linked_to_profile=true` on its category — ensure the profile is fully set up on the frame. |
| `Task box` sensor shows 0 | Task box only surfaces user-added reusable chores; if your frame doesn't use the task-box feature this is expected. |

---

## What changed in v2.1.0

- Full platform coverage: per-source calendars, per-family-member chore sensors, per-slot meal sensors, `image` / `switch` / `number` platforms for frame device controls, reconfigure flow.
- OAuth2 rewritten to proper authorization-code + PKCE — no more manual DevTools token capture.
- Correct routing of brightness / slideshow_speed / sleep_mode through the device endpoint (not the frame endpoint).

## What changed in v2.0.0

- Full rewrite around Skylight's OAuth2 API (Bearer + `Skylight-Api-Version: 2026-05-01`).
- New `todo` and `sensor` platforms.
- Automatic refresh-token rotation persisted to the config entry.
- Discovered / corrected several API-shape mismatches vs. older reverse-engineered notes (lists use `attributes.label`, chores use `after`/`before`, reward points is a flat list).

---

## Credits

[MegaTheLEGEND/skylight_calendar](https://github.com/MegaTheLEGEND/skylight_calendar). OAuth2 rewrite + platform expansion by [@devinslick](https://github.com/devinslick). (Thank you so much!)

## License

MIT — see [LICENSE](LICENSE).
