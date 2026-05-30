"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Graph schema used in this seeder:
  Node labels:
    - MetroStation nodes carry labels :Station:Metro:MetroStation
        :Station   — allows whole-network queries across both networks
        :Metro     — allows metro-only queries
        :MetroStation — matches the teacher guide terminology for static eval
    - NationalRailStation nodes carry labels :Station:NationalRail:NationalRailStation
        same reasoning as above, scoped to the national rail network

  Relationship types:
    - METRO_LINK       — connects two adjacent metro stations
    - RAIL_LINK        — connects two adjacent national rail stations
    - INTERCHANGE_TO   — connects a metro station to its co-located national
                         rail station (and vice versa), representing the
                         physical walk between the two platforms

  Node properties stored:
    station_id  — matches the PostgreSQL primary key exactly (e.g. "MS01")
                  critical for cross-database lookups from queries.py
    name        — human-readable station name; useful in Neo4j Browser and
                  for debugging without needing to join back to PostgreSQL
    lines       — list of line IDs serving this station (e.g. ["M1", "M2"])
                  stored as a native Neo4j array so Cypher can use
                  "M1" IN s.lines for efficient filtering

  Edge properties stored:
    travel_time_min — numeric weight used by Dijkstra shortest-path queries
    line            — line ID (e.g. "M1") stored on METRO_LINK / RAIL_LINK
                      edges so that queries can filter to a single line or
                      calculate minimum-transfers routes; omitted on
                      INTERCHANGE_TO because the transfer is not line-specific

  Idempotency:
    All node and relationship creation uses MERGE, never CREATE.
    MERGE checks whether a matching node/relationship already exists before
    inserting, so re-running this script is safe and produces no duplicates.
    For relationships, the MERGE key includes the line property so that if
    two different lines happen to share the same pair of adjacent stations,
    each line gets its own distinct edge.

  Directionality:
    All relationships are stored in BOTH directions (A→B and B→A).
    Storing both directions makes Dijkstra and other path-finding queries
    simpler and avoids having to use undirected relationship patterns
    (which are slower in Neo4j) in every query.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
# Build an absolute path to the train-mock-data directory so the script can
# be run from any working directory without breaking the file loads.
# __file__ is the absolute path of this script (skeleton/seed_neo4j.py).
# ".." steps up one level to the project root, then into train-mock-data/.
_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename: str) -> list[dict]:
    """Load and parse a JSON file from the train-mock-data directory.

    Args:
        filename: The filename inside train-mock-data/ (e.g. "metro_stations.json").

    Returns:
        Parsed JSON content as a Python list of dicts.
    """
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Node creation helpers
# ---------------------------------------------------------------------------

def _create_metro_stations(session, metro_stations: list[dict]) -> None:
    """Create or update all metro station nodes in the graph.

    Each station gets the labels :Station:Metro:MetroStation so it can be
    matched by network-agnostic queries (:Station), metro-only queries
    (:Metro), and the teacher-specified label (:MetroStation).

    Uses MERGE on station_id so re-running is safe (idempotent).
    SET overwrites name and lines on every run so data stays fresh.

    Args:
        session: An active Neo4j driver session.
        metro_stations: Parsed list from metro_stations.json.
    """
    print(f"  Creating {len(metro_stations)} metro station nodes...")

    for station in metro_stations:
        # MERGE finds an existing :MetroStation node with this station_id,
        # or creates a new one if none exists.
        # We MERGE on station_id alone (the natural unique key) and then SET
        # the remaining properties, which is the recommended Neo4j pattern for
        # idempotent upserts.
        session.run(
            """
            MERGE (s:Station:Metro:MetroStation { station_id: $station_id })
            SET s.name  = $name,
                s.lines = $lines
            """,
            station_id=station["station_id"],
            name=station["name"],
            lines=station["lines"],   # stored as a native Neo4j list property
        )

    print(f"    Done — {len(metro_stations)} metro station nodes ready.")


def _create_rail_stations(session, rail_stations: list[dict]) -> None:
    """Create or update all national rail station nodes in the graph.

    Mirrors _create_metro_stations but uses the labels
    :Station:NationalRail:NationalRailStation.

    Args:
        session: An active Neo4j driver session.
        rail_stations: Parsed list from national_rail_stations.json.
    """
    print(f"  Creating {len(rail_stations)} national rail station nodes...")

    for station in rail_stations:
        session.run(
            """
            MERGE (s:Station:NationalRail:NationalRailStation { station_id: $station_id })
            SET s.name  = $name,
                s.lines = $lines
            """,
            station_id=station["station_id"],
            name=station["name"],
            lines=station["lines"],
        )

    print(f"    Done — {len(rail_stations)} national rail station nodes ready.")


# ---------------------------------------------------------------------------
# Relationship creation helpers
# ---------------------------------------------------------------------------

