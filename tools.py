import sys
import xml.etree.ElementTree as ET
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


def _vital_fact_type_ids() -> tuple[int, int]:
    """Return (birth_type_id, death_type_id) by looking up names in the already-cached
    FactTypeTable mapping. Falls back to 1 / 3 when the table is unavailable."""
    names = _event_type_names()  # {FactTypeID: Name}
    birth_id = next((tid for tid, n in names.items() if n.lower() == "birth"), 1)
    death_id = next((tid for tid, n in names.items() if n.lower() == "death"), 3)
    return birth_id, death_id


def _person_vitals(person_id: int) -> dict:
    """Return birth and death date/place for a person, all fields None if not recorded."""
    birth_type, death_type = _vital_fact_type_ids()
    rows = query(
        """
        SELECT e.EventType, e.Date, p.Name AS PlaceName
        FROM EventTable e
        LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID AND e.PlaceID != 0
        WHERE e.OwnerID = ? AND e.OwnerType = 0 AND e.EventType IN (?, ?)
        """,
        (person_id, birth_type, death_type),
    )
    birth_date: str | None = None
    birth_place: str | None = None
    death_date: str | None = None
    death_place: str | None = None
    for row in rows:
        formatted = _format_date(row["Date"]) or None
        place = row["PlaceName"] or None
        if row["EventType"] == birth_type:
            birth_date, birth_place = formatted, place
        elif row["EventType"] == death_type:
            death_date, death_place = formatted, place
    return {
        "birth_date": birth_date,
        "birth_place": birth_place,
        "death_date": death_date,
        "death_place": death_place,
    }


def _batch_person_vitals(person_ids: list[int]) -> dict[int, dict]:
    """Batch-fetch birth/death vitals for multiple people in a single query."""
    if not person_ids:
        return {}
    birth_type, death_type = _vital_fact_type_ids()
    ph = ",".join("?" * len(person_ids))
    rows = query(
        f"""
        SELECT e.OwnerID, e.EventType, e.Date, p.Name AS PlaceName
        FROM EventTable e
        LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID AND e.PlaceID != 0
        WHERE e.OwnerID IN ({ph}) AND e.OwnerType = 0 AND e.EventType IN (?, ?)
        """,
        (*person_ids, birth_type, death_type),
    )
    result: dict[int, dict] = {
        pid: {"birth_date": None, "birth_place": None, "death_date": None, "death_place": None}
        for pid in person_ids
    }
    for row in rows:
        pid = row["OwnerID"]
        formatted = _format_date(row["Date"]) or None
        place = row["PlaceName"] or None
        if row["EventType"] == birth_type:
            result[pid]["birth_date"] = formatted
            result[pid]["birth_place"] = place
        elif row["EventType"] == death_type:
            result[pid]["death_date"] = formatted
            result[pid]["death_place"] = place
    return result


