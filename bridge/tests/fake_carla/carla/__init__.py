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
class LaneMarking:
    def __init__(s, kind): s.type, s.color, s.lane_change = f"LaneMarkingType.{kind}", "LaneMarkingColor.White", "LaneChange.Both"
class Waypoint:
    def __init__(s, i):
        s.transform = Transform(i * 2.0, 0.0, 0.0); s.road_id = 1 + i // 50; s.section_id = 0; s.lane_id = -1
        s.s = (i % 50) * 2.0; s.lane_width = 3.5; s.is_junction = i % 25 == 0; s.lane_type = "LaneType.Driving"
        s.junction_id = 900 + i // 25 if s.is_junction else -1; s.lane_change = "LaneChange.Left"
        s.left_lane_marking, s.right_lane_marking = LaneMarking("Broken"), LaneMarking("Solid")
    def get_left_lane(s): return None
    def get_right_lane(s): raise RuntimeError("no lane")
    def get_junction(s): return Junction(s.junction_id)
class BoundingBox:
    def __init__(s, x, y, z): s.location, s.extent = Location(x, y, 0.0), Location(2.0, 1.0, 0.8)
class Junction:
    def __init__(s, id): s.id, s.bounding_box = id, BoundingBox(10.0, 0.0, 0.0)
    def get_waypoints(s, lane_type): return [(Waypoint(1), Waypoint(2))]
class Landmark:
    def __init__(s): s.id, s.type, s.sub_type, s.name, s.road_id, s.s = "963", "1000001", "-1", "Signal_3Light_Post01", 1, 3.5
    value, unit, text, transform = -1.0, "", "", Transform(5.0, 1.0, 450.0)
    def get_lane_validities(s): return [(-1, -1)]
OPENDRIVE = """<OpenDRIVE name='{name}'>
<road id='1'><type s='0' type='town'><speed max='40' unit='mph'/></type></road>
<road id='2'><type s='0' type='town'><speed max='no limit' unit='km/h'/></type></road>
<junction id='900'><connection id='0' incomingRoad='1' connectingRoad='2' contactPoint='start'><laneLink from='-1' to='-1'/></connection></junction>
</OpenDRIVE>"""
class Map:
    def __init__(s, name, n): s.name, s._n = f"Carla/Maps/{name}", n
    def to_opendrive(s): return OPENDRIVE.format(name=s.name)
    def get_spawn_points(s): return [Transform(i * 10.0, 5.0, 90.0) for i in range(max(1, s._n // 20))]
    def generate_waypoints(s, d): return [Waypoint(i) for i in range(s._n)]
    def get_all_landmarks(s): return [Landmark()]
    def get_crosswalks(s): return [Location(0, 0, 0), Location(4, 0, 0), Location(4, 3, 0), Location(0, 3, 0), Location(0, 0, 0)]
    def get_topology(s): return [(Waypoint(0), Waypoint(60))]
    def get_waypoint(s, location, project_to_road=True, lane_type=None): return Waypoint(0)
class TrafficLight:
    id = 42
    def get_transform(s): return Transform(5.0, 1.0, 0.0)
    def get_opendrive_id(s): return "963"
    def get_pole_index(s): return 0
    def get_group_traffic_lights(s): return [s]
    def get_stop_waypoints(s): return [Waypoint(3)]
    def get_affected_lane_waypoints(s): return [Waypoint(3), Waypoint(4)]
    def get_green_time(s): return 10.0
    def get_yellow_time(s): return 3.0
    def get_red_time(s): return 2.0
class Actors(list):
    def filter(s, pattern): return Actors(item for item in s if pattern == "traffic.traffic_light")
class Vehicle:
    def __init__(s, bp): s.bounding_box = BoundingBox(0, 0, 0); s.bp = bp
    def destroy(s): DESTROYED.append(s.bp.id)
DESTROYED = []
class ActorAttributeType:
    Bool, Int, Float, String = "ActorAttributeType.Bool", "ActorAttributeType.Int", "ActorAttributeType.Float", "ActorAttributeType.String"
class Attr:
    # Real CARLA: typed attribute, as_str() raises "bad attribute cast" unless the attribute is a String.
    def __init__(s, v):
        s.recommended_values, s._v = [], v
        s.type = ActorAttributeType.Int if v.isdigit() else ActorAttributeType.String
    def as_str(s):
        if s.type != ActorAttributeType.String: raise RuntimeError("bad attribute cast: cannot convert to String")
        return s._v
    def as_int(s): return int(s._v)
class BP:
    def __init__(s, id, attrs): s.id, s._a = id, attrs
    def has_attribute(s, k): return k in s._a
    def get_attribute(s, k): return Attr(s._a[k])
class World:
    def __init__(s, name): s._map = Map(name, {"Town01": 120, "Town01_Opt": 120, "Town03": 300, "Town10HD_Opt": 200, "Town02": 50}[name])
    def get_map(s): return s._map
    def get_actors(s): return Actors([TrafficLight()])
    def try_spawn_actor(s, bp, transform): return Vehicle(bp)
    def get_blueprint_library(s):
        return [BP("vehicle.tesla.model3", {"base_type": "car", "number_of_wheels": "4"}), BP("vehicle.yamaha.yzf", {"number_of_wheels": "2"}),
                BP("walker.pedestrian.0001", {})]
class WeatherParameters:
    cloudiness, precipitation, sun_altitude_angle = 10.0, 0.0, 45.0
WeatherParameters.ClearNoon = WeatherParameters(); WeatherParameters.WetNight = WeatherParameters()
LOADS = []
MAPS = ["/Game/Carla/Maps/AnnotationColorLandscape", "/Game/Carla/Maps/Town01", "/Game/Carla/Maps/Town01_Opt",
        "/Game/Carla/Maps/Town03", "/Game/Carla/Maps/Town10HD_Opt"]
DEAD = {"value": False}
class Client:
    def __init__(s, host, port): s._world = World("Town10HD_Opt")
    def set_timeout(s, t): pass
    def get_server_version(s):
        if DEAD["value"]: raise RuntimeError("time-out of 10000ms while waiting for the simulator")
        return "0.9.16"
    def get_world(s): return s._world
    def get_available_maps(s): return list(MAPS)
    def load_world(s, name):
        LOADS.append(name)
        if name.startswith("Annotation"): raise RuntimeError("failed to generate map")
        if name == "Town02":
            DEAD["value"] = True
            raise RuntimeError("time-out of 300000ms while waiting for the simulator")
        if DEAD["value"]: raise RuntimeError("time-out while waiting for the simulator")
        time.sleep(0.05); s._world = World(name); return s._world