def _create_metro_links(session, metro_stations: list[dict], metro_fare_by_line: dict) -> None:
    """Create bidirectional METRO_LINK relationships between adjacent metro stations.

    Source of truth: the adjacent_stations list on each station entry in
    metro_stations.json. Every station lists all of its neighbours, so
    iterating the full list naturally produces both directions of every edge.

    MERGE key: (origin, destination, line).
    Using line as part of the MERGE key means that if two different metro
    lines ever connect the same pair of stations (not the case in the current
    data, but defensive practice), each line gets its own distinct edge rather
    than overwriting the other.

    Args:
        session: An active Neo4j driver session.
        metro_stations: Parsed list from metro_stations.json.
        metro_fare_by_line: Mapping of line ID → fare dict built from metro_schedules.json.
    """
    link_count = 0

    print("  Creating METRO_LINK relationships...")

    for station in metro_stations:
        for adj in station["adjacent_stations"]:
            # MATCH both endpoint nodes first — they must already exist
            # (created in _create_metro_stations above).
            # MERGE the directed edge (origin → destination) keyed on line.
            # Then SET the remaining property (travel_time_min).
            # Separating the MERGE key from SET is the correct Neo4j idiom:
            # properties in the MERGE pattern are used for matching only;
            # SET updates them after match-or-create.
            fare = metro_fare_by_line.get(adj["line"], {})
            session.run(
                """
                MATCH (a:MetroStation { station_id: $origin_id })
                MATCH (b:MetroStation { station_id: $dest_id })
                MERGE (a)-[r:METRO_LINK { line: $line }]->(b)
                SET r.travel_time_min = $travel_time_min,
                    r.per_stop_rate_usd = $per_stop_rate_usd
                """,
                origin_id=station["station_id"],
                dest_id=adj["station_id"],
                line=adj["line"],
                travel_time_min=adj["travel_time_min"],
                per_stop_rate_usd=fare.get("per_stop_rate_usd"),
            )
            link_count += 1

    # Because every station lists its neighbours and metro_stations.json is
    # symmetric, the loop above creates edges in both directions naturally.
    # (e.g. MS01 lists MS02 as a neighbour AND MS02 lists MS01 as a neighbour)
    print(f"    Done — {link_count} METRO_LINK relationships ready.")


def _create_rail_links(session, rail_stations: list[dict], rail_fare_by_line: dict) -> None:
    """Create bidirectional RAIL_LINK relationships between adjacent national rail stations.

    Identical logic to _create_metro_links but targets :NationalRailStation
    nodes and creates :RAIL_LINK relationships.

    Args:
        session: An active Neo4j driver session.
        rail_stations: Parsed list from national_rail_stations.json.
        rail_fare_by_line: Mapping of line ID → fare dict built from national_rail_schedules.json.
    """
    link_count = 0

    print("  Creating RAIL_LINK relationships...")

    for station in rail_stations:
        for adj in station["adjacent_stations"]:
            fare = rail_fare_by_line.get(adj["line"], {})
            session.run(
                """
                MATCH (a:NationalRailStation { station_id: $origin_id })
                MATCH (b:NationalRailStation { station_id: $dest_id })
                MERGE (a)-[r:RAIL_LINK { line: $line }]->(b)
                SET r.travel_time_min = $travel_time_min,
                    r.standard_fare_usd = $standard_fare_usd,
                    r.first_fare_usd = $first_fare_usd
                """,
                origin_id=station["station_id"],
                dest_id=adj["station_id"],
                line=adj["line"],
                travel_time_min=adj["travel_time_min"],
                standard_fare_usd=fare.get("standard_fare_usd"),
                first_fare_usd=fare.get("first_fare_usd"),
            )
            link_count += 1

    print(f"    Done — {link_count} RAIL_LINK relationships ready.")


