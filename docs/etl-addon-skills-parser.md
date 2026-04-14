# ETL Pipeline: Add-on Skills Wiki Parser

> **Scope:** Parse `data/storage/wiki_raw/NGS/class/Portal_New_Genesis_Add-on_Skills.md`
> into structured, queryable JSON documents for MongoDB.
>
> **Input:** Raw Markdown (pipe-delimited "folded tables" scraped from Arks Visiphone wiki).
> **Output:** One JSON document per class, with typed numeric values for `$gte`/`$lte` queries.
> **Principle:** Zero LLM involvement — deterministic line-by-line parsing only.

---

## 1. Raw Data Structure Analysis

### 1.1 File Layout

The file has 3 distinct zones:

| Zone | Lines | Content | Action |
|------|-------|---------|--------|
| Header | 1-38 | TOC, Overview prose, Cost table | **SKIP** — no skill data |
| Skill Data | 39-328 | Class headings + folded tables | **PARSE** — this is the target |
| Footer/Navbox | 329-331 | Giant wiki navigation links | **STOP** — break parsing loop |

### 1.2 Repeating Pattern Per Class

Each class section follows this **exact** structure:

```text
### ClassName[[edit...]]                          ← H3 heading = new class

| Main Effects | Potency | | | ... |              ← Row 0: effect_type header ("Main Effects"/"Sub Effects") + category ("Potency"/"Stat")
| --- | --- | --- | ... |                         ← Row 1: separator (SKIP)
| Effect Name Here |                               ← Row 2: skill name (single cell, rest empty)
| 1 | 2 | 3 | ... | 10 |                          ← Row 3: level header (fold 1)
| +0.25% | +0.50% | +0.75% | ... | +2.50% |       ← Row 4: values (fold 1)
| 11 | 12 | 13 | ... | 20 |                        ← Row 5: level header (fold 2)
| +2.75% | +3.00% | +3.25% | ... | +5.00% |       ← Row 6: values (fold 2)
                                                    ← blank line = end of table
```

**After the main effect, classes have 2 sub-effect tables** using the exact same structure
but with `| Sub Effects |` in Row 0.

### 1.3 The "Folded Table" Problem

The wiki uses **horizontal folding** to fit 20 levels into a 10-column display:

```
FOLD 1: levels  1-10  → values for 1-10
FOLD 2: levels 11-20  → values for 11-20
```

Both folds belong to the **same** skill. A naive parser would see these as two separate
tables or fail to merge them.

### 1.4 Value Format Variants

All values follow the pattern `+NUMBER` with an optional `%` suffix:

| Format | Example | Unit | Cast |
|--------|---------|------|------|
| `+0.25%` | Melee Weapon Potency Up | `%` | `float` → `0.25` |
| `+0.5%` | Critical Hit Potency Up (note: no trailing zero) | `%` | `float` → `0.5` |
| `+30%` | Burn Resistance Up | `%` | `float` → `30.0` |
| `+15.5%` | HP Recovery upon Joining Trial | `%` | `float` → `15.5` |
| `+1` | PP Up (Waker) — **no percent** | `flat` | `int` → `1` |
| `+10` | HP Up (All Classes) — **no percent** | `flat` | `int` → `10` |

**Edge case:** The category column (Row 0, cell 2) tells you the type:
- `Potency` → expect `%` values
- `Stat` → expect flat integer values (no `%`)

### 1.5 Special Sections

| Section | `### Heading` | Trait |
|---------|---------------|-------|
| `All Classes` | Only has Sub Effects, **no Main Effect** |
| `Select Classes` | Only has Sub Effects (2 skills), **no Main Effect** |

---

## 2. Line Pattern Identification (Regex)

The parser needs to identify 5 line types:

