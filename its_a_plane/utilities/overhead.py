import os
import json
import math
import socket
import time as time_mod
from time import sleep
from threading import Thread, Lock
from datetime import datetime
from typing import Optional, Tuple

import requests
import airportsdata
from opensky_api import OpenSkyApi, TokenManager
from requests.exceptions import ConnectionError
from urllib3.exceptions import NewConnectionError, MaxRetryError

from its_a_plane.config import (
    DISTANCE_UNITS,
    CLOCK_FORMAT,
    MAX_FARTHEST,
    MAX_CLOSEST,
)

from its_a_plane.setup import email_alerts
from its_a_plane.web import map_generator, upload_helper

# Optional config values

try:
    from its_a_plane.config import MIN_ALTITUDE
except (ImportError, ModuleNotFoundError, NameError):
    MIN_ALTITUDE = 0


try:
    from its_a_plane.config import ZONE_HOME, LOCATION_HOME
    ZONE_DEFAULT = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME
except (ImportError, ModuleNotFoundError, NameError):
    ZONE_DEFAULT = {"tl_y": 41.904318, "tl_x": -87.647367,
                    "br_y": 41.851654, "br_x": -87.573027}
    LOCATION_DEFAULT = [41.882724, -87.623350]

# Constants

RETRIES = 3
RATE_LIMIT_DELAY = 1
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 100000  # feet
EARTH_RADIUS_M = 3958.8
BLANK_FIELDS = ["", "N/A", "NONE"]
ROUTE_LOOK_BACK = 12 * 3600  # seconds

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CREDENTIALS_FILE = os.path.join(os.path.dirname(BASE_DIR), "credentials.json")

_airports = airportsdata.load("ICAO")
LOG_FILE = os.path.join(BASE_DIR, "close.txt")
LOG_FILE_FARTHEST = os.path.join(BASE_DIR, "farthest.txt")

# Utility Functions

def safe_load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def safe_write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def ordinal(n: int):
    return f"{n}{'tsnrhtdd'[(n//10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"


def haversine(lat1, lon1, lat2, lon2):
    """Internal helper for distance."""
    lat1, lon1 = map(math.radians, (lat1, lon1))
    lat2, lon2 = map(math.radians, (lat2, lon2))

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2)**2 +
        math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    miles = EARTH_RADIUS_M * c

    return miles * 1.609 if DISTANCE_UNITS == "metric" else miles


def degrees_to_cardinal(deg):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) / 45)
    return dirs[idx % 8]


