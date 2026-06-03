"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.

GRAPH ROLE:
  - Model the dual transit network (city metro M1–M4 + national rail NR1–NR2)
  - Find fastest routes (Dijkstra by travel_time_min via APOC)
  - Find cheapest routes (Dijkstra by fare via APOC)
  - Find alternative routes avoiding a given station
  - Find cross-network interchange paths (metro → rail or rail → metro)
  - Show delay ripple: which stations are affected within N hops

Graph schema (created by skeleton/seed_neo4j.py):
  Node labels:
    :Station:Metro:MetroStation        — metro stations  (MS01–MS20)
    :Station:NationalRail:NationalRailStation — rail stations (NR01–NR10)

  Relationship types / key properties:
    METRO_LINK        travel_time_min, line, base_fare_usd, per_stop_rate_usd
    RAIL_LINK         travel_time_min, line, normal_standard_fare_usd,
                      normal_standard_per_stop_rate_usd, normal_first_fare_usd,
                      normal_first_per_stop_rate_usd, (express variants too)
    INTERCHANGE_TO    travel_time_min = 5  (fixed platform-walk time)
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]


# ── Private helpers ────────────────────────────────────────────────────────────

def _rel_types(origin_id: str, dest_id: str, network: str) -> str:
    """
    Return the APOC-compatible relationship-type string for the requested network.

    "auto" infers the network from station ID prefixes (MS = metro, NR = rail).
    Mixed IDs (one MS, one NR) automatically include INTERCHANGE_TO so the path
    can cross network boundaries.
    """
    if network == "metro":
        return "METRO_LINK"
    if network == "rail":
        return "RAIL_LINK"
    # network == "auto" — infer from IDs
    if origin_id.startswith("MS") and dest_id.startswith("MS"):
        return "METRO_LINK"
    if origin_id.startswith("NR") and dest_id.startswith("NR"):
        return "RAIL_LINK"
    # cross-network or explicit "cross"
    return "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"