```python
import re

# Pattern 1: Class heading — captures class name
# Example: "### Hunter[[edit..."
RE_CLASS_HEADING = re.compile(r'^###\s+([A-Za-z ]+?)(?:\[|$)')

# Pattern 2: Effect type row — captures "Main Effects" or "Sub Effects" + category
# Example: "| Main Effects | Potency | | | ..."
RE_EFFECT_TYPE = re.compile(r'^\|\s*(Main Effects?|Sub Effects?)\s*\|\s*(\w+)')

# Pattern 3: Separator row — all dashes
# Example: "| --- | --- | --- |"
RE_SEPARATOR = re.compile(r'^\|[\s\-|]+$')

# Pattern 4: Level header row — cells contain only integers 1-20
# Example: "| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |"
RE_LEVEL_ROW = re.compile(r'^\|[\s\d|]+$')

# Pattern 5: Value row — cells contain +N or +N.N or +N% or +N.N%
# Example: "| +0.25% | +0.50% | +0.75% | ..."
RE_VALUE_ROW = re.compile(r'^\|\s*\+[\d.]+%?\s*\|')

# Pattern 6: Skill name row — single cell with text (rest empty)
# Example: "| Melee Weapon Potency Up |"
# Detected by: has exactly 1 non-empty cell after split, does NOT match other patterns

# Pattern 7: Footer sentinel — massive pipe row (>50 columns)
RE_FOOTER = re.compile(r'^(\|\s*){20,}$')
```

### Helper: Parse a pipe-delimited row into cells

```python
def parse_row(line: str) -> list[str]:
    """Split a markdown table row into stripped, non-empty cells."""
    cells = [c.strip() for c in line.split('|')]
    # Remove leading/trailing empty strings from split
    return [c for c in cells if c]
```

### Helper: Clean and cast a value

```python
def parse_value(raw: str) -> tuple[float | int, str]:
    """
    Parse '+0.25%' → (0.25, '%')
    Parse '+10'    → (10, 'flat')
    """
    cleaned = raw.strip().lstrip('+')
    if cleaned.endswith('%'):
        numeric_str = cleaned.rstrip('%')
        return float(numeric_str), '%'
    else:
        # Try int first, fall back to float
        val = float(cleaned)
        return int(val) if val == int(val) else val, 'flat'
```

---

## 3. State Machine Design

The parser uses a finite-state machine with these state variables:

```python
@dataclass
class ParserState:
    current_class: str = ""         # e.g., "Hunter"
    effect_type: str = ""           # "main" or "sub"
    category: str = ""              # "Potency" or "Stat"
    skill_name: str = ""            # e.g., "Melee Weapon Potency Up"
    pending_levels: list[int] = field(default_factory=list)   # [1,2,...10] from current fold
    levels_data: list[dict] = field(default_factory=list)     # accumulated {level, value, unit}
```

### State Transitions

```
                     ┌────────────────────────────────────────┐
                     │                                        │
  ┌──────────┐   H3 heading    ┌─────────────┐               │
  │  START    │───────────────→ │  IN_CLASS    │               │
  └──────────┘                  └─────────────┘               │
                                     │                        │
                            "Main/Sub Effects" row            │
                                     │                        │
                                     ▼                        │
                               ┌───────────┐                  │
                               │ IN_TABLE   │                  │
                               └───────────┘                  │
                                     │                        │
                               separator row → SKIP           │
                                     │                        │
                               skill name row → save name     │
                                     │                        │
                               level row → save pending       │
                                     │                        │
                               value row → zip & merge        │
                                     │                        │
                          ┌──────────┼──────────┐             │
                          │          │          │             │
                     level row    blank      H3/Effect      │
                     (fold 2)     line       type row        │
                          │          │          │             │
                     save more    FLUSH      FLUSH           │
                          │       current    current         │
                          ▼       skill      skill           │
                     value row      │          │             │
                     zip & merge    ▼          │             │
                          │      IN_CLASS     ─┘             │
                          │                                  │
                          └──────────────────────────────────┘
```

### Flush Logic

When a table boundary is reached (blank line, new effect type, new class, or footer),
flush the current skill into the class document:

