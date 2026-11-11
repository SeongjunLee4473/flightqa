# DJI 임무 파일 (KMZ / WPML) 구조

`core/mission.py` 의 근거. DJI Pilot 2 (RC Pro, M3M) 의 비행 경로 라이브러리에서 내보낸 실제 매핑 임무
KMZ (2026-09-30, `wpml` 네임스페이스 `http://www.dji.com/wpmz/1.0.6`) 로 확인했다.
좌표는 `<lon>,<lat>` 로 마스킹했고 실제 값은 저장소에 두지 않는다 (CLAUDE.md §12).

## 파일 구성

KMZ 는 zip 이고 안에 폴더 `wpmz/` 가 있다.

| 경로 | 내용 | 크기(샘플) |
|---|---|---|
| `wpmz/template.kml` | 임무 파라미터 + **매핑 폴리곤** (조정기에서 그린 구역) | 3.7 KB |
| `wpmz/waylines.wpml` | 조정기가 폴리곤에서 생성한 **웨이포인트**·동작 | 50 KB (24 점) |

임무 이름은 KMZ 파일 이름이고, 그 임무로 촬영한 영상 폴더는 `DJI_YYYYMMDDHHMM_NNN_<임무명>` 이다.
같은 임무를 여러 번 비행하면 폴더는 여러 개(`_005_`, `_006_` …)이고 KMZ 는 하나다.
→ `mission.choose_mission_file` 은 폴더명의 임무명과 파일 이름이 같은 KMZ 를 우선 고른다.

## template.kml

```xml
<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document>
    <wpml:createTime>1790648226033</wpml:createTime>        <!-- ms epoch (UTC) -->
    <wpml:updateTime>1790648889369</wpml:updateTime>
    <wpml:missionConfig>
      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>
      <wpml:finishAction>goHome</wpml:finishAction>
      <wpml:exitOnRCLost>executeLostAction</wpml:exitOnRCLost>
      <wpml:executeRCLostAction>goBack</wpml:executeRCLostAction>
      <wpml:takeOffSecurityHeight>20</wpml:takeOffSecurityHeight>
      <wpml:takeOffRefPoint><lat>,<lon>,108.400000</wpml:takeOffRefPoint>   <!-- 주의: lat,lon,alt 순서 -->
      <wpml:globalTransitionalSpeed>15</wpml:globalTransitionalSpeed>
      <wpml:droneInfo>
        <wpml:droneEnumValue>77</wpml:droneEnumValue>          <!-- 77 = Mavic 3 Enterprise 계열 (M3M) -->
        <wpml:droneSubEnumValue>0</wpml:droneSubEnumValue>
      </wpml:droneInfo>
      <wpml:waylineAvoidLimitAreaMode>0</wpml:waylineAvoidLimitAreaMode>
      <wpml:payloadInfo>
        <wpml:payloadEnumValue>68</wpml:payloadEnumValue>       <!-- 68 = M3M 카메라 -->
        <wpml:payloadSubEnumValue>3</wpml:payloadSubEnumValue>
        <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>
      </wpml:payloadInfo>
    </wpml:missionConfig>
    <Folder>
      <wpml:templateType>mapping2d</wpml:templateType>          <!-- 매핑 임무. waypoint / mapping3d / mappingStrip 도 있음 -->
      <wpml:templateId>0</wpml:templateId>
      <wpml:waylineCoordinateSysParam>
        <wpml:coordinateMode>WGS84</wpml:coordinateMode>
        <wpml:heightMode>relativeToStartPoint</wpml:heightMode>
        <wpml:globalShootHeight>108.444213867188</wpml:globalShootHeight>
      </wpml:waylineCoordinateSysParam>
      <wpml:autoFlightSpeed>6</wpml:autoFlightSpeed>            <!-- m/s -->
      <Placemark>
        <wpml:caliFlightEnable>0</wpml:caliFlightEnable>
        <wpml:elevationOptimizeEnable>1</wpml:elevationOptimizeEnable>   <!-- 지형(DSM) 추종 -->
        <wpml:smartObliqueEnable>0</wpml:smartObliqueEnable>
        <wpml:quickOrthoMappingEnable>0</wpml:quickOrthoMappingEnable>
        <wpml:facadeWaylineEnable>0</wpml:facadeWaylineEnable>
        <wpml:isLookAtSceneSet>0</wpml:isLookAtSceneSet>
        <wpml:smartObliqueGimbalPitch>-45</wpml:smartObliqueGimbalPitch>
        <wpml:shootType>time</wpml:shootType>                    <!-- time | distance -->
        <wpml:direction>90</wpml:direction>                      <!-- 라인 방향 (도) -->
        <wpml:margin>10</wpml:margin>                            <!-- 폴리곤 바깥으로 라인을 늘리는 여유 (m) -->
        <wpml:efficiencyFlightModeEnable>0</wpml:efficiencyFlightModeEnable>
        <wpml:overlap>
          <wpml:orthoLidarOverlapH>85</wpml:orthoLidarOverlapH>
          <wpml:orthoLidarOverlapW>85</wpml:orthoLidarOverlapW>
          <wpml:orthoCameraOverlapH>85</wpml:orthoCameraOverlapH>  <!-- 종중복 % -->
          <wpml:orthoCameraOverlapW>85</wpml:orthoCameraOverlapW>  <!-- 횡중복 % -->
        </wpml:overlap>
        <Polygon>                                                <!-- 조정기에서 그린 임무 구역 -->
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>
                <lon>,<lat>
                <lon>,<lat>
                <lon>,<lat>
                <lon>,<lat>                                      <!-- 닫히지 않음 (첫 점 반복 없음) -->
              </coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
        <wpml:ellipsoidHeight>108.444213867188</wpml:ellipsoidHeight>
        <wpml:height>108.444213867188</wpml:height>              <!-- 비행 고도 (heightMode 기준) -->
      </Placemark>
      <wpml:payloadParam>
        <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>
        <wpml:dewarpingEnable>0</wpml:dewarpingEnable>
        <wpml:returnMode>singleReturnFirst</wpml:returnMode>
        <wpml:samplingRate>240000</wpml:samplingRate>
        <wpml:scanningMode>nonRepetitive</wpml:scanningMode>
        <wpml:modelColoringEnable>0</wpml:modelColoringEnable>
        <wpml:imageFormat>visable,narrow_band</wpml:imageFormat>  <!-- RGB + 다중분광 (DJI 철자 그대로) -->
      </wpml:payloadParam>
    </Folder>
  </Document>
</kml>
```