def _create_interchange_links(session, metro_stations: list[dict]) -> None:
    """Create bidirectional INTERCHANGE_TO relationships at cross-network stations.

    An interchange exists wherever a metro station has
    is_interchange_national_rail = true, which means a passenger can walk
    between the metro platform and the co-located national rail platform.

    INTERCHANGE_TO properties:
      travel_time_min = 5  — fixed assumed walking time between platforms
                             (no source data provides a measured value)

    No 'line' property is stored on INTERCHANGE_TO because the transfer is a
    physical walk, not a service on any particular line.

    Both directions are created (metro→rail AND rail→metro) so that
    query_interchange_path in queries.py can traverse the network in either
    direction without needing undirected relationship patterns.

    Args:
        session: An active Neo4j driver session.
        metro_stations: Parsed list from metro_stations.json (interchange
            flags and paired rail station IDs come from this file).
    """
    interchange_count = 0

    print("  Creating INTERCHANGE_TO relationships...")

    for station in metro_stations:
        # Only process stations that are flagged as having a rail interchange
        if not station.get("is_interchange_national_rail"):
            continue

        rail_station_id = station.get("interchange_national_rail_station_id")
        if not rail_station_id:
            # Defensive guard: flag is true but no target ID — skip silently
            print(f"    WARNING: {station['station_id']} flagged as interchange "
                  f"but interchange_national_rail_station_id is missing — skipping.")
            continue

        # Metro → National Rail direction
        # MERGE ensures re-seeding does not create duplicate interchange edges.
        session.run(
            """
            MATCH (metro:MetroStation { station_id: $metro_id })
            MATCH (rail:NationalRailStation { station_id: $rail_id })
            MERGE (metro)-[r:INTERCHANGE_TO]->(rail)
            SET r.travel_time_min = $travel_time_min
            """,
            metro_id=station["station_id"],
            rail_id=rail_station_id,
            travel_time_min=5,
        )

        # National Rail → Metro direction (reverse of the above)
        # Storing both directions allows Dijkstra to traverse the interchange
        # regardless of which network the journey starts on.
        session.run(
            """
            MATCH (rail:NationalRailStation { station_id: $rail_id })
            MATCH (metro:MetroStation { station_id: $metro_id })
            MERGE (rail)-[r:INTERCHANGE_TO]->(metro)
            SET r.travel_time_min = $travel_time_min
            """,
            rail_id=rail_station_id,
            metro_id=station["station_id"],
            travel_time_min=5,
        )

        interchange_count += 1
        print(f"    Interchange: {station['station_id']} ({station['name']}) "
              f"↔ {rail_station_id}")

    print(f"    Done — {interchange_count} interchange station(s), "
          f"{interchange_count * 2} INTERCHANGE_TO relationships ready.")


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------

def seed() -> None:
    """Seed the Neo4j graph database with all station nodes and relationships.

    Execution order matters:
      1. Load JSON source data
      2. Clear existing graph (DETACH DELETE removes nodes + their relationships)
      3. Create all metro station nodes
      4. Create all national rail station nodes
      5. Create METRO_LINK edges  (nodes must exist first)
      6. Create RAIL_LINK edges   (nodes must exist first)
      7. Create INTERCHANGE_TO edges (both node types must exist first)

    Steps 5–7 use MATCH to find nodes, so they will silently produce no
    relationships if the corresponding nodes were not created in steps 3–4.
    """
    print("\nLoading source data from train-mock-data/...")
    metro_stations   = _load("metro_stations.json")
    rail_stations    = _load("national_rail_stations.json")
    metro_schedules  = _load("metro_schedules.json")
    rail_schedules   = _load("national_rail_schedules.json")
    print(f"  Loaded {len(metro_stations)} metro stations, "
          f"{len(rail_stations)} national rail stations.")

    metro_fare_by_line: dict = {}
    for sch in metro_schedules:
        line = sch["line"]
        if line not in metro_fare_by_line:
            metro_fare_by_line[line] = {
                "base_fare_usd": sch["base_fare_usd"],
                "per_stop_rate_usd": sch["per_stop_rate_usd"],
            }

    rail_fare_by_line: dict = {}
    for sch in rail_schedules:
        line = sch["line"]
        if line not in rail_fare_by_line:
            rail_fare_by_line[line] = {
                "standard_fare_usd": sch["fare_classes"]["standard"]["base_fare_usd"],
                "first_fare_usd": sch["fare_classes"]["first"]["base_fare_usd"],
            }

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:

        # ------------------------------------------------------------------
        # Step 1: Clear existing data
        # ------------------------------------------------------------------
        # DETACH DELETE removes all nodes AND their relationships in one pass.
        # Without DETACH, Neo4j raises an error if a node still has
        # relationships attached when you try to delete it.
        print("\nClearing existing graph data...")
        session.run("MATCH (n) DETACH DELETE n")
        print("  Done — graph is empty.")

        # ------------------------------------------------------------------
        # Step 2: Create nodes
        # ------------------------------------------------------------------
        # Nodes must be created before any relationship creation step,
        # because the MATCH clauses in the relationship queries rely on the
        # nodes already existing.
        print("\nCreating station nodes...")
        _create_metro_stations(session, metro_stations)
        _create_rail_stations(session, rail_stations)

        # ------------------------------------------------------------------
        # Step 3: Create intra-network relationships
        # ------------------------------------------------------------------
        print("\nCreating intra-network relationships...")
        _create_metro_links(session, metro_stations, metro_fare_by_line)
        _create_rail_links(session, rail_stations, rail_fare_by_line)

        # ------------------------------------------------------------------
        # Step 4: Create cross-network interchange relationships
        # ------------------------------------------------------------------
        print("\nCreating cross-network interchange relationships...")
        _create_interchange_links(session, metro_stations)

    driver.close()

    print("\n" + "=" * 60)
    print("Neo4j graph seeded successfully.")
    print("Open http://localhost:7475 to explore the graph.")
    print("=" * 60)


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()
