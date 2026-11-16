# Skylight for Home Assistant

A custom [Home Assistant](https://www.home-assistant.io/) integration for the [Skylight Calendar Frame](https://www.ourskylight.com/). It brings your frame's calendar events, chores, meals, Task Box, shopping and to-do lists, reward stars, and device settings (brightness, slideshow speed, sleep mode) into Home Assistant, and lets you send photos to the frame.

It talks to Skylight's cloud service, so it works with any Home Assistant installation that can reach the internet. The frame doesn't need to be on the same network.

> **Upgrading from v2.x or an older fork?** v3.0.0 is a breaking change. Read [Upgrading](#upgrading) before you update.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation): [HACS](#option-a-hacs-recommended) · [Manual](#option-b-manual-installation)
- [Setup](#setup)
- [What you get](#what-you-get)
- [Dashboard examples](#dashboard-examples) · [Automation examples](#automation-examples)
- [Actions](#actions) · [Chores](#working-with-chores) · [Photos](#uploading-photos-and-videos) · [Calendar events](#creating-calendar-events)
- [Troubleshooting](#troubleshooting)
- [Upgrading](#upgrading)
- [Development and tests](#development-and-tests)

---

## Requirements

- Home Assistant **2024.1 or newer**, with any installation type: Home Assistant OS (Green, Yellow, Raspberry Pi, a virtual machine), Supervised, Container (Docker) or Core (Python virtual environment).
- A Skylight account that can sign in to the Skylight app, with at least one frame.
- [HACS](https://hacs.xyz/) if you want one-click installs and updates (optional).

The YAML examples use current Home Assistant syntax. On releases before 2024.8, write `service:` where the examples say `action:`. Before 2024.10, write `trigger:` with `- platform: time` where the examples say `triggers:` with `- trigger: time`.

---

## Installation

Every step works from a web browser on any computer, phone or tablet that can open Home Assistant.

### Option A: HACS (recommended)

1. In Home Assistant, open **HACS**, then the three-dot menu (top right) → **Custom repositories**.
2. Add `https://github.com/MegaTheLEGEND/skylight_calendar` with type **Integration**.

   Or use this button to do steps 1–2:

   [![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=megathelegend&repository=skylight_calendar&category=integration)
3. Search HACS for **Skylight**, open it and select **Download**. Choose the newest version offered.
4. Restart Home Assistant: **Settings → System → ⋮ (top right) → Restart Home Assistant**.
5. Continue with [Setup](#setup).

HACS tells you when updates are available.

### Option B: Manual installation

You need a folder named `skylight` inside the `custom_components` folder of your Home Assistant configuration folder, so that this file exists:

```text
<configuration folder>/custom_components/skylight/manifest.json
```

**1. Download the integration.** On the [repository page](https://github.com/MegaTheLEGEND/skylight_calendar), select **Code → Download ZIP** (or download a `.zip` from the [Releases page](https://github.com/MegaTheLEGEND/skylight_calendar/releases) when one is published). Then unzip it:

- **Windows:** right-click the file → **Extract All…**
- **macOS:** double-click the file.
- **Linux or ChromeOS:** open it in your file manager, or run `unzip <file>.zip`.

Inside is `custom_components/skylight/`. That `skylight` folder is the one you copy.

**2. Find your configuration folder.** It's the folder that contains `configuration.yaml`. Where it is, and how you reach it, depends on how you run Home Assistant:

| Installation type | Configuration folder | Ways to copy files into it |
|---|---|---|
| **Home Assistant OS** or **Supervised** | `/config` (some add-ons show it as `/homeassistant`) | The **Samba share** add-on; the **Studio Code Server** or **File editor** add-on (upload from the browser); or the **Terminal & SSH** add-on with `scp` |
| **Container (Docker)** | The host folder you mapped to `/config`, for example `-v /opt/homeassistant:/config` | Copy into that host folder |
| **Core (Python virtual environment)** | `~/.homeassistant`, or the folder you pass with `--config` | Copy into it |

If you use the **Samba share** add-on, open the share from your computer:

- **Windows:** type `\\homeassistant.local\config` (or `\\<Home Assistant IP address>\config`) in File Explorer's address bar.
- **macOS:** in Finder, choose **Go → Connect to Server…** and enter `smb://homeassistant.local/config`.
- **Linux:** open `smb://homeassistant.local/config` in your file manager.

If you use **Terminal & SSH**, run this from a terminal on your computer (PowerShell on Windows 10 or 11, Terminal on macOS or Linux), in the unzipped folder:

```bash
scp -r custom_components/skylight root@homeassistant.local:/config/custom_components/
```

**3. Copy the folder.** Create a `custom_components` folder inside the configuration folder if there isn't one, then copy `skylight` into it. A common mistake is copying the whole download, which leaves an extra level such as `custom_components/skylight_calendar-main/custom_components/skylight/`. Home Assistant can't find the integration when it's nested like that.

**4. Restart Home Assistant**, then continue with [Setup](#setup).

To update a manual install later, replace the `skylight` folder with the new one and restart.

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration** and choose **Skylight**, or use this button:

   [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=skylight)
2. Skylight's sign-in page opens. Sign in with your Skylight account and approve access.
3. If your account has more than one frame, pick the one to add. To add another frame later, run **Add Integration → Skylight** again. Frames that are already set up aren't offered.

Sign-in uses OAuth2 (authorization code with PKCE). Home Assistant stores a refresh token and renews access automatically, so you won't need to sign in again unless you revoke access in your Skylight account.

### Polling intervals

**Settings → Devices & Services → Skylight → Configure** sets how often each kind of data is fetched, in seconds (30–7200):

| Option | Default | Covers |
|---|---|---|
| Calendar | 300 | Calendar events and connected calendars |
| Lists | 120 | Shopping and to-do lists |
| Chores, meals and rewards | 300 | Chores, meal plans, star totals, Task Box |
| Frame settings | 600 | Brightness, sleep schedule, other device settings |
| Photos | 900 | Latest frame photo |

Saving reloads the integration. Frame settings and connected-calendar lists are only downloaded when they've changed (`If-None-Match`), so polling those more often costs little. Chores, lists, rewards and photos are always downloaded in full, because Skylight doesn't reliably signal when those change.

---

## What you get

| HA platform | Entities | Notes |
|---|---|---|
| `calendar` | One combined calendar, plus one per Skylight source calendar | Covers 14 days back to 60 days ahead. `calendar.get_events` works. Create events on the combined calendar; edit and delete them on any writable source calendar. |
| `todo` | One per Skylight list (Grocery, To-Do, custom lists), plus one chore list per family member | Add, rename, complete and delete. A chore added to a person's list is assigned to them, and its due date sets the chore's day. |
| `sensor` | See [Sensors](#sensors) | Full details are in each sensor's attributes, for dashboards and automations. |
| `switch` | Sleep mode, night light, show captions, show heart, blur effect, start sound, side by side | Changes the frame directly. |
| `number` | Brightness (0–255), slideshow speed (0–240 s), night light brightness, sleep sound volume | Changes the frame directly. |
| `time` | Sleeps at, wakes at | The frame's sleep schedule. |
| `binary_sensor` | Screen asleep, activated | Read-only. *Screen asleep* is whether the display is off right now; the *sleep mode* switch is whether the schedule is on. |
| `image` | Latest frame photo | |

Entities are only created for settings your frame reports. What Skylight reports varies by hardware, and a missing setting means "not supported on this frame" rather than "off". `sleep_mode`, `nightlight_color`, `sleep_sound` and `slideshow_style` are read-only diagnostic sensors instead of drop-down controls: only one value of each has ever been seen, and a guessed list of options would give controls that fail.

### Sensors

Entity IDs are built from your frame's name and each family member's name. The examples on this page use a frame named **Family** and people named **Alex** and **Sam**. Look up your own IDs in **Developer Tools → States**.

| Entity | State | Attributes |
|---|---|---|
| `sensor.<frame>_chores_today` | Number of chores | `chores[]` (id, summary, description, emoji, status, reward_points, start_date, start_time, completed_on, recurrence_rrule, routine, assignee_id, assignee) and `by_status` |
| `sensor.<person>_chores_today` | Number of chores for that person | Same `chores[]` as above, for that person only |
| `sensor.<frame>_meals_today` | Number of meals | `meals[]` (id, summary, category, recipe_title, recipe_description, note, recurrence_rrule, instances) and `by_slot` |
| `sensor.<frame>_breakfast_today`, `_lunch_today`, `_dinner_today`, `_snack_today` | Meal names separated by commas, or `none` | Full details in `meals[]` |
| `sensor.<person>_stars` | Current star balance | One per family member |
| `sensor.<frame>_task_box` | Number of items | `items[]`: reusable chore templates the frame picks from when adding a chore (summary, emoji, reward_points, routine) |
| `image.<frame>_latest_photo` | Most recent photo | Skylight only returns photos sent by the account you signed in with, not photos other people sent to the same frame. |

### Skylight Buddy

A Buddy paired to the frame gets its own device in Home Assistant, named as in the Skylight app:

| HA platform | Entities | Notes |
|---|---|---|
| `switch` | Night light; one `<label> alarm` per alarm | Turns that alarm on or off. Attributes: `time`, `rrule`, `fires_on`, `sound`, `volume`, `snoozable`. |
| `number` | Night light brightness, sleep sound volume; `<label> alarm volume` per alarm | |
| `time` | `<label> alarm time` per alarm | Sent as `HH:MM`. |

Alarms created in the Skylight app appear after the next frame-settings poll. A Buddy's `wakes_at` and `sleeps_at` are separate from its alarms and read `null`.

---

## Dashboard examples

Every sensor keeps the full record for each item in its attributes, so a **Markdown card** with a template can show whatever detail you want. Replace `family`, `alex` and `sam` with the names in your own entity IDs.

### Today's chores (whole family)

```yaml
type: markdown
title: Today's chores
content: |
  {% for c in state_attr('sensor.family_chores_today', 'chores') or [] %}
  ### {{ c.emoji or '📋' }} {{ c.summary }}
  {% if c.description %}> {{ c.description }}{% endif %}
  - **Assigned to:** {{ c.assignee or 'Anyone' }}
  - **Reward:** ⭐ {{ c.reward_points or 0 }}
  - **Status:** {{ c.status }}{% if c.start_time %} · Due at {{ c.start_time }}{% endif %}
  {% if c.recurrence_rrule %}- **Repeats:** `{{ c.recurrence_rrule }}`{% endif %}

  {% endfor %}
```

### Today's chores (one person)

```yaml
type: markdown
title: Alex's chores today
content: |
  {% set chores = state_attr('sensor.alex_chores_today', 'chores') or [] %}
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

### Today's meals

```yaml
type: markdown
title: Today's meals
content: |
  {% for m in state_attr('sensor.family_meals_today', 'meals') or [] %}
  ### {{ m.category }}: {{ m.summary }}
  {% if m.recipe_title and m.recipe_title != m.summary %}*Recipe:* **{{ m.recipe_title }}**{% endif %}
  {% if m.recipe_description %}{{ m.recipe_description }}{% endif %}
  {% if m.note %}> Note: {{ m.note }}{% endif %}

  {% endfor %}
```

The per-slot sensors have the meal name as their state, so ordinary entity cards work:

```yaml
type: glance
title: Today's menu
entities:
  - entity: sensor.family_breakfast_today
    name: Breakfast
  - entity: sensor.family_lunch_today
    name: Lunch
  - entity: sensor.family_dinner_today
    name: Dinner
```

### Task Box

```yaml
type: markdown
title: Task Box
content: |
  {% for t in state_attr('sensor.family_task_box', 'items') or [] %}
  - {{ t.emoji or '☐' }} **{{ t.summary }}**{% if t.reward_points %} · ⭐ {{ t.reward_points }}{% endif %}{% if t.routine %} · _routine_{% endif %}
  {% endfor %}
```

### Lists, stars and calendar

The built-in cards work as they are:

```yaml
type: todo-list
entity: todo.grocery_list
```

```yaml
type: glance
title: Family stars
entities:
  - sensor.alex_stars
  - sensor.sam_stars
```

```yaml
type: calendar
entities:
  - calendar.family_calendar
```

Skylight list items only have a name: no description, due date or reward. Use a chore if you need those.

---

## Automation examples

**Read out today's chores at breakfast** (use your own text-to-speech and speaker entities):

```yaml
alias: Morning chores announcement
triggers:
  - trigger: time
    at: "07:30:00"
actions:
  - action: tts.speak
    target:
      entity_id: tts.google_translate_en_com
    data:
      media_player_entity_id: media_player.kitchen_display
      message: >
        Good morning. Today's chores:
        {% for c in state_attr('sensor.family_chores_today', 'chores') or [] %}
        {{ c.assignee or 'Someone' }}: {{ c.summary }}.
        {% endfor %}
```

**Celebrate a star milestone:**

```yaml
alias: Star milestone
triggers:
  - trigger: numeric_state
    entity_id: sensor.sam_stars
    above: 100
actions:
  - action: notify.notify
    data:
      message: "🎉 Sam just passed 100 stars!"
```

**Dinner reminder with the dish name:**

```yaml
alias: Dinner reminder
triggers:
  - trigger: time
    at: "17:00:00"
conditions:
  - condition: template
    value_template: "{{ states('sensor.family_dinner_today') != 'none' }}"
actions:
  - action: notify.notify
    data:
      message: "Dinner in one hour: {{ states('sensor.family_dinner_today') }}"
```

**Dim the frame at night:**

```yaml
alias: Skylight night dim
triggers:
  - trigger: time
    at: "21:00:00"
actions:
  - action: number.set_value
    target:
      entity_id: number.family_brightness
    data:
      value: 40
```

---

## Actions

All actions are in the `skylight` domain, and you can try them in **Developer Tools → Actions**. Each takes an optional `frame_id`, which you only need when more than one frame is set up. After a successful call, the affected data refreshes straight away instead of waiting for the next poll.

| Action | What it does |
|---|---|
| `skylight.upload_media` | Send a photo or short video to the frame. See [Uploading photos and videos](#uploading-photos-and-videos). |
| `skylight.create_chore` | Add a chore for one or more family members. Skylight creates one chore per person. |
| `skylight.update_chore` | Change the reward and/or emoji of an existing chore. |
| `skylight.complete_chore` | Complete a chore and credit a chosen family member, including up-for-grabs chores. |
| `skylight.delete_chore` | Permanently delete a chore, including every occurrence of a repeating one. |
| `skylight.create_task` | Add an unscheduled item to the Task Box. |
| `skylight.create_list` | Create a shopping or to-do list. |
| `skylight.delete_list` | Permanently delete a list and its items. |
| `skylight.create_reward` | Add a reward that can be bought with stars. Skylight stores one reward per family member, so this creates one per ID given. |
| `skylight.redeem_reward` | Spend a family member's stars on a reward. |
| `skylight.create_recipe` | Add a recipe to the meal planner. |
| `skylight.plan_meal` | Put a recipe in a meal slot on a given day. |
| `skylight.add_recipe_to_grocery_list` | Add a recipe's ingredients to the default grocery list. |

**Who can run them.** When a signed-in Home Assistant user runs an action (from Developer Tools, a dashboard button and so on), that user needs control of the frame's calendar entity, and `upload_media` also needs an administrator. Calls with no user attached, such as most automations, are allowed.

**Finding IDs.** Family member `category_id`s, meal slot `meal_category_id`s and `recipe_id`s are in the sensor attributes above. For example, `sensor.<frame>_chores_today` lists each chore's `assignee_id`, and `sensor.<frame>_meals_today` shows each meal's recipe and slot. A list's `list_id` is the last part of the matching todo entity's unique ID.

```yaml
# Plan Tuesday's dinner and send the ingredients to the grocery list
- action: skylight.plan_meal
  data:
    date: "2026-09-01"
    meal_category_id: "9115872"
    recipe_id: "1234567"
- action: skylight.add_recipe_to_grocery_list
  data:
    recipe_id: "1234567"
```

---

## Working with chores

### Chore IDs

Each chore in `sensor.<frame>_chores_today` has an `id` in one of three forms:

| Form | Example | Used for |
|---|---|---|
| Series ID | `12345` | One-off chores, and chores that repeat after completion |
| Series ID + date | `12345-2026-11-04` | One day of a repeating all-day chore |
| Series ID + date + time | `12345-2026-11-04-1400` | One day of a repeating chore scheduled at a time of day (here 14:00) |

Pass IDs exactly as listed. Don't add a date to a bare ID, and keep the date and time on an occurrence ID even when you complete it late.

### Creating chores

```yaml
# Saturday bin duty: one chore each for two people
action: skylight.create_chore
data:
  summary: Take out the bins
  start: "2026-09-05"
  start_time: "17:30:00"
  assignees: ["Alex", "Sam"]   # or category_ids: ["23469502"]
  description: Green bin this week
```

Names are matched against the frame's family members, ignoring case. A frame's category list also includes calendars such as `US Holidays`; those are rejected rather than silently used. An unknown name gets an error that lists the valid names.

You can also add a chore to a family member's to-do list in Home Assistant. It's assigned to them, and its due date becomes the chore's day.

### Chores that repeat after completion

`skylight.create_chore` accepts `renewal_interval` and `renewal_unit` (`day`, `week` or `month`) together. Skylight schedules the next one from the day it was actually completed, so a 30-day chore finished late on 4 November is next due on 4 December.

```yaml
action: skylight.create_chore
data:
  summary: Replace pre-filter
  up_for_grabs: true
  renewal_interval: 30
  renewal_unit: day
```

`start` optionally sets the first due date; the default is today. Don't combine renewal with `recurrence_set` or `recurring_until`.

### Changing a chore's reward or emoji

`skylight.update_chore` changes `reward_points`, `emoji`, or both. Fields you leave out stay the same, and `reward_points: 0` removes the reward. It doesn't complete the chore or award stars.

```yaml
action: skylight.update_chore
data:
  chore_id: "12345"
  emoji: 🧹
  reward_points: 1
```

Use the series ID: for `12345-2026-11-04`, pass `12345`. The change applies to every occurrence. Creating and editing are separate calls, so if an edit fails, retry only the edit, not the create.

### Completing a chore for someone

Ticking a chore on a family member's to-do list completes it for them. `skylight.complete_chore` also handles up-for-grabs chores, which aren't on anyone's list, by naming who gets the stars:

```yaml
action: skylight.complete_chore
data:
  chore_id: "12345-2026-11-04"
  assignee: Alex          # or category_id: "67890"
```

- Give exactly one of `assignee` (case doesn't matter) or `category_id`. Names that match more than one person, and calendar categories, are rejected.
- `completed_on` is the day it was done. The default is today in Home Assistant's time zone.
- The action checks the chore first: it skips one that's already complete and refuses one assigned to someone else.
- Failures are reported back to the automation and not retried. Don't run two completions of the same chore at once: someone could change it in the Skylight app between the check and the update, and Skylight offers no way to make the call safe to repeat.
- The name only chooses who gets credit; it doesn't identify who pressed the button. If you run this from a phone notification or dashboard button, map the Home Assistant user to a family member yourself instead of trusting a name from the button.

### Deleting chores

Remove an assigned chore from its family member's to-do list, or call `skylight.delete_chore` with its ID. That action is the only way to remove an up-for-grabs chore. An occurrence ID deletes the whole series.

---

## Uploading photos and videos

`skylight.upload_media` sends an image or short video to the frame the same way the Skylight app does.

**Formats:** jpg, jpeg, png, gif, heic, heif, webp, mp4, mov, m4v.

**Where the file must be.** `file_path` is a path on the machine running Home Assistant, not on your computer or phone. Home Assistant only lets integrations read files in allowed folders. The `www` folder in your configuration folder (`/config/www`) is allowed by default. To use other folders, list them under `allowlist_external_dirs` in `configuration.yaml`, and include `/config/www` too, because setting the list replaces the default:

```yaml
homeassistant:
  allowlist_external_dirs:
    - /config/www
    - /media
```

On Home Assistant OS, Supervised and Container, write paths the way Home Assistant sees them (`/config/...`), even if the files live somewhere else on the host. On Core, use the real path, such as `/home/you/.homeassistant/www/photo.jpg`.

```yaml
action: skylight.upload_media
data:
  file_path: /config/www/family_photo.jpg
  caption: Sunday brunch    # optional
```

**Send a doorbell snapshot to the frame:**

```yaml
alias: Doorbell to Skylight
triggers:
  - trigger: state
    entity_id: binary_sensor.front_door
    to: "on"
actions:
  - action: camera.snapshot
    target:
      entity_id: camera.front_door
    data:
      filename: /config/www/skylight_doorbell.jpg
  - action: skylight.upload_media
    data:
      file_path: /config/www/skylight_doorbell.jpg
      caption: Someone at the door!
```

Captions only appear on frames with Skylight Plus. Free accounts accept the caption, but the frame doesn't show it.

After an upload, `image.<frame>_latest_photo` refreshes within a few seconds instead of waiting for the next photo poll.

---

## Creating calendar events

Use Home Assistant's `calendar.create_event` action on the combined `calendar.<frame>_calendar` entity. Skylight puts the event on whichever source calendar is set as the default for new events. You can edit and delete events on any source calendar Skylight reports as writable; read-only subscriptions (holiday feeds, imported `webcal` calendars) don't offer those options.

```yaml
action: calendar.create_event
target:
  entity_id: calendar.family_calendar
data:
  summary: Dentist
  start_date_time: "2026-09-01 14:00:00"
  end_date_time: "2026-09-01 15:00:00"
  location: Dr Smith's office
```

---

## Troubleshooting

| Problem | What to check |
|---|---|
| **Skylight** isn't listed in **Add Integration** | Restart Home Assistant after installing, then refresh the browser page. For a manual install, check that `custom_components/skylight/manifest.json` is directly inside the configuration folder, with no extra folder level (see [Manual installation](#option-b-manual-installation)). |
| Sign-in fails or keeps returning to the sign-in page | Try another browser or a private window, or clear cookies for `ourskylight.com`. Check that the same account can sign in to the Skylight app. |
| Calendar shows no events | Events must fall between 14 days ago and 60 days ahead. |
| A new list doesn't appear | Wait for the next lists poll (2 minutes by default) or reload the integration. |
| No star sensor or to-do list for someone | They need a full family member profile on the frame, not only a calendar. |
| Task Box sensor shows 0 | It only counts reusable chore templates. If you don't use the Task Box, that's expected. |
| `... is not under an allowlist_external_dirs entry` | The photo is outside an allowed folder. Move it to `/config/www` or allow its folder as shown in [Uploading photos and videos](#uploading-photos-and-videos). |
| Ticking a timed repeating chore fails with `422` | Update to the latest version ([#18](https://github.com/MegaTheLEGEND/skylight_calendar/issues/18)). |

For more detail, turn on debug logging, reproduce the problem, then look in **Settings → System → Logs**. Either select **Enable debug logging** on the Skylight integration's page, or add this to `configuration.yaml` and restart:

```yaml
logger:
  default: info
  logs:
    custom_components.skylight: debug
```

When you [open an issue](https://github.com/MegaTheLEGEND/skylight_calendar/issues), include the relevant log lines with tokens and personal details removed.

---

## Upgrading

### To v3.0.0 (domain renamed)

The integration's domain changed from `skylight_calendar` to `skylight`. Every entity ID, action name and config entry has to be set up again:

1. **Settings → Devices & Services → Skylight → ⋮ → Delete**.
2. In HACS, open **Skylight → ⋮ → Redownload** and choose the newest version. For a manual install, copy in the new `skylight` folder and delete the old `custom_components/skylight_calendar` folder.
3. Restart Home Assistant.
4. **Add Integration → Skylight** and sign in again.
5. Update automations and dashboards that use `skylight_calendar.*` entity IDs or the `skylight_calendar.upload_media` action. The action is now `skylight.upload_media`; check **Developer Tools → States** for the new entity IDs.

### From releases before v2

v2 switched to signing in through the browser (OAuth2), with no manual token copying. Remove the old integration and add it again.

### Changes in v2.x

- **v2.1.0:** per-source calendars, per-person chore sensors, per-slot meal sensors, `image` / `switch` / `number` platforms for frame controls, reconfigure flow; OAuth2 rewritten as authorization code with PKCE; brightness, slideshow speed and sleep mode now sent to the device endpoint.
- **v2.0.0:** rewritten around Skylight's OAuth2 API (`Skylight-Api-Version: 2026-05-01`); new `todo` and `sensor` platforms; automatic refresh-token rotation; several corrections to how Skylight's data is read.

---

## Development and tests

The tests run offline. They don't need Home Assistant, a Skylight account or a network connection. They load the integration's real code with Home Assistant and Skylight replaced by test doubles, and one file runs the real API client against a local fake server. Use Python 3.12 or newer.

In the repository folder, create a virtual environment, install the test dependencies and run the tests.

**Windows (PowerShell):**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r tests\requirements.txt
python -m unittest discover -s tests -v
```

If PowerShell refuses to run the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use `.venv\Scripts\activate.bat` from Command Prompt instead.

**macOS and Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -v
```

The tests check the code against Skylight's behavior as recorded from the Skylight apps. They don't replace trying a change on a real frame.

---

## Credits

[MegaTheLEGEND/skylight_calendar](https://github.com/MegaTheLEGEND/skylight_calendar). OAuth2 rewrite and platform expansion by [@devinslick](https://github.com/devinslick) (thank you so much!). Chore features by [@Shafty199](https://github.com/Shafty199).

## License

MIT. See [LICENSE](LICENSE).