def _extract_path(path_obj) -> tuple[list[dict], list[dict]]:
    """
    Parse a Neo4j Path object into two parallel lists:
      stations — one dict per node  (station_id, name, lines)
      legs     — one dict per edge  (from, to, line, travel_time_min, rel_type)

    The two lists satisfy: len(legs) == len(stations) - 1
    """
    ns = list(path_obj.nodes)
    rs = list(path_obj.relationships)

    stations = [
        {
            "station_id": n["station_id"],
            "name": n["name"],
            "lines": list(n.get("lines") or []),
        }
        for n in ns
    ]

    legs = [
        {
            "from": ns[i]["station_id"],
            "to": ns[i + 1]["station_id"],
            # INTERCHANGE_TO has no 'line' property — label it "walk"
            "line": rs[i].get("line") or "walk",
            "travel_time_min": rs[i].get("travel_time_min") or 0,
            "rel_type": rs[i].type,
        }
        for i in range(len(rs))
    ]

    return stations, legs


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations, minimising total travel time.

    Uses apoc.algo.dijkstra which runs the classic Dijkstra algorithm on the
    graph, treating travel_time_min as the edge weight.  Both directions of
    every edge are stored in Neo4j, so direction is not a constraint here.

    Args:
        origin_id:       e.g. "MS01" or "NR01"
        destination_id:  e.g. "MS09" or "NR05"
        network:         "metro", "rail", or "auto" (inferred from IDs)

    Returns:
        dict with keys:
          found           bool — False when no path exists
          origin_id       str
          destination_id  str
          total_time_min  float | None
          path            list[dict]  — ordered station dicts
          legs            list[dict]  — one dict per edge traversed
    """
    rel_str = _rel_types(origin_id, destination_id, network)

    with _driver() as driver:
        with driver.session() as session:
            rec = session.run(
                """
                MATCH (origin:Station {station_id: $origin_id})
                MATCH (dest:Station   {station_id: $dest_id})
                CALL apoc.algo.dijkstra(origin, dest, $rel_types, 'travel_time_min')
                YIELD path, weight
                RETURN path, weight
                """,
                origin_id=origin_id,
                dest_id=destination_id,
                rel_types=rel_str,
            ).single()

    if not rec:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_time_min": None,
            "path": [],
            "legs": [],
        }

    stations, legs = _extract_path(rec["path"])
    return {
        "found": True,
        "origin_id": origin_id,
        "destination_id": destination_id,
        "total_time_min": rec["weight"],
        "path": stations,
        "legs": legs,
    }


# ── CHEAPEST ROUTE (pure Cypher, fare computed per edge) ─────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising estimated total fare.

    Fare model (per edge):
      METRO_LINK  →  base_fare_usd + per_stop_rate_usd
      RAIL_LINK   →  normal_{fare_class}_fare_usd + normal_{fare_class}_per_stop_rate_usd
      INTERCHANGE_TO → 0  (walking between platforms is free)

    This treats each link as one boarding unit.  APOC dijkstra only accepts a
    single property name as weight, so we use a plain Cypher variable-length
    path match instead and compute the cost with `reduce()`.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        network:         "metro", "rail", or "auto"
        fare_class:      "standard" or "first" (national rail only)

    Returns:
        dict with found, total_fare_usd, fare_class, path, legs
    """
    # We use a plain variable-length path + reduce() instead of apoc.algo.dijkstra
    # because APOC dijkstra only accepts a single property name as the edge weight.
    # Fare depends on both relationship type AND fare_class, so we must compute it
    # inline in Cypher using CASE expressions with $fare_class as a parameter.
    rel_str = _rel_types(origin_id, destination_id, network)
    with _driver() as driver:
        with driver.session() as session:
            rec = session.run(
                f"""
                MATCH (origin:Station {{station_id: $origin_id}})
                MATCH (dest:Station   {{station_id: $dest_id}})
                MATCH path = (origin)-[:{rel_str}*1..15]->(dest)
                // Reject paths that visit the same node twice (no cycles)
                WHERE all(n IN nodes(path) WHERE single(x IN nodes(path) WHERE x = n))
                WITH path,
                     reduce(fare = 0.0, r IN relationships(path) |
                       fare + CASE type(r)
                         WHEN 'METRO_LINK' THEN
                           coalesce(r.base_fare_usd, 0.0) + coalesce(r.per_stop_rate_usd, 0.0)
                         WHEN 'RAIL_LINK' THEN
                           // fare_class parameter selects between standard/first columns
                           CASE $fare_class
                             WHEN 'first' THEN
                               coalesce(r.normal_first_fare_usd, 0.0)
                               + coalesce(r.normal_first_per_stop_rate_usd, 0.0)
                             ELSE
                               coalesce(r.normal_standard_fare_usd, 0.0)
                               + coalesce(r.normal_standard_per_stop_rate_usd, 0.0)
                           END
                         ELSE 0.0  // INTERCHANGE_TO: platform walk has no fare
                       END
                     ) AS total_fare
                RETURN path, total_fare
                ORDER BY total_fare
                LIMIT 1
                """,
                origin_id=origin_id,
                dest_id=destination_id,
                fare_class=fare_class,
            ).single()

    if not rec:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_fare_usd": None,
            "fare_class": fare_class,
            "path": [],
            "legs": [],
        }

    stations, legs = _extract_path(rec["path"])
    return {
        "found": True,
        "origin_id": origin_id,
        "destination_id": destination_id,
        "total_fare_usd": round(rec["total_fare"], 2),
        "fare_class": fare_class,
        "path": stations,
        "legs": legs,
    }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[dict]:
    """
    Find paths between two stations that avoid a specific intermediate station.

    Useful for routing around a delayed or closed station.  The query finds all
    simple paths (no repeated nodes) up to depth 15 that do not pass through
    avoid_station_id, then returns the fastest ones.

    Note: the avoid constraint only applies to intermediate nodes — if
    avoid_station_id happens to equal origin or destination the query still
    returns paths (though that would be a degenerate caller mistake).

    Args:
        origin_id:         e.g. "NR01"
        destination_id:    e.g. "NR05"
        avoid_station_id:  e.g. "NR03"
        network:           "metro", "rail", or "auto"
        max_routes:        max number of alternatives to return (default 3)

    Returns:
        List of route dicts, each with keys:
          total_time_min   int
          legs             list[dict]
          path             list[dict]
    """
    rel_str = _rel_types(origin_id, destination_id, network)
    with _driver() as driver:
        with driver.session() as session:
            records = list(session.run(
                f"""
                MATCH (origin:Station {{station_id: $origin_id}})
                MATCH (dest:Station   {{station_id: $dest_id}})
                MATCH path = (origin)-[:{rel_str}*1..15]->(dest)
                WHERE
                  // Allow origin/dest to equal avoid_id (degenerate input) but block
                  // any intermediate node that matches, since those are the "closed" stops.
                  NOT any(n IN nodes(path)
                          WHERE n.station_id = $avoid_id
                            AND n <> origin
                            AND n <> dest)
                  // Reject cycles so each path is a simple path (no station visited twice)
                  AND all(n IN nodes(path) WHERE single(x IN nodes(path) WHERE x = n))
                WITH path,
                     reduce(t = 0, r IN relationships(path) |
                       t + coalesce(r.travel_time_min, 0)) AS total_time
                RETURN path, total_time
                ORDER BY total_time
                LIMIT $max_routes
                """,
                origin_id=origin_id,
                dest_id=destination_id,
                avoid_id=avoid_station_id,
                max_routes=max_routes,
            ))

    routes = []
    for rec in records:
        stations, legs = _extract_path(rec["path"])
        routes.append({
            "total_time_min": rec["total_time"],
            "path": stations,
            "legs": legs,
        })
    return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path crossing the metro / national-rail network boundary.

    Uses the same APOC Dijkstra as query_shortest_route but always allows all
    three relationship types (METRO_LINK, RAIL_LINK, INTERCHANGE_TO) regardless
    of which network the origin/destination belong to.

    Interchange points are edges where the relationship type is INTERCHANGE_TO —
    these represent the physical platform-to-platform walk (5 min fixed cost).

    Args:
        origin_id:       e.g. "MS03" or "NR05"
        destination_id:  e.g. "NR05" or "MS09"

    Returns:
        dict with found, total_time_min, stations, legs, interchange_points
        interchange_points is a list of {from_station, to_station} for each
        INTERCHANGE_TO edge in the path.
    """
    with _driver() as driver:
        with driver.session() as session:
            rec = session.run(
                """
                MATCH (origin:Station {station_id: $origin_id})
                MATCH (dest:Station   {station_id: $dest_id})
                CALL apoc.algo.dijkstra(
                    origin, dest,
                    'METRO_LINK|RAIL_LINK|INTERCHANGE_TO',
                    'travel_time_min'
                )
                YIELD path, weight
                RETURN path, weight
                """,
                origin_id=origin_id,
                dest_id=destination_id,
            ).single()

    if not rec:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_time_min": None,
            "path": [],
            "legs": [],
            "interchange_points": [],
        }

    stations, legs = _extract_path(rec["path"])

    # Collect network-crossing edges (INTERCHANGE_TO)
    interchange_points = [
        {"from_station": leg["from"], "to_station": leg["to"]}
        for leg in legs
        if leg["rel_type"] == "INTERCHANGE_TO"
    ]

    return {
        "found": True,
        "origin_id": origin_id,
        "destination_id": destination_id,
        "total_time_min": rec["weight"],
        "path": stations,
        "legs": legs,
        "interchange_points": interchange_points,
    }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a disrupted station.

    A "hop" is one METRO_LINK, RAIL_LINK, or INTERCHANGE_TO traversal.
    The result includes stations on both networks if an interchange is within
    range.

    Cypher variable-length path ranges do not support query parameters, so the
    hops value is interpolated as a plain integer (safe — it is never user text).

    Args:
        delayed_station_id: e.g. "NR03" or "MS01"
        hops:               how many connections out to search (default 2)

    Returns:
        List of dicts: {station_id, name, hops_away, lines_affected}
        sorted by hops_away ascending.
        hops=0 returns only the source station itself (hops_away=0).
    """
    safe_hops = int(hops)

    # hops=0 means "no expansion" — return the disrupted station itself.
    # The *1..N path pattern cannot express depth 0, so we handle it separately
    # rather than clamping to 1 (which would incorrectly return 1-hop neighbours).
    if safe_hops <= 0:
        with _driver() as driver:
            with driver.session() as session:
                rec = session.run(
                    """
                    MATCH (s:Station {station_id: $sid})
                    RETURN s.station_id AS station_id,
                           s.name       AS name,
                           s.lines      AS lines
                    """,
                    sid=delayed_station_id,
                ).single()
        if not rec:
            return []
        return [{
            "station_id": rec["station_id"],
            "name": rec["name"],
            "hops_away": 0,
            "lines_affected": list(rec["lines"] or []),
        }]

    # Cypher variable-length path ranges (*1..N) do not accept query parameters,
    # so safe_hops (a validated Python int) is interpolated directly into the query.
    cypher = f"""
        MATCH (source:Station {{station_id: $station_id}})
        MATCH path = (source)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..{safe_hops}]->
                     (affected:Station)
        WHERE affected <> source
        WITH affected, min(length(path)) AS hops_away
        RETURN DISTINCT
               affected.station_id AS station_id,
               affected.name       AS name,
               hops_away,
               affected.lines      AS lines_affected
        ORDER BY hops_away, affected.station_id
    """

    with _driver() as driver:
        with driver.session() as session:
            records = list(session.run(cypher, station_id=delayed_station_id))

    return [
        {
            "station_id": r["station_id"],
            "name": r["name"],
            "hops_away": r["hops_away"],
            "lines_affected": list(r["lines_affected"] or []),
        }
        for r in records
    ]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct neighbours of a station (one-hop traversal).

    Returns connections from all three relationship types so that metro
    stations also show their rail interchange neighbour (if any).

    Args:
        station_id: e.g. "MS01" or "NR01"

    Returns:
        List of dicts: {station_id, name, lines, rel_type, line, travel_time_min}
        sorted by travel_time_min ascending.
    """
    with _driver() as driver:
        with driver.session() as session:
            records = list(session.run(
                """
                MATCH (s:Station {station_id: $station_id})-[r]->(neighbor:Station)
                RETURN neighbor.station_id  AS station_id,
                       neighbor.name        AS name,
                       neighbor.lines       AS lines,
                       type(r)              AS rel_type,
                       r.line               AS line,
                       r.travel_time_min    AS travel_time_min
                ORDER BY coalesce(r.travel_time_min, 9999)
                """,
                station_id=station_id,
            ))

    return [
        {
            "station_id": r["station_id"],
            "name": r["name"],
            "lines": list(r["lines"] or []),
            "rel_type": r["rel_type"],
            "line": r["line"],
            "travel_time_min": r["travel_time_min"],
        }
        for r in records
    ]