def plane_bearing(flight, home=LOCATION_DEFAULT):
    lat1, lon1 = map(math.radians, home)
    lat2, lon2 = map(math.radians, (flight.latitude, flight.longitude))

    b = math.atan2(
        math.sin(lon2 - lon1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    )
    return (math.degrees(b) + 360) % 360

def lookup_aircraft(icao24):
    try:
        r = requests.get(f"https://hexdb.io/api/v1/aircraft/{icao24}", timeout=5)
        if r.status_code == 200:
            d = r.json()
            return d.get("ICAOTypeCode", ""), d.get("RegisteredOwners", ""), d.get("OperatorFlagCode", "")
    except Exception:
        pass
    return "", "", ""


def airport_coords(icao_code):
    if not icao_code:
        return "", None, None
    ap = _airports.get(icao_code.upper())
    if ap:
        return ap.get("iata") or icao_code, ap.get("lat"), ap.get("lon")
    return icao_code, None, None


# Distance wrappers

def distance_from_flight_to_home(flight):
    return haversine(
        flight.latitude, flight.longitude,
        LOCATION_DEFAULT[0], LOCATION_DEFAULT[1],
    )


def distance_to_point(flight, lat, lon):
    return haversine(flight.latitude, flight.longitude, lat, lon)

# Logging Closest Flights

def log_flight_data(entry: dict):
    """Track top-N closest flights and email only when NEW enters top-N."""
    try:
        entry["timestamp"] = email_alerts.get_timestamp()
        lst = safe_load_json(LOG_FILE)

        callsigns = {f.get("callsign"): f for f in lst}
        new_call = entry.get("callsign")
        new_dist = entry.get("distance", float("inf"))
        notify = False

        # Existing ? update if better
        if new_call in callsigns:
            idx = next(i for i, f in enumerate(lst) if f.get("callsign") == new_call)
            if new_dist < lst[idx].get("distance", float("inf")):
                lst[idx] = entry
            else:
                return
        else:
            lst.append(entry)

        # Sorting by closest
        lst.sort(key=lambda x: x.get("distance", float("inf")))
        top_n = lst[:MAX_CLOSEST]

        if new_call not in [f["callsign"] for f in top_n]:
            return

        rank = next(i + 1 for i, f in enumerate(top_n) if f["callsign"] == new_call)

        if new_call not in callsigns:
            notify = True

        safe_write_json(LOG_FILE, top_n)

        if notify:
            html = map_generator.generate_closest_map(top_n, filename="closest.html")
            url = upload_helper.upload_map_to_server(html)

            subject = f"New {ordinal(rank)} Closest Flight - {entry.get('callsign','Unknown')}"
            email_alerts.send_flight_summary(subject, entry, map_url=url)

    except Exception as e:
        print("Failed to log closest flight:", e)

# Logging Farthest Flights

def log_farthest_flight(entry: dict):
    """Track farthest airports (origin or destination)."""
    try:
        d_o = entry.get("distance_origin", -1)
        d_d = entry.get("distance_destination", -1)

        if d_o < 0 and d_d < 0:
            return

        reason = "origin" if d_o >= d_d else "destination"
        far = d_o if reason == "origin" else d_d
        airport = entry.get(reason)

        if not airport:
            return

        entry["timestamp"] = email_alerts.get_timestamp()
        entry["reason"] = reason
        entry["farthest_value"] = far
        entry["_airport"] = airport

        lst = safe_load_json(LOG_FILE_FARTHEST)
        airport_map = {f["_airport"]: f for f in lst}

        existing = airport_map.get(airport)
        notify = False
        updated = False

        if existing:
            # Only update if "distance" improved
            if entry["distance"] < existing.get("distance", 9e9):
                lst = [entry if f["_airport"] == airport else f for f in lst]
                updated = True
            else:
                return
        else:
            # New airport entering top-N
            if len(lst) >= MAX_FARTHEST:
                if far <= min(f["farthest_value"] for f in lst):
                    return
            lst.append(entry)
            notify = True

        lst.sort(key=lambda x: x["farthest_value"], reverse=True)
        lst = lst[:MAX_FARTHEST]
        safe_write_json(LOG_FILE_FARTHEST, lst)

        # --- ALWAYS generate local map for notify OR updated ---
        if notify or updated:
            html = map_generator.generate_farthest_map(lst, filename="farthest.html")

        # --- ONLY upload + email if this is a NEW airport ---
        if notify:
            url = upload_helper.upload_map_to_server(html)

            rank = next(i for i, f in enumerate(lst) if f["_airport"] == airport) + 1
            cs = entry.get("callsign", "UNKNOWN")

            if rank == 1:
                subject = f"New Farthest Flight ({reason}) - {cs}"
            else:
                subject = f"{ordinal(rank)}-Farthest Flight ({reason}) - {cs}"

            email_alerts.send_flight_summary(subject, entry, reason, map_url=url)

    except Exception as e:
        print("Failed to log farthest flight:", e)


# Overhead Class

class Overhead:
    def __init__(self):
        tm = TokenManager.from_json_file(CREDENTIALS_FILE)
        self._api = OpenSkyApi(token_manager=tm)
        self._lock = Lock()
        self._data = []
        self._new_data = False
        self._processing = False

    def grab_data(self):
        Thread(target=self._grab).start()

    def _grab(self):
        with self._lock:
            self._new_data = False
            self._processing = True

        data = []

        try:
            bbox = (
                ZONE_DEFAULT["br_y"],  # min_lat (south)
                ZONE_DEFAULT["tl_y"],  # max_lat (north)
                ZONE_DEFAULT["tl_x"],  # min_lon (west)
                ZONE_DEFAULT["br_x"],  # max_lon (east)
            )
            states = self._api.get_states(bbox=bbox)

            if states and states.states:
                # Altitude filter — OpenSky uses metres, config is in feet
                min_alt_m = MIN_ALTITUDE * 0.3048
                max_alt_m = MAX_ALTITUDE * 0.3048
                flights = [
                    s for s in states.states
                    if not s.on_ground and min_alt_m < (s.baro_altitude or 0) < max_alt_m
                ]

                flights.sort(key=lambda s: distance_from_flight_to_home(s))
                flights = flights[:MAX_FLIGHT_LOOKUP]

                now = int(time_mod.time())
                look_back = now - ROUTE_LOOK_BACK

                for s in flights:
                    retries = RETRIES
                    while retries:
                        sleep(RATE_LIMIT_DELAY)
                        try:
                            callsign = (s.callsign or "").strip()

                            plane, airline, owner_icao = lookup_aircraft(s.icao24)
                            if not owner_icao:
                                owner_icao = callsign[:3] if len(callsign) >= 3 and callsign[:3].isalpha() else ""

                            origin = destination = ""
                            origin_lat = origin_lon = dest_lat = dest_lon = None
                            dep_time = arr_time = None

                            route_flights = self._api.get_flights_by_aircraft(s.icao24, look_back, now)
                            if route_flights:
                                recent = max(route_flights, key=lambda f: f.firstSeen)
                                origin, origin_lat, origin_lon = airport_coords(recent.estDepartureAirport)
                                destination, dest_lat, dest_lon = airport_coords(recent.estArrivalAirport)
                                dep_time = recent.firstSeen
                                arr_time = recent.lastSeen

                            dist_o = distance_to_point(s, origin_lat, origin_lon) if origin_lat else 0
                            dist_d = distance_to_point(s, dest_lat, dest_lon) if dest_lat else 0

                            entry = {
                                "airline": airline,
                                "plane": plane,
                                "origin": origin,
                                "origin_latitude": origin_lat,
                                "origin_longitude": origin_lon,
                                "destination": destination,
                                "destination_latitude": dest_lat,
                                "destination_longitude": dest_lon,
                                "plane_latitude": s.latitude,
                                "plane_longitude": s.longitude,

                                "owner_iata": "",
                                "owner_icao": owner_icao,

                                "time_scheduled_departure": None,
                                "time_scheduled_arrival": None,
                                "time_real_departure": dep_time,
                                "time_estimated_arrival": arr_time,

                                "vertical_speed": s.vertical_rate,
                                "callsign": callsign,

                                "distance_origin": dist_o,
                                "distance_destination": dist_d,
                                "distance": distance_from_flight_to_home(s),
                                "direction": degrees_to_cardinal(plane_bearing(s)),
                            }

                            data.append(entry)
                            log_flight_data(entry)
                            log_farthest_flight(entry)

                            break

                        except Exception:
                            retries -= 1

            with self._lock:
                self._new_data = True
                self._processing = False
                self._data = data

        except (ConnectionError, NewConnectionError, MaxRetryError):
            with self._lock:
                self._new_data = False
                self._processing = False

    # Properties
    @property
    def new_data(self):
        with self._lock:
            return self._new_data

    @property
    def processing(self):
        with self._lock:
            return self._processing

    @property
    def data(self):
        with self._lock:
            self._new_data = False
            return self._data

    @property
    def data_is_empty(self):
        return len(self._data) == 0

# Main

if __name__ == "__main__":
    o = Overhead()
    o.grab_data()

    while not o.new_data:
        print("processing...")
        sleep(1)

    print(o.data)

