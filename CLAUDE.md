# CLAUDE.md — Family-Tree-MCP

This file gives Claude Code persistent context for every session in this project.
Read it fully before writing any code.

---

## Project Purpose

A Python MCP server that wraps a RootsMagic `.rmtree` SQLite database, exposing it to Claude Desktop via 7 semantic tools. The goal is token-efficient, structured access to genealogy data — no raw GEDCOM, no schema reasoning at query time.

---

## Architecture

```
server.py   →   FastMCP entry point, transport=stdio
tools.py    →   All @mcp.tool() decorated functions
db.py       →   SQLite connection + raw query helpers
```

**No frameworks beyond `mcp`.** Keep it simple — this is a thin query wrapper, not an application.

---

## Environment

- **DB path**: read from `FAMILYTREE_DB` environment variable — never hardcode a path
- **Transport**: `stdio` — this runs as a subprocess of Claude Desktop
- **Python**: 3.10+ (use `match` statements and `|` union types freely)

---

## RootsMagic SQLite Schema

Always use these exact table and column names:

### PersonTable
```sql
PersonID    INTEGER PRIMARY KEY
Sex         INTEGER  -- 0=male, 1=female, 2=unknown
ParentID    INTEGER  -- FamilyID where this person is a child (not a PersonID)
SpouseID    INTEGER  -- FamilyID of first spouse family (not a PersonID)
```

### NameTable
```sql
NameID      INTEGER PRIMARY KEY
PersonID    INTEGER  -- FK to PersonTable
Surname     TEXT
Given       TEXT
NameType    INTEGER  -- 0 = primary name, others = alternates
```

### FamilyTable
```sql
FamilyID    INTEGER PRIMARY KEY
FatherID    INTEGER  -- PersonID of father (0 if unknown)
MotherID    INTEGER  -- PersonID of mother (0 if unknown)
```

### ChildTable  ← critical bridge table
```sql
RecID       INTEGER PRIMARY KEY
FamilyID    INTEGER  -- FK to FamilyTable
ChildID     INTEGER  -- PersonID of child
```

### EventTable
```sql
EventID     INTEGER PRIMARY KEY
OwnerID     INTEGER  -- PersonID or FamilyID depending on OwnerType
OwnerType   INTEGER  -- 0 = person event, 1 = family event
EventType   INTEGER  -- see EventTypes below
Date        TEXT     -- RootsMagic date string format
PlaceID     INTEGER  -- FK to PlaceTable (0 if no place)
Details     TEXT
```

### PlaceTable
```sql
PlaceID     INTEGER PRIMARY KEY
PlaceName   TEXT
```

### Common EventType values
```
1  = Birth
2  = Christening
3  = Death
4  = Burial
7  = Marriage
20 = Census
21 = Occupation
```

---

## Key Query Patterns

### Primary name for a person
```sql
SELECT Surname, Given FROM NameTable
WHERE PersonID = ? AND NameType = 0
LIMIT 1
```

### Children of a family
```sql
SELECT p.PersonID, n.Given, n.Surname
FROM ChildTable c
JOIN PersonTable p ON c.ChildID = p.PersonID
JOIN NameTable n ON n.PersonID = p.PersonID AND n.NameType = 0
WHERE c.FamilyID = ?
```

### Families where person is a spouse
```sql
-- Father in a family:
SELECT * FROM FamilyTable WHERE FatherID = ?
-- Mother in a family:
SELECT * FROM FamilyTable WHERE MotherID = ?
```

### Person's family of origin (where they are a child)
```sql
SELECT f.* FROM FamilyTable f
JOIN ChildTable c ON c.FamilyID = f.FamilyID
WHERE c.ChildID = ?
```

---

## Tool Specifications

Implement exactly these 7 tools in `tools.py`. Docstrings become the tool descriptions visible to Claude — make them clear and specific.

### 1. `search_people(name: str, birth_year: int | None = None) -> list[dict]`
- Search `NameTable` with `LIKE '%name%'` across `Given` and `Surname`
- If `birth_year` provided, filter by birth events in `EventTable` (EventType=1)
- Return: `[{person_id, given, surname, birth_year, death_year}]`
- Limit to 50 results

### 2. `get_person(person_id: int) -> dict`
- Join PersonTable + NameTable (all names, flag primary) + EventTable + PlaceTable
- Return: `{person_id, sex, names: [{given, surname, is_primary}], events: [{type, date, place}]}`

### 3. `get_family(person_id: int) -> dict`
- Find family of origin (ChildTable → FamilyTable → father/mother PersonIDs)
- Find spouse families (FamilyTable WHERE FatherID=? OR MotherID=?)
- For each spouse family, get children via ChildTable
- Return: `{parents: [{person_id, name, relationship}], spouses: [{person_id, name, family_id}], children: [{person_id, name}], siblings: [{person_id, name}]}`

### 4. `get_ancestors(person_id: int, generations: int = 4) -> list[dict]`
- Recursive walk: for each person, find their family of origin, get FatherID and MotherID
- Stop at `generations` depth or when no more parents found
- Return: `[{person_id, name, generation, relationship}]` (generation 1 = parents, 2 = grandparents, etc.)
- Cap at 8 generations maximum to avoid runaway queries

### 5. `get_descendants(person_id: int, generations: int = 3) -> list[dict]`
- Recursive walk: find all families where person is father/mother, get children via ChildTable, recurse
- Return: `[{person_id, name, generation, relationship}]`
- Cap at 6 generations maximum

### 6. `get_timeline(person_id: int) -> list[dict]`
- Fetch all EventTable rows where OwnerID=person_id AND OwnerType=0
- Also fetch family events (OwnerType=1) for families this person belongs to
- Join PlaceTable for place names
- Return sorted by Date: `[{event_type, event_type_name, date, place, details}]`

### 7. `find_by_place(place_name: str) -> list[dict]`
- Search PlaceTable with `LIKE '%place_name%'`
- For each matching PlaceID, get EventTable rows, resolve OwnerID to person names
- Return: `[{person_id, name, event_type, date, place_name}]`
- Limit to 100 results

---

## db.py Requirements

```python
import sqlite3, os

def get_conn() -> sqlite3.Connection:
    path = os.environ.get("FAMILYTREE_DB")
    if not path:
        raise RuntimeError("FAMILYTREE_DB environment variable not set")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # enables dict-like access
    return conn

def query(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

def query_one(sql: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
```

Use `query()` and `query_one()` throughout `tools.py` — never open connections directly in tool handlers.

---

## server.py Requirements

```python
from mcp.server.fastmcp import FastMCP
from tools import register_tools

mcp = FastMCP("FamilyTree")
register_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

## Code Style Rules

- Type-hint all function signatures
- Docstrings on every tool (they become Claude's tool descriptions)
- Return empty list `[]` or `{}` rather than raising exceptions for not-found cases
- Always filter `NameType = 0` when fetching the primary name
- Treat `FatherID = 0` and `MotherID = 0` as unknown (not a real person)
- Log errors to stderr, not stdout (stdout is the MCP protocol stream)

---

## Testing Without Claude Desktop

Run the server manually and send JSON-RPC via stdin to verify tools work:

```bash
FAMILYTREE_DB=/path/to/your.rmtree python server.py
```

Or write a quick test script that calls `db.query()` directly against a test database.

---

## What NOT to Do

- Don't add a web server, HTTP transport, or REST API — stdio only
- Don't cache query results — RootsMagic may update the file
- Don't expose raw SQL as a tool — schema reasoning belongs here, not in Claude
- Don't use an ORM — plain `sqlite3` is correct here
- Don't add authentication — Claude Desktop handles process isolation