## waylines.wpml

같은 `missionConfig` 를 반복한 뒤 `Folder` 하나에 웨이포인트 `Placemark` 가 순서대로 있다 (샘플 24 개 = 12 라인).

```xml
<Folder>
  <wpml:templateId>0</wpml:templateId>
  <wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode>
  <wpml:waylineId>0</wpml:waylineId>
  <wpml:distance>2799.03198242188</wpml:distance>     <!-- 경로 길이 m -->
  <wpml:duration>475.296936035156</wpml:duration>     <!-- 예상 소요 s -->
  <wpml:autoFlightSpeed>6</wpml:autoFlightSpeed>
  <wpml:startActionGroup> … gimbalRotate(-90°) · hover · setFocusType(manual) · focus(infinite) … </wpml:startActionGroup>
  <Placemark>
    <Point><coordinates><lon>,<lat></coordinates></Point>
    <wpml:index>0</wpml:index>
    <wpml:executeHeight>108.444213867188</wpml:executeHeight>
    <wpml:waypointSpeed>6</wpml:waypointSpeed>
    <wpml:waypointHeadingParam>
      <wpml:waypointHeadingMode>followWayline</wpml:waypointHeadingMode>
      <wpml:waypointHeadingAngle>-89.9985264380369</wpml:waypointHeadingAngle>
      …
    </wpml:waypointHeadingParam>
    <wpml:waypointTurnParam>
      <wpml:waypointTurnMode>toPointAndStopWithDiscontinuityCurvature</wpml:waypointTurnMode>
      <wpml:waypointTurnDampingDist>0</wpml:waypointTurnDampingDist>
    </wpml:waypointTurnParam>
    <wpml:useStraightLine>1</wpml:useStraightLine>
    <wpml:actionGroup>            <!-- betweenAdjacentPoints: startTimeLapse(visable,narrow_band) … 마지막 점: stopTimeLapse -->
      …
    </wpml:actionGroup>
    <wpml:waypointGimbalHeadingParam> … </wpml:waypointGimbalHeadingParam>
    <wpml:isRisky>0</wpml:isRisky>
    <wpml:waypointWorkType>0</wpml:waypointWorkType>
  </Placemark>
  …
</Folder>
```

## 파서가 쓰는 것

| 필드 (`MissionPlan`) | 태그 | 비고 |
|---|---|---|
| `polygon_geojson` | `Placemark/Polygon/outerBoundaryIs/LinearRing/coordinates` | `lon,lat[,alt]`, 첫 점을 붙여 닫는다. Placemark 가 여러 개면 MultiPolygon |
| `waypoints_geojson`, `n_waypoints` | waylines `Placemark/Point/coordinates` | 순서대로 LineString |
| `template_type` | `wpml:templateType` | `mapping2d` 가 아니면 폴리곤이 없을 수 있음 → `MissionError` |
| `height_m`, `shoot_height_m`, `height_mode` | `wpml:height`, `wpml:globalShootHeight`, `wpml:heightMode` | |
| `margin_m` | `wpml:margin` | 라인이 폴리곤 밖으로 나가는 양 → "폴리곤 밖 캡처 비율" 해석에 사용 |
| `overlap_h_pct`, `overlap_w_pct` | `wpml:orthoCameraOverlapH/W` | 계획값. 실측 중복과 비교 가능 |
| `direction_deg`, `speed_mps`, `shoot_type` | `wpml:direction`, `wpml:autoFlightSpeed`, `wpml:shootType` | |
| `elevation_optimize` | `wpml:elevationOptimizeEnable` | 1 = DSM 추종 (지형추종 감지 규칙과 교차 확인 가능) |
| `drone_enum`, `payload_enum` | `wpml:droneEnumValue`, `wpml:payloadEnumValue` | 77 / 68 = M3M |
| `takeoff_ref` | `wpml:takeOffRefPoint` | **lat,lon,alt** 순서 |
| `created_utc` | `wpml:createTime` | ms epoch |
| `distance_m`, `duration_s` | waylines `wpml:distance`, `wpml:duration` | |

확인하지 못한 것: M350 + Pilot 2 (RC Plus) 에서 내보낸 KMZ 가 같은 구조인지. 네임스페이스 버전이 다르면
`_WPML_NS_RE` 가 실제 값을 읽으므로 파싱은 되고, 폴리곤 태그 위치가 다르면 `MissionError` 로 경고가 난다.
