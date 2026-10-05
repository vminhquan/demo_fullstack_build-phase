"""Minimal stand-in for the CARLA Python API (tests only)."""
import time


class Location:
    def __init__(s, x, y, z): s.x, s.y, s.z = x, y, z
class Rotation:
    def __init__(s, yaw): s.yaw = yaw
class Transform:
    def __init__(s, x, y, yaw): s.location, s.rotation = Location(x, y, 0.0), Rotation(yaw)
class LaneType:
    Driving = "Driving"
class Waypoint:
    def __init__(s, i):
        s.transform = Transform(i * 2.0, 0.0, 0.0); s.road_id = 1 + i // 50; s.section_id = 0; s.lane_id = -1
        s.s = (i % 50) * 2.0; s.lane_width = 3.5; s.is_junction = i % 25 == 0; s.lane_type = "LaneType.Driving"
class Map:
    def __init__(s, name, n): s.name, s._n = f"Carla/Maps/{name}", n
    def to_opendrive(s): return f"<OpenDRIVE name='{s.name}'/>"
    def get_spawn_points(s): return [Transform(i * 10.0, 5.0, 90.0) for i in range(max(1, s._n // 20))]
    def generate_waypoints(s, d): return [Waypoint(i) for i in range(s._n)]
class Attr:
    def __init__(s, v): s.recommended_values, s._v = [], v
    def as_str(s): return s._v
class BP:
    def __init__(s, id, attrs): s.id, s._a = id, attrs
    def has_attribute(s, k): return k in s._a
    def get_attribute(s, k): return Attr(s._a[k])
class World:
    def __init__(s, name): s._map = Map(name, {"Town01": 120, "Town03": 300, "Town10HD_Opt": 200}[name])
    def get_map(s): return s._map
    def get_blueprint_library(s):
        return [BP("vehicle.tesla.model3", {"base_type": "car", "number_of_wheels": "4"}), BP("vehicle.yamaha.yzf", {"number_of_wheels": "2"}),
                BP("walker.pedestrian.0001", {})]
class WeatherParameters:
    pass
WeatherParameters.ClearNoon = WeatherParameters(); WeatherParameters.WetNight = WeatherParameters()
LOADS = []
class Client:
    def __init__(s, host, port): s._world = World("Town10HD_Opt")
    def set_timeout(s, t): pass
    def get_server_version(s): return "0.9.16"
    def get_world(s): return s._world
    def get_available_maps(s): return ["/Game/Carla/Maps/Town01", "/Game/Carla/Maps/Town03", "/Game/Carla/Maps/Town10HD_Opt"]
    def load_world(s, name):
        time.sleep(0.3); LOADS.append(name); s._world = World(name); return s._world
