# Family-Tree-MCP

A Model Context Protocol (MCP) server that exposes a RootsMagic genealogy database to Claude Desktop, enabling natural language queries against your family tree.

## Overview

RootsMagic stores your family tree as a SQLite database (`.rmtree` file). This MCP server wraps that database with a set of semantic tools so Claude can answer questions like:

- *"Who are the ancestors of John Smith?"*
- *"Show me everyone born in County Cork, Ireland"*
- *"What events are recorded for Mary O'Brien?"*
- *"List all the descendants of Thomas Jones to 3 generations"*

## Project Structure

```
familytree-mcp/
├── server.py          # MCP server entry point (FastMCP)
├── db.py              # SQLite connection and query helpers
├── tools.py           # Tool definitions and handlers
├── requirements.txt   # Python dependencies
└── README.md
```

## Requirements

- Python 3.10+
- A RootsMagic `.rmtree` file (SQLite database)
- Claude Desktop app

## Installation

```bash
git clone https://github.com/markjennine/Family-Tree-MCP.git
cd Family-Tree-MCP
pip install -r requirements.txt
```

## Configuration

Add the following to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "familytree": {
      "command": "python",
      "args": ["/absolute/path/to/Family-Tree-MCP/server.py"],
      "env": {
        "FAMILYTREE_DB": "/absolute/path/to/your/family.rmtree"
      }
    }
  }
}
```

Restart Claude Desktop after saving the config.

## Available Tools

| Tool | Description |
|---|---|
| `search_people` | Search by name, optionally filtered by birth year |
| `get_person` | Full profile: names, dates, places, notes |
| `get_family` | Parents, siblings, spouses, and children |
| `get_ancestors` | Pedigree chain up to N generations |
| `get_descendants` | Descendant tree up to N generations |
| `get_timeline` | All events for a person in chronological order |
| `find_by_place` | Everyone with a connection to a named place |

## RootsMagic Schema Reference

Key tables used by this server:

| Table | Key Columns |
|---|---|
| `PersonTable` | `PersonID`, `Sex`, `ParentID`, `SpouseID` |
| `NameTable` | `PersonID`, `Surname`, `Given`, `NameType` (0 = primary) |
| `FamilyTable` | `FamilyID`, `FatherID`, `MotherID` |
| `ChildTable` | `FamilyID`, `ChildID` |
| `EventTable` | `EventID`, `OwnerID`, `OwnerType` (0=person, 1=family), `EventType`, `Date`, `PlaceID` |
| `PlaceTable` | `PlaceID`, `PlaceName` |

## Usage Examples

Once connected in Claude Desktop:

> *"Find everyone with the surname Brennan"*  
> *"Tell me about person 142"*  
> *"Who were the parents and children of Margaret Sullivan?"*  
> *"Give me the ancestors of person 56 going back 4 generations"*  
> *"What events happened in Liverpool?"*

## License

MIT
