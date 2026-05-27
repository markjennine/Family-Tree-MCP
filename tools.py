import sys
from collections import deque
from db import query, query_one

# Loaded lazily on first tool call to avoid import-time DB access
_EVENT_TYPE_NAMES: dict[int, str] | None = None


def _event_type_names() -> dict[int, str]:
    global _EVENT_TYPE_NAMES
    if _EVENT_TYPE_NAMES is None:
        try:
            rows = query("SELECT FactTypeID, Name FROM FactTypeTable")
            _EVENT_TYPE_NAMES = {r["FactTypeID"]: r["Name"] for r in rows}
        except Exception as e:
            print(f"Failed to load FactTypeTable: {e}", file=sys.stderr)
            _EVENT_TYPE_NAMES = {}
    return _EVENT_TYPE_NAMES


SEX_NAMES: dict[int, str] = {0: "Unknown", 1: "Male", 2: "Female"}


def _person_name(person_id: int) -> str:
    row = query_one(
        "SELECT Given, Surname FROM NameTable WHERE OwnerID = ? AND IsPrimary = 1 LIMIT 1",
        (person_id,),
    )
    if not row:
        return "Unknown"
    given = row.get("Given") or ""
    surname = row.get("Surname") or ""
    return f"{given} {surname}".strip() or "Unknown"


def _format_date(date_str: str | None) -> str:
    """Convert RootsMagic date encoding to a readable string.

    Format: D.+YYYYMMDD..+00000000.. where 00 in MM or DD means unknown.
    """
    if not date_str or date_str == ".":
        return ""
    if date_str.startswith("D.+") and len(date_str) >= 11:
        yyyymmdd = date_str[3:11]
        y, m, d = yyyymmdd[:4], yyyymmdd[4:6], yyyymmdd[6:8]
        if d != "00" and m != "00":
            return f"{y}-{m}-{d}"
        elif m != "00":
            return f"{y}-{m}"
        elif y != "0000":
            return y
        return ""
    return date_str


def _extract_year(date_str: str | None) -> int | None:
    if not date_str or date_str == ".":
        return None
    if date_str.startswith("D.+") and len(date_str) >= 7:
        y = date_str[3:7]
        return int(y) if y.isdigit() and y != "0000" else None
    return None


def _person_vitals(person_id: int) -> dict:
    """Return birth and death date/place for a person, all fields None if not recorded."""
    rows = query(
        """
        SELECT e.EventType, e.Date, p.Name AS PlaceName
        FROM EventTable e
        LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID AND e.PlaceID != 0
        WHERE e.OwnerID = ? AND e.OwnerType = 0 AND e.EventType IN (1, 3)
        """,
        (person_id,),
    )
    birth_date: str | None = None
    birth_place: str | None = None
    death_date: str | None = None
    death_place: str | None = None
    for row in rows:
        formatted = _format_date(row["Date"]) or None
        place = row["PlaceName"] or None
        if row["EventType"] == 1:
            birth_date, birth_place = formatted, place
        elif row["EventType"] == 3:
            death_date, death_place = formatted, place
    return {
        "birth_date": birth_date,
        "birth_place": birth_place,
        "death_date": death_date,
        "death_place": death_place,
    }


