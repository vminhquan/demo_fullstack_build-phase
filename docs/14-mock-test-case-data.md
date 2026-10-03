# 14 — Mock Data Test Case cho Luồng B

## 1. Mục đích

Bộ mock này dùng cho:

- frontend list/filter;
- REST API mock;
- seed PostgreSQL;
- review workflow;
- Test Suite approved-only;
- keyword search;
- semantic/RAG search;
- automated filter matrix.

Dữ liệu là **mock**, không khẳng định đây là taxonomy chính thức của CARLA/ODD. Khi nhóm chốt 5 trường thật, đổi seed tương ứng.

## 2. Mock users

| user_key | display_name | roles |
|---|---|---|
| `creator_01` | Minh Creator | CREATOR |
| `creator_02` | Lan Creator | CREATOR |
| `reviewer_01` | Huy Reviewer | REVIEWER |
| `admin_01` | Admin Scenario Forge | ADMIN |

## 3. Mock Test Cases

| case_key | v | title | map | ego_vehicle | adversary | environment | danger | status | creator | tags |
|---|---:|---|---|---|---|---|---|---|---|---|
| TC-0001 | 2 | Pedestrian crossing in heavy rain | Town05 | vehicle.tesla.model3 | pedestrian | heavy_rain | HIGH | APPROVED | creator_01 | pedestrian, crossing, rain |
| TC-0002 | 1 | Vehicle cuts in from adjacent lane | Town03 | vehicle.lincoln.mkz_2020 | vehicle | clear_day | HIGH | APPROVED | creator_02 | cut-in, lane-change |
| TC-0003 | 1 | Cyclist merges from bike lane at night | Town05 | vehicle.tesla.model3 | cyclist | night | HIGH | IN_REVIEW | creator_01 | cyclist, merge, night |
| TC-0004 | 3 | Lead vehicle emergency braking | Town04 | vehicle.audi.tt | vehicle | clear_day | CRITICAL | APPROVED | creator_02 | braking, lead-vehicle, ttc |
| TC-0005 | 1 | Pedestrian waits at sidewalk | Town01 | vehicle.tesla.model3 | pedestrian | clear_day | LOW | DRAFT | creator_01 | pedestrian, nominal |
| TC-0006 | 2 | Motorcycle overtakes from right | Town03 | vehicle.lincoln.mkz_2020 | motorcycle | cloudy | MEDIUM | APPROVED | creator_02 | motorcycle, overtake |
| TC-0007 | 2 | Cyclist sudden merge after occlusion | Town05 | vehicle.tesla.model3 | cyclist | night | CRITICAL | APPROVED | creator_01 | cyclist, occlusion, merge |
| TC-0008 | 1 | Stopped vehicle blocks ego lane | Town05 | vehicle.audi.tt | vehicle | fog | HIGH | APPROVED | creator_02 | stopped-vehicle, fog, obstruction |
| TC-0009 | 2 | Running pedestrian emerges behind bus | Town03 | vehicle.tesla.model3 | pedestrian | heavy_rain | CRITICAL | APPROVED | creator_01 | pedestrian, occlusion, bus, rain |
| TC-0010 | 1 | Slow truck ahead on wet road | Town04 | vehicle.lincoln.mkz_2020 | vehicle | wet_cloudy | MEDIUM | REJECTED | creator_02 | truck, wet-road, follow |
| TC-0011 | 1 | Oncoming vehicle crosses center line | Town01 | vehicle.audi.tt | vehicle | clear_day | CRITICAL | APPROVED | creator_01 | oncoming, center-line |
| TC-0012 | 1 | Pedestrian crossing at signalized junction | Town03 | vehicle.tesla.model3 | pedestrian | clear_day | MEDIUM | APPROVED | creator_02 | pedestrian, junction, signal |
| TC-0013 | 2 | Parked car door opens near ego | Town05 | vehicle.tesla.model3 | vehicle | cloudy | HIGH | IN_REVIEW | creator_01 | dooring, parked-car, urban |
| TC-0014 | 1 | Occluded pedestrian crossing near bus stop | Town05 | vehicle.lincoln.mkz_2020 | pedestrian | wet_cloudy | HIGH | APPROVED | creator_02 | pedestrian, occlusion, bus-stop |
| TC-0015 | 1 | Adversary vehicle runs red light | Town03 | vehicle.audi.tt | vehicle | night | CRITICAL | APPROVED | creator_01 | red-light, junction, night |
| TC-0016 | 1 | Cyclist travels steadily in bike lane | Town01 | vehicle.tesla.model3 | cyclist | clear_day | LOW | DRAFT | creator_02 | cyclist, nominal, bike-lane |