```python
def flush_skill(state: ParserState, class_doc: dict):
    """Emit current skill into the class document."""
    if not state.skill_name or not state.levels_data:
        return

    effect = {
        "name": state.skill_name,
        "effect_type": state.category,  # "Potency" or "Stat"
        "levels": sorted(state.levels_data, key=lambda x: x["level"]),
    }

    if state.effect_type == "main":
        class_doc["main_effect"] = effect
    else:
        class_doc.setdefault("sub_effects", []).append(effect)

    # Reset for next skill
    state.skill_name = ""
    state.levels_data = []
    state.pending_levels = []
```

---

## 4. Core Algorithm (Pseudocode)

```python
def parse_addon_skills(filepath: str) -> list[dict]:
    results = []
    state = ParserState()
    current_doc = None

    for line in open(filepath):
        line = line.rstrip('\n')

        # --- STOP: Footer sentinel ---
        if RE_FOOTER.match(line):
            break

        # --- Class heading ---
        m = RE_CLASS_HEADING.match(line)
        if m:
            # Flush previous class
            if current_doc and state.skill_name:
                flush_skill(state, current_doc)
            if current_doc:
                results.append(current_doc)

            class_name = m.group(1).strip()
            current_doc = {
                "class_name": class_name,
                "game_mode": "NGS",
                "source": "Add-on Skills",
                "main_effect": None,
                "sub_effects": [],
            }
            state = ParserState(current_class=class_name)
            continue

        # --- Skip non-table content ---
        if not line.startswith('|'):
            # Blank line or prose → flush if pending
            if state.skill_name and state.levels_data:
                flush_skill(state, current_doc)
            continue

        # --- Separator row ---
        if RE_SEPARATOR.match(line):
            continue

        # --- Effect type header ---
        m = RE_EFFECT_TYPE.match(line)
        if m:
            # Flush previous skill if any
            if state.skill_name and state.levels_data:
                flush_skill(state, current_doc)

            raw_type = m.group(1).strip()
            state.effect_type = "main" if "Main" in raw_type else "sub"
            state.category = m.group(2).strip()  # "Potency" or "Stat"
            state.skill_name = ""
            state.levels_data = []
            state.pending_levels = []
            continue

        # --- Parse cells ---
        cells = parse_row(line)
        if not cells:
            continue

        # --- Level row (all cells are integers) ---
        if all(c.isdigit() for c in cells):
            state.pending_levels = [int(c) for c in cells]
            continue

        # --- Value row (all cells match +N% or +N) ---
        if all(re.match(r'^\+[\d.]+%?$', c) for c in cells):
            if state.pending_levels and len(cells) == len(state.pending_levels):
                for level, raw_val in zip(state.pending_levels, cells):
                    value, unit = parse_value(raw_val)
                    state.levels_data.append({
                        "level": level,
                        "value": value,
                        "unit": unit,
                    })
                state.pending_levels = []  # consumed
            continue

        # --- Skill name row (single meaningful cell) ---
        if len(cells) == 1 or (len(cells) >= 1 and all(c == '' for c in cells[1:])):
            state.skill_name = cells[0].strip()
            state.levels_data = []  # reset for new skill
            continue

    # Flush final class/skill
    if current_doc:
        if state.skill_name and state.levels_data:
            flush_skill(state, current_doc)
        results.append(current_doc)

    return results
```

---

## 5. Target MongoDB Schema

### 5.1 Document Structure (per class)

