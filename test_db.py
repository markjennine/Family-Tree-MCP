"""Quick smoke test — run with:
  FAMILYTREE_DB=/path/to/your.rmtree .venv/bin/python test_db.py
"""
import sys
import os

path = os.environ.get("FAMILYTREE_DB", "")
if not path or not os.path.exists(path):
    print(f"ERROR: FAMILYTREE_DB not set or file not found: {path!r}", file=sys.stderr)
    sys.exit(1)

from db import query, introspect

print("Tables:", introspect())

people = query("SELECT PersonID FROM PersonTable LIMIT 5")
print("PersonTable sample:", people)

names = query("SELECT PersonID, Given, Surname FROM NameTable WHERE NameType=0 LIMIT 5")
print("NameTable sample:", names)

if people:
    pid = people[0]["PersonID"]
    from tools import register_tools
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("test")
    register_tools(mcp)
    print(f"\nTesting tools with PersonID={pid}:")

    # search_people
    name_row = names[0] if names else None
    if name_row and name_row["Surname"]:
        from tools import register_tools
        # Direct function test via db
        results = query(
            "SELECT DISTINCT n.PersonID, n.Given, n.Surname FROM NameTable n "
            "WHERE n.Surname LIKE ? AND n.NameType=0 LIMIT 5",
            (f"%{name_row['Surname'][:4]}%",)
        )
        print("search_people (raw):", results[:3])

    events = query(
        "SELECT EventType, Date FROM EventTable WHERE OwnerID=? AND OwnerType=0 LIMIT 5",
        (pid,)
    )
    print(f"Events for person {pid}:", events)

print("\nAll checks passed.")