## 4. Mock descriptions dùng cho semantic search

### TC-0001

```text
Ego vehicle approaches an urban crossing while heavy rain reduces visibility.
A pedestrian steps from the sidewalk into the ego lane with limited time to collision.
```

### TC-0002

```text
A neighboring vehicle changes lane sharply into the ego vehicle's lane with a small gap.
The ego vehicle must react to an aggressive cut-in maneuver.
```

### TC-0003

```text
At night, a cyclist leaves the bicycle lane and begins merging into the ego lane.
Visibility is reduced and the ego vehicle has limited reaction distance.
```

### TC-0004

```text
The lead vehicle performs a sudden hard brake while the ego vehicle follows at road speed.
The scenario evaluates collision avoidance and minimum time-to-collision.
```

### TC-0005

```text
A pedestrian stands on the sidewalk and does not enter the road.
This is a low-risk nominal urban driving case.
```

### TC-0006

```text
A motorcycle approaches quickly from the rear-right and overtakes the ego vehicle.
The maneuver tests lateral awareness without severe weather.
```

### TC-0007

```text
A cyclist is initially hidden by a roadside obstacle and suddenly merges into the ego lane at night.
The scenario combines occlusion, vulnerable road user behavior and short reaction time.
```

### TC-0008

```text
Dense fog limits forward visibility. A stationary vehicle blocks part of the ego lane.
The ego vehicle must detect the obstruction and stop or maneuver safely.
```

### TC-0009

```text
During heavy rain, a running pedestrian appears from behind a bus and crosses the road.
The bus creates visual occlusion and the time-to-collision is very short.
```

### TC-0010

```text
The ego vehicle follows a slow truck on a wet road under cloudy conditions.
The scenario focuses on safe following distance and low-friction braking.
```

### TC-0011

```text
An oncoming vehicle drifts across the center line into the ego vehicle's path.
The ego vehicle must avoid a potential head-on collision.
```

### TC-0012

```text
A pedestrian uses a marked crossing at a signalized junction during clear daytime conditions.
The ego vehicle must respect the crossing and traffic signal context.
```

### TC-0013

```text
A parked vehicle door opens into the ego vehicle's path on an urban street.
The hazard appears suddenly from the roadside.
```

### TC-0014

```text
Near a bus stop on a wet road, a pedestrian is hidden by a bus before entering the crossing.
The scenario tests detection under partial occlusion.
```

### TC-0015

```text
At night, another vehicle enters a junction against a red traffic signal and crosses the ego path.
The conflict can lead to a severe side collision.
```

### TC-0016

```text
A cyclist remains inside the bicycle lane and travels at a stable speed in clear weather.
This is a nominal low-risk interaction case.
```

## 5. JSON mock payload

Có thể copy đoạn sau thành seed fixture sau này:

```json
[
  {
    "case_key": "TC-0001",
    "title": "Pedestrian crossing in heavy rain",
    "version_no": 2,
    "map_code": "Town05",
    "ego_vehicle_code": "vehicle.tesla.model3",
    "adversary_type": "pedestrian",
    "environment_code": "heavy_rain",
    "danger_level": "HIGH",
    "status": "APPROVED",
    "creator_key": "creator_01",
    "tags": ["pedestrian", "crossing", "rain"]
  },
  {
    "case_key": "TC-0007",
    "title": "Cyclist sudden merge after occlusion",
    "version_no": 2,
    "map_code": "Town05",
    "ego_vehicle_code": "vehicle.tesla.model3",
    "adversary_type": "cyclist",
    "environment_code": "night",
    "danger_level": "CRITICAL",
    "status": "APPROVED",
    "creator_key": "creator_01",
    "tags": ["cyclist", "occlusion", "merge"]
  },
  {
    "case_key": "TC-0009",
    "title": "Running pedestrian emerges behind bus",
    "version_no": 2,
    "map_code": "Town03",
    "ego_vehicle_code": "vehicle.tesla.model3",
    "adversary_type": "pedestrian",
    "environment_code": "heavy_rain",
    "danger_level": "CRITICAL",
    "status": "APPROVED",
    "creator_key": "creator_01",
    "tags": ["pedestrian", "occlusion", "bus", "rain"]
  }
]
```

Bảng ở mục 3 là bộ dữ liệu đầy đủ; JSON rút gọn chỉ minh họa fixture format.

## 6. Expected filter results

### Filter 1

```text
status = APPROVED
adversary_type = pedestrian
```

Expected:

```text
TC-0001
TC-0009
TC-0012
TC-0014
```

### Filter 2

```text
map_code = Town05
status = APPROVED
danger_level IN (HIGH, CRITICAL)
```

Expected:

```text
TC-0001
TC-0007
TC-0008
TC-0014
```

### Filter 3

```text
creator = creator_01
status = DRAFT
```

Expected:

```text
TC-0005
```

## 7. Expected semantic-search behavior

Không ép thứ tự tuyệt đối vì ranking phụ thuộc embedding model. Dùng các expectation theo tập liên quan.

### Query A

```text
"person suddenly runs into road from behind a large vehicle in rain"
```

Expected top relevant set:

```text
TC-0009
TC-0014
TC-0001
```

### Query B

```text
"bike rider enters ego lane unexpectedly at night"
```

Expected top relevant set:

```text
TC-0007
TC-0003
```

Nếu thêm:

```text
status = APPROVED
```

thì `TC-0003` phải bị loại vì đang `IN_REVIEW`.

### Query C

```text
"vehicle ahead suddenly brakes"
```

Expected top relevant:

```text
TC-0004
```

### Query D

```text
"possible head on collision from opposite traffic"
```

Expected top relevant:

```text
TC-0011
```

## 8. Mock review history

```text
TC-0001 v1 -> REJECTED
  comment: "Increase visibility constraint and define crossing trigger more clearly."

TC-0001 v2 -> APPROVED
  reviewer: reviewer_01

TC-0003 v1 -> IN_REVIEW
  reviewer: reviewer_01

TC-0010 v1 -> REJECTED
  comment: "Danger level and expected behavior are not consistent."
```

## 9. Mock Test Suite

```text
Suite: Critical Urban Regression

items:
  TC-0004 v3 APPROVED
  TC-0007 v2 APPROVED
  TC-0009 v2 APPROVED
  TC-0011 v1 APPROVED
  TC-0015 v1 APPROVED
```

Test negative:

```text
Add TC-0003 v1 IN_REVIEW -> 409 VERSION_NOT_APPROVED
Add TC-0005 v1 DRAFT     -> 409 VERSION_NOT_APPROVED
Add TC-0010 v1 REJECTED  -> 409 VERSION_NOT_APPROVED
```

## 10. Seed strategy

Khuyến nghị tạo command riêng:

```text
python -m app.scripts.seed_mock_data
```

Seed phải idempotent theo `case_key`/`user_key`, không insert duplicate mỗi lần chạy.

Môi trường production không tự chạy mock seed.