def register_tools(mcp) -> None:

    @mcp.tool()
    def search_people(name: str, birth_year: int | None = None) -> list[dict]:
        """Search for people by name (given or surname). Optionally filter by birth year.
        Returns up to 50 matches with basic vital info."""
        like = f"%{name}%"
        if birth_year is None:
            rows = query(
                """
                SELECT OwnerID AS PersonID, Given, Surname, BirthYear, DeathYear
                FROM NameTable
                WHERE (Given LIKE ? OR Surname LIKE ?) AND IsPrimary = 1
                LIMIT 50
                """,
                (like, like),
            )
        else:
            rows = query(
                """
                SELECT OwnerID AS PersonID, Given, Surname, BirthYear, DeathYear
                FROM NameTable
                WHERE (Given LIKE ? OR Surname LIKE ?) AND IsPrimary = 1
                  AND BirthYear = ?
                LIMIT 50
                """,
                (like, like, birth_year),
            )

        return [
            {
                "person_id": r["PersonID"],
                "given": r["Given"] or "",
                "surname": r["Surname"] or "",
                "birth_year": r["BirthYear"] or None,
                "death_year": r["DeathYear"] or None,
            }
            for r in rows
        ]

    @mcp.tool()
    def get_person(person_id: int) -> dict:
        """Get full profile for a person: all names, sex, and all personal events with dates and places."""
        person = query_one("SELECT * FROM PersonTable WHERE PersonID = ?", (person_id,))
        if not person:
            return {}

        names = query(
            "SELECT Given, Surname, IsPrimary FROM NameTable WHERE OwnerID = ? ORDER BY IsPrimary DESC",
            (person_id,),
        )
        name_list = [
            {
                "given": r["Given"] or "",
                "surname": r["Surname"] or "",
                "is_primary": bool(r["IsPrimary"]),
            }
            for r in names
        ]

        events_raw = query(
            """
            SELECT e.EventType, e.Date, e.Details, p.Name AS PlaceName
            FROM EventTable e
            LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID AND e.PlaceID != 0
            WHERE e.OwnerID = ? AND e.OwnerType = 0
            ORDER BY e.SortDate
            """,
            (person_id,),
        )
        events = [
            {
                "event_type": r["EventType"],
                "event_type_name": _event_type_names().get(r["EventType"], f"Event {r['EventType']}"),
                "date": _format_date(r["Date"]),
                "place": r["PlaceName"] or "",
                "details": r["Details"] or "",
            }
            for r in events_raw
        ]

        return {
            "person_id": person_id,
            "sex": SEX_NAMES.get(person.get("Sex", 0), "Unknown"),
            "names": name_list,
            "events": events,
        }

    @mcp.tool()
    def get_family(person_id: int) -> dict:
        """Get family relationships for a person: parents, siblings, spouses, and children."""
        parents: list[dict] = []
        siblings: list[dict] = []
        spouses: list[dict] = []
        children: list[dict] = []

        # Family of origin
        origin = query_one(
            """
            SELECT f.FamilyID, f.FatherID, f.MotherID
            FROM FamilyTable f
            JOIN ChildTable c ON c.FamilyID = f.FamilyID
            WHERE c.ChildID = ?
            LIMIT 1
            """,
            (person_id,),
        )
        if origin:
            origin_family_id = origin["FamilyID"]
            if origin["FatherID"] and origin["FatherID"] != 0:
                parents.append(
                    {"person_id": origin["FatherID"], "name": _person_name(origin["FatherID"]), "relationship": "Father"}
                )
            if origin["MotherID"] and origin["MotherID"] != 0:
                parents.append(
                    {"person_id": origin["MotherID"], "name": _person_name(origin["MotherID"]), "relationship": "Mother"}
                )

            sibling_rows = query(
                "SELECT ChildID FROM ChildTable WHERE FamilyID = ? AND ChildID != ?",
                (origin_family_id, person_id),
            )
            siblings = [
                {"person_id": r["ChildID"], "name": _person_name(r["ChildID"])}
                for r in sibling_rows
            ]

        # Spouse families
        spouse_families = query(
            "SELECT * FROM FamilyTable WHERE FatherID = ? OR MotherID = ?",
            (person_id, person_id),
        )
        for fam in spouse_families:
            fam_id = fam["FamilyID"]
            spouse_id = fam["MotherID"] if fam["FatherID"] == person_id else fam["FatherID"]
            spouse_entry: dict = {"family_id": fam_id}
            if spouse_id and spouse_id != 0:
                spouse_entry["person_id"] = spouse_id
                spouse_entry["name"] = _person_name(spouse_id)
            else:
                spouse_entry["person_id"] = None
                spouse_entry["name"] = "Unknown"
            spouses.append(spouse_entry)

            child_rows = query(
                "SELECT ChildID FROM ChildTable WHERE FamilyID = ?",
                (fam_id,),
            )
            for cr in child_rows:
                child_entry = {"person_id": cr["ChildID"], "name": _person_name(cr["ChildID"])}
                if not any(c["person_id"] == cr["ChildID"] for c in children):
                    children.append(child_entry)

        return {
            "parents": parents,
            "siblings": siblings,
            "spouses": spouses,
            "children": children,
        }

    @mcp.tool()
    def get_ancestors(person_id: int, generations: int = 4) -> list[dict]:
        """Walk up the family tree from a person, returning ancestors up to the specified number
        of generations (default 4, max 8). Generation 1 = parents, 2 = grandparents, etc.

        Each ancestor dict contains: person_id, name, generation, relationship,
        birth_date, birth_place, death_date, death_place. Vital fields are None when
        not recorded in the database."""
        generations = min(generations, 8)
        visited: set[int] = {person_id}
        results: list[dict] = []
        queue: deque[tuple[int, int]] = deque([(person_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if depth >= generations:
                continue

            origin = query_one(
                """
                SELECT f.FatherID, f.MotherID
                FROM FamilyTable f
                JOIN ChildTable c ON c.FamilyID = f.FamilyID
                WHERE c.ChildID = ?
                LIMIT 1
                """,
                (current_id,),
            )
            if not origin:
                continue

            next_gen = depth + 1
            for pid, sex in [(origin["FatherID"], "Father"), (origin["MotherID"], "Mother")]:
                if not pid or pid == 0 or pid in visited:
                    continue
                visited.add(pid)
                vitals = _person_vitals(pid)
                results.append(
                    {
                        "person_id": pid,
                        "name": _person_name(pid),
                        "generation": next_gen,
                        "relationship": _ancestor_label(next_gen, sex),
                        "birth_date": vitals["birth_date"],
                        "birth_place": vitals["birth_place"],
                        "death_date": vitals["death_date"],
                        "death_place": vitals["death_place"],
                    }
                )
                queue.append((pid, next_gen))

        results.sort(key=lambda r: (r["generation"], r["name"]))
        return results

    @mcp.tool()
    def get_descendants(person_id: int, generations: int = 3) -> list[dict]:
        """Walk down the family tree from a person, returning descendants up to the specified
        number of generations (default 3, max 6). Generation 1 = children, 2 = grandchildren, etc."""
        generations = min(generations, 6)
        visited: set[int] = {person_id}
        results: list[dict] = []
        queue: deque[tuple[int, int]] = deque([(person_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if depth >= generations:
                continue

            families = query(
                "SELECT FamilyID FROM FamilyTable WHERE FatherID = ? OR MotherID = ?",
                (current_id, current_id),
            )
            for fam in families:
                child_rows = query(
                    "SELECT ChildID FROM ChildTable WHERE FamilyID = ?",
                    (fam["FamilyID"],),
                )
                for cr in child_rows:
                    cid = cr["ChildID"]
                    if cid in visited:
                        continue
                    visited.add(cid)
                    next_gen = depth + 1
                    results.append(
                        {
                            "person_id": cid,
                            "name": _person_name(cid),
                            "generation": next_gen,
                            "relationship": _descendant_label(next_gen),
                        }
                    )
                    queue.append((cid, next_gen))

        results.sort(key=lambda r: (r["generation"], r["name"]))
        return results

    @mcp.tool()
    def get_timeline(person_id: int) -> list[dict]:
        """Return all events for a person in chronological order, including personal events
        and family events (marriages, etc.) for families they belong to."""
        events: list[dict] = []

        personal = query(
            """
            SELECT e.EventType, e.Date, e.SortDate, e.Details, p.Name AS PlaceName
            FROM EventTable e
            LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID AND e.PlaceID != 0
            WHERE e.OwnerID = ? AND e.OwnerType = 0
            """,
            (person_id,),
        )
        for r in personal:
            events.append(
                {
                    "event_type": r["EventType"],
                    "event_type_name": _event_type_names().get(r["EventType"], f"Event {r['EventType']}"),
                    "date": _format_date(r["Date"]),
                    "place": r["PlaceName"] or "",
                    "details": r["Details"] or "",
                    "_sort": r["SortDate"] or 9223372036854775807,
                }
            )

        # Family IDs this person belongs to
        family_ids: list[int] = []
        for fam in query(
            "SELECT FamilyID FROM FamilyTable WHERE FatherID = ? OR MotherID = ?",
            (person_id, person_id),
        ):
            family_ids.append(fam["FamilyID"])

        origin = query_one(
            """
            SELECT f.FamilyID FROM FamilyTable f
            JOIN ChildTable c ON c.FamilyID = f.FamilyID
            WHERE c.ChildID = ? LIMIT 1
            """,
            (person_id,),
        )
        if origin and origin["FamilyID"] not in family_ids:
            family_ids.append(origin["FamilyID"])

        if family_ids:
            placeholders = ",".join("?" * len(family_ids))
            family_events = query(
                f"""
                SELECT e.EventType, e.Date, e.SortDate, e.Details, p.Name AS PlaceName
                FROM EventTable e
                LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID AND e.PlaceID != 0
                WHERE e.OwnerID IN ({placeholders}) AND e.OwnerType = 1
                """,
                tuple(family_ids),
            )
            for r in family_events:
                events.append(
                    {
                        "event_type": r["EventType"],
                        "event_type_name": _event_type_names().get(r["EventType"], f"Event {r['EventType']}"),
                        "date": _format_date(r["Date"]),
                        "place": r["PlaceName"] or "",
                        "details": r["Details"] or "",
                        "_sort": r["SortDate"] or 9223372036854775807,
                    }
                )

        events.sort(key=lambda e: e.pop("_sort"))
        return events

    @mcp.tool()
    def find_by_place(place_name: str) -> list[dict]:
        """Find people and events associated with a place. Searches place names with a partial
        match. Returns up to 100 results sorted by place then surname."""
        like = f"%{place_name}%"
        matching_places = query(
            "SELECT PlaceID, Name FROM PlaceTable WHERE Name LIKE ?",
            (like,),
        )
        if not matching_places:
            return []

        place_ids = [p["PlaceID"] for p in matching_places]
        place_name_map = {p["PlaceID"]: p["Name"] for p in matching_places}

        placeholders = ",".join("?" * len(place_ids))
        events = query(
            f"""
            SELECT e.EventID, e.OwnerID, e.OwnerType, e.EventType, e.Date, e.PlaceID
            FROM EventTable e
            WHERE e.PlaceID IN ({placeholders})
            """,
            tuple(place_ids),
        )

        seen: set[tuple[int, int]] = set()
        results: list[dict] = []

        for ev in events:
            owner_id = ev["OwnerID"]
            owner_type = ev["OwnerType"]

            if owner_type == 0:
                pid = owner_id
            else:
                fam = query_one(
                    "SELECT FatherID, MotherID FROM FamilyTable WHERE FamilyID = ?",
                    (owner_id,),
                )
                if not fam:
                    continue
                pid = fam["FatherID"] if fam["FatherID"] != 0 else fam["MotherID"]
                if not pid or pid == 0:
                    continue

            key = (pid, ev["EventID"])
            if key in seen:
                continue
            seen.add(key)

            name_row = query_one(
                "SELECT Given, Surname FROM NameTable WHERE OwnerID = ? AND IsPrimary = 1 LIMIT 1",
                (pid,),
            )
            given = name_row["Given"] or "" if name_row else ""
            surname = name_row["Surname"] or "" if name_row else ""
            full_name = f"{given} {surname}".strip() or "Unknown"

            results.append(
                {
                    "person_id": pid,
                    "name": full_name,
                    "event_type": ev["EventType"],
                    "event_type_name": _event_type_names().get(ev["EventType"], f"Event {ev['EventType']}"),
                    "date": _format_date(ev["Date"]),
                    "place_name": place_name_map.get(ev["PlaceID"], ""),
                    "_surname": surname,
                }
            )

            if len(results) >= 100:
                break

        results.sort(key=lambda r: (r["place_name"], r["_surname"]))
        for r in results:
            del r["_surname"]

        return results


def _ancestor_label(generation: int, sex: str) -> str:
    match generation:
        case 1:
            return sex
        case 2:
            return f"Grand{sex.lower()}"
        case 3:
            return f"Great-grand{sex.lower()}"
        case _:
            prefix = "Great-" * (generation - 2)
            return f"{prefix}grand{sex.lower()}"


def _descendant_label(generation: int) -> str:
    match generation:
        case 1:
            return "Child"
        case 2:
            return "Grandchild"
        case 3:
            return "Great-grandchild"
        case _:
            prefix = "Great-" * (generation - 2)
            return f"{prefix}grandchild"
