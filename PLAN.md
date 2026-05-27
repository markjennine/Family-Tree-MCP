# Implementation Plan — Family-Tree-MCP

This document is the step-by-step implementation guide for Claude Code.
Follow the phases in order. Check off each task as you complete it.

---

## Phase 0 — Setup

- [ ] Create `requirements.txt`:
  ```
  mcp>=1.0.0
  ```
- [ ] Verify Python version is 3.10+ (`python --version`)
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Confirm `mcp` package installed: `python -c "from mcp.server.fastmcp import FastMCP; print('ok')`

---

## Phase 1 — Database Layer (`db.py`)

Create `db.py` first. Everything else depends on it.

- [ ] Implement `get_conn()` — reads `FAMILYTREE_DB` env var, raises `RuntimeError` if not set, sets `row_factory = sqlite3.Row`
- [ ] Implement `query(sql, params) -> list[dict]` — executes and returns all rows as dicts
- [ ] Implement `query_one(sql, params) -> dict | None` — returns single row or None
- [ ] Add a `introspect()` helper that runs `SELECT name FROM sqlite_master WHERE type='table'` — useful for debugging schema issues

**Smoke test** (run with a real `.rmtree` file):
```bash
FAMILYTREE_DB=/path/to/your.rmtree python -c "
from db import query
rows = query('SELECT PersonID FROM PersonTable LIMIT 3')
print(rows)
"
```

---

## Phase 2 — Core Tools (`tools.py`)

Implement tools one at a time, testing each before moving on.

### 2a. `search_people`
- [ ] Search `NameTable` with `LIKE` on both `Given` and `Surname`
- [ ] Join to `EventTable` (EventType=1, OwnerType=0) for birth year if `birth_year` provided
- [ ] Extract year from `Date` field (RootsMagic stores dates as strings — extract 4-digit year with a simple string parse or `SUBSTR`)
- [ ] Return list of dicts, limit 50

### 2b. `get_person`
- [ ] Fetch from `PersonTable`
- [ ] Fetch all names from `NameTable` (flag `is_primary` where `NameType=0`)
- [ ] Fetch all person events from `EventTable` (OwnerType=0), join `PlaceTable`
- [ ] Map `EventType` integers to human-readable strings (Birth, Death, Marriage, etc.)
- [ ] Return structured dict

### 2c. `get_family`
- [ ] Find family of origin: `ChildTable → FamilyTable → FatherID/MotherID`
- [ ] Resolve FatherID and MotherID to names (skip if 0)
- [ ] Find siblings: other ChildTable entries for same FamilyID, excluding self
- [ ] Find spouse families: `FamilyTable WHERE FatherID=? OR MotherID=?`
- [ ] For each spouse family: resolve the spouse PersonID and get children via ChildTable
- [ ] Return `{parents, siblings, spouses, children}`

### 2d. `get_ancestors`
- [ ] Implement iteratively using a queue (avoid deep recursion)
- [ ] Queue starts with `[(person_id, 0)]` — (id, generation)
- [ ] For each entry: find family of origin → add FatherID and MotherID at generation+1
- [ ] Stop when generation reaches `generations` param or no more parents
- [ ] Hard cap at 8 generations
- [ ] Deduplicate by PersonID (family trees can have loops in rare cases)
- [ ] Return list sorted by generation then name

### 2e. `get_descendants`
- [ ] Implement iteratively using a queue
- [ ] Queue starts with `[(person_id, 0)]`
- [ ] For each entry: find all families as parent → get children via ChildTable → add at generation+1
- [ ] Hard cap at 6 generations
- [ ] Deduplicate by PersonID
- [ ] Return list sorted by generation

### 2f. `get_timeline`
- [ ] Fetch person events (OwnerType=0, OwnerID=person_id)
- [ ] Fetch families for this person (as father, as mother, as child) → get family events (OwnerType=1) for those FamilyIDs
- [ ] Join PlaceTable for each event
- [ ] Parse and sort by Date (RootsMagic date strings are roughly sortable as strings, but handle empties)
- [ ] Return chronological list with human-readable event type names

### 2g. `find_by_place`
- [ ] Search `PlaceTable` with `LIKE '%place_name%'`
- [ ] For each matching place: get events, resolve OwnerID+OwnerType to person names
- [ ] For OwnerType=1 (family events): resolve FatherID or MotherID as the representative person
- [ ] Deduplicate person+event combinations
- [ ] Limit to 100 results
- [ ] Return list sorted by place name then person surname

---

## Phase 3 — Server Entry Point (`server.py`)

- [ ] Implement `server.py` exactly as specified in CLAUDE.md
- [ ] Verify it imports cleanly: `python -c "import server"`
- [ ] Test stdio transport starts without error: `FAMILYTREE_DB=/path/to/your.rmtree python server.py` (should hang waiting for input — that's correct)

---

## Phase 4 — Integration Test

Run a manual end-to-end test by piping JSON-RPC to the server:

```bash
echo '{"jsonrpc":"2.0","method":"tools/list","id":1}' | \
  FAMILYTREE_DB=/path/to/your.rmtree python server.py
```

Expected: JSON response listing all 7 tools.

Then test a tool call:
```bash
echo '{"jsonrpc":"2.0","method":"tools/call","id":2,"params":{"name":"search_people","arguments":{"name":"Smith"}}}' | \
  FAMILYTREE_DB=/path/to/your.rmtree python server.py
```

---

## Phase 5 — Claude Desktop Config

Generate the config snippet for the user. Print the exact JSON they need to add to their Claude Desktop config, with the correct absolute path to `server.py`.

The config location is:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "familytree": {
      "command": "python",
      "args": ["/ABSOLUTE/PATH/TO/Family-Tree-MCP/server.py"],
      "env": {
        "FAMILYTREE_DB": "/ABSOLUTE/PATH/TO/your.rmtree"
      }
    }
  }
}
```

---

## Known Gotchas

**RootsMagic date format**
Dates in `EventTable.Date` are stored as custom-formatted strings, not ISO dates. They look like `"1 Jan 1920"` or `"Abt 1850"` or `"Bef 1900"`. Don't try to parse them precisely — surface them as-is and let Claude interpret them.

**FatherID/MotherID = 0 means unknown**
Always check `!= 0` before treating these as real PersonIDs.

**PersonTable.ParentID and SpouseID are FamilyIDs**
These are shortcuts but can be stale if someone has multiple families. Always resolve via `ChildTable` and `FamilyTable` for accuracy.

**NameType = 0 is primary**
People can have multiple names (married name, AKA, etc.). Always filter `NameType = 0` when you want the canonical display name.

**stdout is the MCP wire**
Never print to stdout in any tool handler. Use `import sys; print(..., file=sys.stderr)` for debug output.

---

## Completion Checklist

- [ ] `db.py` — connection, query, query_one helpers
- [ ] `tools.py` — all 7 tools implemented and registered
- [ ] `server.py` — FastMCP entry point
- [ ] `requirements.txt` — `mcp>=1.0.0`
- [ ] Manual JSON-RPC test passes
- [ ] Claude Desktop config snippet generated for user