def _compute_people_stats(people: list[dict]) -> dict:
    """Compute missing-vital counts for a collection of person dicts.

    Expects each dict to have birth_date, birth_place, death_date, death_place keys
    (values are None when not recorded). When a sex key is present, also counts
    missing_sex (people where sex is "Unknown").
    """
    total = len(people)
    missing_birth_date = sum(1 for p in people if not p.get("birth_date"))
    missing_death_date = sum(1 for p in people if not p.get("death_date"))
    missing_birth_place = sum(1 for p in people if not p.get("birth_place"))
    missing_death_place = sum(1 for p in people if not p.get("death_place"))
    missing_any_vital = sum(
        1 for p in people
        if not p.get("birth_date") or not p.get("death_date")
        or not p.get("birth_place") or not p.get("death_place")
    )
    stats = {
        "total": total,
        "missing_birth_date": missing_birth_date,
        "missing_death_date": missing_death_date,
        "missing_birth_place": missing_birth_place,
        "missing_death_place": missing_death_place,
        "missing_any_vital": missing_any_vital,
    }
    if people and "sex" in people[0]:
        stats["missing_sex"] = sum(1 for p in people if p.get("sex") == "Unknown")
    return stats


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
    def get_people(person_ids: list[int]) -> list[dict]:
        """Get full profiles for multiple people in a single call. Accepts up to 500 person IDs
        and returns full profiles (names, sex, personal events) using the same schema as get_person.
        Ideal for bulk analysis after get_ancestors or get_descendants."""
        if not person_ids:
            return []

        unique_ids = list(dict.fromkeys(person_ids))[:500]
        ph = ",".join("?" * len(unique_ids))

        persons = query(
            f"SELECT PersonID, Sex FROM PersonTable WHERE PersonID IN ({ph})",
            tuple(unique_ids),
        )
        if not persons:
            return []

        found_ids = [p["PersonID"] for p in persons]
        person_sex = {p["PersonID"]: p["Sex"] for p in persons}
        fph = ",".join("?" * len(found_ids))

        names_rows = query(
            f"SELECT OwnerID, Given, Surname, IsPrimary FROM NameTable WHERE OwnerID IN ({fph}) ORDER BY IsPrimary DESC",
            tuple(found_ids),
        )
        events_rows = query(
            f"""
            SELECT e.OwnerID, e.EventType, e.Date, e.Details, p.Name AS PlaceName
            FROM EventTable e
            LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID AND e.PlaceID != 0
            WHERE e.OwnerID IN ({fph}) AND e.OwnerType = 0
            ORDER BY e.OwnerID, e.SortDate
            """,
            tuple(found_ids),
        )

        names_by_person: dict[int, list[dict]] = {pid: [] for pid in found_ids}
        for r in names_rows:
            names_by_person[r["OwnerID"]].append(
                {
                    "given": r["Given"] or "",
                    "surname": r["Surname"] or "",
                    "is_primary": bool(r["IsPrimary"]),
                }
            )

        events_by_person: dict[int, list[dict]] = {pid: [] for pid in found_ids}
        for r in events_rows:
            events_by_person[r["OwnerID"]].append(
                {
                    "event_type": r["EventType"],
                    "event_type_name": _event_type_names().get(r["EventType"], f"Event {r['EventType']}"),
                    "date": _format_date(r["Date"]),
                    "place": r["PlaceName"] or "",
                    "details": r["Details"] or "",
                }
            )

        id_order = {pid: i for i, pid in enumerate(unique_ids)}
        results = [
            {
                "person_id": pid,
                "sex": SEX_NAMES.get(person_sex.get(pid, 0), "Unknown"),
                "names": names_by_person.get(pid, []),
                "events": events_by_person.get(pid, []),
            }
            for pid in found_ids
        ]
        results.sort(key=lambda r: id_order.get(r["person_id"], 0))
        return results

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

        all_pids = list(dict.fromkeys(
            [e["person_id"] for grp in (parents, siblings, children) for e in grp if e.get("person_id")]
            + [s["person_id"] for s in spouses if s.get("person_id")]
        ))
        vitals_by_pid = _batch_person_vitals(all_pids)
        stats = _compute_people_stats(list(vitals_by_pid.values()))

        return {
            "parents": parents,
            "siblings": siblings,
            "spouses": spouses,
            "children": children,
            "stats": stats,
        }

    @mcp.tool()
    def get_ancestors(person_id: int, generations: int = 4) -> dict:
        """Walk up the family tree from a person, returning ancestors up to the specified number
        of generations (default 4, max 8). Generation 1 = parents, 2 = grandparents, etc.

        Returns {"ancestors": [...], "stats": {...}} where stats summarises missing vital data
        across the result set (total, missing_birth_date, missing_death_date,
        missing_birth_place, missing_death_place, missing_sex, missing_any_vital).

        Each ancestor dict contains: person_id, name, sex, generation, relationship,
        birth_date, birth_place, death_date, death_place, parent_ids, spouse_ids. Vital
        fields are None when not recorded in the database. sex is "Unknown" when not
        recorded. parent_ids and spouse_ids list person_id values of parents/spouses that
        are also present in the result set — making the full family structure self-describing
        without additional tool calls."""
        generations = min(generations, 8)
        visited: set[int] = {person_id}
        results: list[dict] = []
        queue: deque[tuple[int, int]] = deque([(person_id, 0)])
        parent_map: dict[int, list[int]] = {}  # person_id -> their parent IDs found in BFS

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

            raw_parents = [p for p in [origin["FatherID"], origin["MotherID"]] if p and p != 0]
            if raw_parents:
                parent_map[current_id] = raw_parents

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

        result_ids = {r["person_id"] for r in results}
        for r in results:
            r["parent_ids"] = [p for p in parent_map.get(r["person_id"], []) if p in result_ids]

        spouse_map: dict[int, list[int]] = {pid: [] for pid in result_ids}
        if result_ids:
            ph = ",".join("?" * len(result_ids))
            fam_rows = query(
                f"SELECT FatherID, MotherID FROM FamilyTable WHERE FatherID IN ({ph}) OR MotherID IN ({ph})",
                (*result_ids, *result_ids),
            )
            for fam in fam_rows:
                f_id, m_id = fam["FatherID"], fam["MotherID"]
                if f_id and f_id != 0 and m_id and m_id != 0:
                    if f_id in result_ids and m_id in result_ids:
                        if m_id not in spouse_map[f_id]:
                            spouse_map[f_id].append(m_id)
                        if f_id not in spouse_map[m_id]:
                            spouse_map[m_id].append(f_id)

        for r in results:
            r["spouse_ids"] = spouse_map.get(r["person_id"], [])

        if results:
            all_ids = [r["person_id"] for r in results]
            ph = ",".join("?" * len(all_ids))
            sex_rows = query(
                f"SELECT PersonID, Sex FROM PersonTable WHERE PersonID IN ({ph})",
                tuple(all_ids),
            )
            sex_by_id = {row["PersonID"]: SEX_NAMES.get(row["Sex"], "Unknown") for row in sex_rows}
            for r in results:
                r["sex"] = sex_by_id.get(r["person_id"], "Unknown")

        results.sort(key=lambda r: (r["generation"], r["name"]))
        return {"ancestors": results, "stats": _compute_people_stats(results)}

    @mcp.tool()
    def get_descendants(person_id: int, generations: int = 3) -> dict:
        """Walk down the family tree from a person, returning descendants up to the specified
        number of generations (default 3, max 6). Generation 1 = children, 2 = grandchildren, etc.

        Returns {"descendants": [...], "stats": {...}} where stats summarises missing vital data
        across the result set (total, missing_birth_date, missing_death_date,
        missing_birth_place, missing_death_place, missing_sex, missing_any_vital).

        Each descendant dict contains: person_id, name, sex, generation, relationship,
        birth_date, birth_place, death_date, death_place. sex is "Unknown" when not recorded."""
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
                    vitals = _person_vitals(cid)
                    results.append(
                        {
                            "person_id": cid,
                            "name": _person_name(cid),
                            "generation": next_gen,
                            "relationship": _descendant_label(next_gen),
                            "birth_date": vitals["birth_date"],
                            "birth_place": vitals["birth_place"],
                            "death_date": vitals["death_date"],
                            "death_place": vitals["death_place"],
                        }
                    )
                    queue.append((cid, next_gen))

        if results:
            all_ids = [r["person_id"] for r in results]
            ph = ",".join("?" * len(all_ids))
            sex_rows = query(
                f"SELECT PersonID, Sex FROM PersonTable WHERE PersonID IN ({ph})",
                tuple(all_ids),
            )
            sex_by_id = {row["PersonID"]: SEX_NAMES.get(row["Sex"], "Unknown") for row in sex_rows}
            for r in results:
                r["sex"] = sex_by_id.get(r["person_id"], "Unknown")

        results.sort(key=lambda r: (r["generation"], r["name"]))
        return {"descendants": results, "stats": _compute_people_stats(results)}

    @mcp.tool()
    def get_timeline(person_id: int) -> list[dict]:
        """Return all events for a person in chronological order, including personal events
        and family events (marriages, etc.) for families where they are a spouse or parent.
        Events from the person's family of origin (parents' marriage, etc.) are excluded."""
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

        # Family IDs where this person is a spouse/parent (not family of origin)
        family_ids: list[int] = []
        for fam in query(
            "SELECT FamilyID FROM FamilyTable WHERE FatherID = ? OR MotherID = ?",
            (person_id, person_id),
        ):
            family_ids.append(fam["FamilyID"])

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

    @mcp.tool()
    def get_home_person() -> dict:
        """Return the full profile of the tree's designated home/root person.

        Uses the RootPerson field stored in ConfigTable — no guesswork about IDs.
        Returns the same structure as get_person(): person_id, sex, names, events.
        Returns {} if no home person is configured or the person no longer exists."""
        config = query_one(
            "SELECT DataRec FROM ConfigTable WHERE RecType = 1 LIMIT 1"
        )
        if not config or not config.get("DataRec"):
            return {}

        try:
            root = ET.fromstring(config["DataRec"])
            rp_elem = root.find("RootPerson")
            if rp_elem is None or not rp_elem.text:
                return {}
            home_person_id = int(rp_elem.text)
        except (ET.ParseError, ValueError) as e:
            print(f"get_home_person: failed to parse ConfigTable XML: {e}", file=sys.stderr)
            return {}

        if home_person_id == 0:
            return {}

        person = query_one("SELECT * FROM PersonTable WHERE PersonID = ?", (home_person_id,))
        if not person:
            return {}

        names = query(
            "SELECT Given, Surname, IsPrimary FROM NameTable WHERE OwnerID = ? ORDER BY IsPrimary DESC",
            (home_person_id,),
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
            (home_person_id,),
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
            "person_id": home_person_id,
            "sex": SEX_NAMES.get(person.get("Sex", 0), "Unknown"),
            "names": name_list,
            "events": events,
        }

    @mcp.tool()
    def search_events(
        event_type: str,
        has_place: bool | None = None,
        place_contains: str | None = None,
        person_ids: list[int] | None = None,
    ) -> list[dict]:
        """Search across all people by event type with optional place filters.

        event_type: name of the event, e.g. "Birth", "Death", "Marriage", "Census"
        has_place: True = only events with a recorded location; False = events missing a location
        place_contains: partial place name to match (case-insensitive substring)
        person_ids: if provided, restrict search to these people only

        Returns up to 200 results: [{person_id, name, event_type, event_type_name, date, place}]

        Examples:
          search_events("Death", has_place=True)            — all people with a recorded death location
          search_events("Birth", place_contains="Scotland") — all people born in Scotland
          search_events("Death", has_place=False)           — all people missing a death location
        """
        names_cache = _event_type_names()
        event_type_lower = event_type.strip().lower()
        type_id = next((tid for tid, n in names_cache.items() if n.lower() == event_type_lower), None)
        if type_id is None:
            return []

        conditions = ["e.OwnerType = 0", "e.EventType = ?"]
        params: list = [type_id]

        if has_place is True:
            conditions.append("e.PlaceID != 0")
        elif has_place is False:
            conditions.append("e.PlaceID = 0")

        if place_contains:
            conditions.append("pl.Name LIKE ?")
            params.append(f"%{place_contains}%")

        if person_ids:
            ph = ",".join("?" * len(person_ids))
            conditions.append(f"e.OwnerID IN ({ph})")
            params.extend(person_ids)

        # INNER JOIN when we need place data to exist; LEFT JOIN otherwise
        pl_join = "JOIN" if (has_place is True or place_contains) else "LEFT JOIN"
        where = " AND ".join(conditions)

        rows = query(
            f"""
            SELECT e.OwnerID AS PersonID, e.EventType, e.Date, pl.Name AS PlaceName
            FROM EventTable e
            {pl_join} PlaceTable pl ON pl.PlaceID = e.PlaceID AND e.PlaceID != 0
            WHERE {where}
            ORDER BY e.SortDate
            LIMIT 200
            """,
            tuple(params),
        )

        if not rows:
            return []

        # Batch-load names to avoid N+1 queries
        found_pids = list(dict.fromkeys(r["PersonID"] for r in rows))
        name_ph = ",".join("?" * len(found_pids))
        name_rows = query(
            f"SELECT OwnerID, Given, Surname FROM NameTable WHERE OwnerID IN ({name_ph}) AND IsPrimary = 1",
            tuple(found_pids),
        )
        person_name_map: dict[int, str] = {}
        for nr in name_rows:
            given = nr["Given"] or ""
            surname = nr["Surname"] or ""
            person_name_map[nr["OwnerID"]] = f"{given} {surname}".strip() or "Unknown"

        return [
            {
                "person_id": r["PersonID"],
                "name": person_name_map.get(r["PersonID"], "Unknown"),
                "event_type": r["EventType"],
                "event_type_name": names_cache.get(r["EventType"], f"Event {r['EventType']}"),
                "date": _format_date(r["Date"]),
                "place": r["PlaceName"] or "",
            }
            for r in rows
        ]

    @mcp.tool()
    def report_issue() -> dict:
        """Use this tool to direct the user to report a bug or unexpected behavior.
        Returns a message and URLs for the GitHub repository and issue tracker."""
        return {
            "message": "Please report bugs and issues at the link below.",
            "repo_url": "https://github.com/markjennine/Family-Tree-MCP",
            "issues_url": "https://github.com/markjennine/Family-Tree-MCP/issues/new",
        }


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