```json
{
  "_id": "addon_skill:ngs:hunter",
  "class_name": "Hunter",
  "game_mode": "NGS",
  "source": "Add-on Skills",
  "source_url": "https://pso2na.arks-visiphone.com/wiki/Portal:New_Genesis/Add-on_Skills",
  "last_updated": "2026-04-03T04:00:00Z",
  "main_effect": {
    "name": "Melee Weapon Potency Up",
    "effect_type": "Potency",
    "levels": [
      {"level": 1,  "value": 0.25, "unit": "%"},
      {"level": 2,  "value": 0.50, "unit": "%"},
      {"level": 3,  "value": 0.75, "unit": "%"},
      {"level": 4,  "value": 1.00, "unit": "%"},
      {"level": 5,  "value": 1.25, "unit": "%"},
      {"level": 6,  "value": 1.50, "unit": "%"},
      {"level": 7,  "value": 1.75, "unit": "%"},
      {"level": 8,  "value": 2.00, "unit": "%"},
      {"level": 9,  "value": 2.25, "unit": "%"},
      {"level": 10, "value": 2.50, "unit": "%"},
      {"level": 11, "value": 2.75, "unit": "%"},
      {"level": 12, "value": 3.00, "unit": "%"},
      {"level": 13, "value": 3.25, "unit": "%"},
      {"level": 14, "value": 3.50, "unit": "%"},
      {"level": 15, "value": 3.75, "unit": "%"},
      {"level": 16, "value": 4.00, "unit": "%"},
      {"level": 17, "value": 4.25, "unit": "%"},
      {"level": 18, "value": 4.50, "unit": "%"},
      {"level": 19, "value": 4.75, "unit": "%"},
      {"level": 20, "value": 5.00, "unit": "%"}
    ]
  },
  "sub_effects": [
    {
      "name": "PA Charge Movement Speed Up",
      "effect_type": "Potency",
      "levels": [
        {"level": 1,  "value": 10.0, "unit": "%"},
        {"level": 20, "value": 30.0, "unit": "%"}
      ]
    },
    {
      "name": "Burn Resistance Up",
      "effect_type": "Potency",
      "levels": [
        {"level": 1,  "value": 30.0, "unit": "%"},
        {"level": 20, "value": 50.0, "unit": "%"}
      ]
    }
  ]
}
```

### 5.2 Waker Special Case (Flat Integer Values)

```json
{
  "_id": "addon_skill:ngs:waker",
  "class_name": "Waker",
  "game_mode": "NGS",
  "source": "Add-on Skills",
  "main_effect": {
    "name": "PP Up",
    "effect_type": "Stat",
    "levels": [
      {"level": 1,  "value": 1,  "unit": "flat"},
      {"level": 20, "value": 20, "unit": "flat"}
    ]
  },
  "sub_effects": [...]
}
```

### 5.3 "All Classes" Special Case (No Main Effect)

```json
{
  "_id": "addon_skill:ngs:all_classes",
  "class_name": "All Classes",
  "game_mode": "NGS",
  "source": "Add-on Skills",
  "main_effect": null,
  "sub_effects": [
    {
      "name": "HP Up",
      "effect_type": "Stat",
      "levels": [
        {"level": 1,  "value": 1,  "unit": "flat"},
        {"level": 20, "value": 20, "unit": "flat"}
      ]
    }
  ]
}
```

### 5.4 MongoDB Indexes

```javascript
// For filtering by class
db.addon_skills.createIndex({ "class_name": 1, "game_mode": 1 }, { unique: true })

// For numeric queries across all classes (e.g., "which classes have potency > 4% at level 20?")
db.addon_skills.createIndex({ "main_effect.levels.value": 1 })
db.addon_skills.createIndex({ "sub_effects.levels.value": 1 })
```

### 5.5 Example MongoDB Queries

```javascript
// "What is Hunter's main add-on skill at level 20?"
db.addon_skills.findOne(
  { class_name: "Hunter", game_mode: "NGS" },
  { "main_effect.name": 1, "main_effect.levels": { $elemMatch: { level: 20 } } }
)

// "Which classes have a main effect value >= 5% at level 20?"
db.addon_skills.find({
  "main_effect.levels": { $elemMatch: { level: 20, value: { $gte: 5.0 } } }
}, { class_name: 1, "main_effect.name": 1 })

// "List all sub-effects of type 'Resistance'"
db.addon_skills.find(
  { "sub_effects.name": { $regex: /Resistance/i } },
  { class_name: 1, "sub_effects.$": 1 }
)
```

---

## 6. Edge Cases and Risks

### 6.1 Identified Edge Cases

| # | Edge Case | Example | Risk if Missed | Mitigation |
|---|-----------|---------|----------------|------------|
| 1 | **Inconsistent decimal format** | `+0.5%` vs `+0.50%` | Float comparison fails | `parse_value()` strips `+` and `%`, then `float()` normalizes both to `0.5` |
| 2 | **Flat vs percent values** | `+1` (PP Up) vs `+1%` | Wrong unit stored, queries break | Check for `%` suffix; derive from Row 0 category column (`Stat` vs `Potency`) |
| 3 | **"All Classes" has no Main Effect** | Heading `### All Classes` only has Sub Effects tables | `main_effect` key would be missing or `{}` | Initialize `main_effect: null`, only set if `Main Effects` row found |
| 4 | **"Select Classes" has no Main Effect** | Same as above | Same risk | Same mitigation |
| 5 | **Half-percent values** | `+15.5%` in HP Recovery upon Joining Trial | Int cast loses `.5` | Always use `float()` for `%` values |
| 6 | **Footer navbox** | Line 329-331 — giant pipe-delimited mess | Parser crashes or produces garbage chunks | Detect by column count (>20 pipes in a single row) → `break` |
| 7 | **Wiki edit links in headings** | `### Hunter[[edit(...` | Class name includes `[[edit...` junk | Regex captures only `[A-Za-z ]+` before `[[` |
| 8 | **Mismatched fold lengths** | A future wiki edit adds level 21+ | `zip()` silently drops unmatched pairs | Log a warning if `len(levels) != len(values)` |
| 9 | **Duplicate class names** | Wiki error creating two Hunter sections | Second document overwrites first | Use `_id = addon_skill:ngs:{class_name_lower}` as upsert key |
| 10 | **Empty cells in skill name row** | `\| Melee Weapon Potency Up \|` + trailing empty cells | `parse_row()` returns `["Melee Weapon Potency Up", ""]` | Filter empty strings after split |

### 6.2 Data Validation Assertions

After parsing, run these checks before MongoDB insertion:

```python
def validate_class_doc(doc: dict) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors = []
    name = doc.get("class_name", "UNKNOWN")

    # All standard classes must have a main_effect
    NO_MAIN_EFFECT_CLASSES = {"All Classes", "Select Classes"}
    if name not in NO_MAIN_EFFECT_CLASSES and doc["main_effect"] is None:
        errors.append(f"{name}: missing main_effect")

    # All classes must have at least 1 sub-effect
    if not doc.get("sub_effects"):
        errors.append(f"{name}: no sub_effects found")

    # Each effect must have exactly 20 levels (1-20)
    def check_levels(effect: dict, label: str):
        levels = effect.get("levels", [])
        level_nums = {l["level"] for l in levels}
        if level_nums != set(range(1, 21)):
            errors.append(f"{name} > {label}: expected levels 1-20, got {sorted(level_nums)}")
        for l in levels:
            if not isinstance(l["value"], (int, float)):
                errors.append(f"{name} > {label} lv{l['level']}: value '{l['value']}' is not numeric")
            if l["unit"] not in ("%", "flat"):
                errors.append(f"{name} > {label} lv{l['level']}: unexpected unit '{l['unit']}'")

    if doc["main_effect"]:
        check_levels(doc["main_effect"], f"main:{doc['main_effect']['name']}")
    for i, sub in enumerate(doc.get("sub_effects", [])):
        check_levels(sub, f"sub[{i}]:{sub['name']}")

    return errors
```

---

## 7. Architecture: Where This Parser Fits

```text
data/
├── scrapers/
│   └── wiki_scraper.py              ← existing: fetches HTML from wiki API
├── storage/
│   └── wiki_raw/
│       └── NGS/
│           └── class/
│               └── Portal_New_Genesis_Add-on_Skills.md    ← INPUT
├── etl/
│   └── parse_addon_skills.py        ← NEW: this parser
└── models/
    └── addon_skill.py               ← NEW: Pydantic model (optional)
```

### Flow

```text
wiki_raw/...Add-on_Skills.md                       (1) Source file
         │
         ▼
parse_addon_skills.py                               (2) Parse → list[dict]
         │
         ├──→ validate_class_doc()                  (3) Pre-insert validation
         │
         └──→ MongoDB upsert (structured queries)   (4) For LLM tool calls
```

The parser produces **MongoDB documents** for programmatic tool-call queries.
The LLM agent calls MongoDB directly via tool functions
(e.g., `"What is Hunter's potency at level 15?"` → `$elemMatch` query).

---

## 8. Applicability to Other Wiki Pages

This parser is **specific** to the Add-on Skills page format. Other wiki pages
use different table structures:

| Page type | Table format | Parser needed |
|-----------|-------------|---------------|
| **Add-on Skills** (this doc) | Folded 2x10 level tables | `parse_addon_skills.py` |
| **Class Skills** (Slayer, Ranger...) | Skill-per-table with rowspan/colspan | Already handled by `wiki_scraper.py` → `.chunks.json` |
| **Weapon lists** | Simple data table (name, stats) | Standard row parser |
| **Tech Arts Customization** | Similar folded tables (very large) | Adapted version of this parser |

The folded-table handling logic (`zip` levels+values, merge folds) can be
extracted into a shared utility for reuse on Tech Arts Customization.

---

## 9. Implementation Checklist

- [ ] Create `data/etl/parse_addon_skills.py` with the state machine parser
- [ ] Implement `parse_value()` with regex-based cleaning and type casting
- [ ] Implement `parse_row()` to split pipe-delimited rows
- [ ] Implement `flush_skill()` to emit completed skills into class documents
- [ ] Add footer sentinel detection (>20 pipe columns → break)
- [ ] Handle "All Classes" and "Select Classes" (no main effect)
- [ ] Add `validate_class_doc()` post-parse validation
- [ ] Add CLI interface: `uv run data/etl/parse_addon_skills.py --dry-run`
- [ ] Add MongoDB upsert logic using `_id = addon_skill:ngs:{class_name_lower}`
- [ ] Verify MongoDB upsert writes correct documents to `addon_skills` collection
- [ ] Test: verify 12 classes parsed (10 standard + All Classes + Select Classes)
- [ ] Test: verify all classes have exactly 20 levels per effect
- [ ] Test: verify Waker main_effect values are `int` type (flat), not `float`
- [ ] Test: verify "All Classes" has `main_effect: null`
- [ ] Test: verify `+0.5%` and `+0.50%` both parse to `0.5`

---

## 10. Expected Output Summary

After successful parsing, expect:

| Class | Main Effect | Sub 1 | Sub 2 |
|-------|-------------|-------|-------|
| Hunter | Melee Weapon Potency Up (%) | PA Charge Movement Speed Up (%) | Burn Resistance Up (%) |
| Fighter | Critical Hit Potency Up (%) | Jump Power Up (%) | Freeze Resistance Up (%) |
| Ranger | Ranged Weapon Potency Up (%) | Photon Blast Potency Up (%) | Shock Resistance Up (%) |
| Gunner | Offensive PB Gauge Accumulation Up (%) | Offensive PP Recovery Up (%) | Blind Resistance Up (%) |
| Force | Technique Weapon Potency Up (%) | Natural PP Recovery Up (%) | Panic Resistance Up (%) |
| Techter | Natural PB Gauge Accumulation Up (%) | Damage Resistance Up (%) | Poison Resistance Up (%) |
| Braver | Critical Hit Rate Up (%) | Restasigne Heal Amount Up (%) | Burn Resistance Up (%) |
| Bouncer | Dash and Glide PP Recovery (%) | Encore Jump (%) | Freeze Resistance Up (%) |
| Waker | PP Up (flat) | Stationary HP Recovery (%) | Shock Resistance Up (%) |
| Slayer | Downed Critical Hit Rate Up (%) | Reduced Photon Blast Cooldown (%) | Blind Resistance Up (%) |
| All Classes | null | HP Up (flat) | — |
| Select Classes | null | HP Recovery upon Joining Trial (%) | PP Recovery upon Joining Trial (%) |

**Total: 12 documents, 34 effects, 680 level-value pairs.**
