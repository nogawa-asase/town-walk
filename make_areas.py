"""
府中市の「道で囲まれたエリア」を作って fuchu_areas.geojson に保存するスクリプト。

使い方:
    pip install osmnx geopandas shapely pandas
    python make_areas.py

- 細い路地まで境界にする（私道・駐車場内通路・私有地は除外）
- 市境で切る
- 小さすぎるエリアは、いちばん長く接している隣のエリアに統合する

※ 一度つくった fuchu_areas.geojson は、歩き始めたら作り直さないでください。
   エリアのIDが変わって、記録とずれます。
"""

import pandas as pd
import geopandas as gpd
import osmnx as ox
from shapely.geometry import LineString, MultiLineString, Polygon, MultiPolygon
from shapely.ops import unary_union, polygonize

# ---------------- 設定（ここを変えて調整） ----------------
PLACE_QUERIES = ["府中市, 東京都, 日本", "Fuchu, Tokyo, Japan"]
OUTPUT = "fuchu_areas.geojson"
METRIC_CRS = "EPSG:6677"      # 平面直角座標系 IX系（東京）。メートルで計算するため
MIN_AREA_M2 = 400             # これより小さいエリアは隣に統合（約20m×20m）。0で無効
INCLUDE_PATHS = True          # True: 公園内の園路・抜け道（footway/path等）も境界にする
INCLUDE_RAILWAY = True        # True: 線路も境界にする（歩いて渡れないため）

# 境界にする道路の種類（細い路地は residential / living_street / service / pedestrian）
ROAD_TYPES = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "pedestrian", "road",
}
PATH_TYPES = {"footway", "path", "cycleway", "steps", "bridleway"}
# 歩道（道路沿いの並行線）や横断歩道は、境界にすると細長い欠片だらけになるので除外
EXCLUDE_FOOTWAY = {"sidewalk", "crossing", "traffic_island"}
# 私道・敷地内の道は除外
EXCLUDE_ACCESS = {"private", "no"}
EXCLUDE_SERVICE = {"driveway", "parking_aisle", "drive-through"}
# -----------------------------------------------------------


def get_boundary():
    for q in PLACE_QUERIES:
        try:
            gdf = ox.geocode_to_gdf(q)
        except Exception as e:  # noqa: BLE001
            print(f"  {q}: 取得失敗 ({e})")
            continue
        geom = gdf.geometry.iloc[0]
        if isinstance(geom, (Polygon, MultiPolygon)):
            print(f"市境を取得: {q}")
            return gdf.iloc[[0]]
    raise SystemExit("府中市の市境(ポリゴン)を取得できませんでした。")


def col(df, name):
    """列がなくても動くように、文字列の列を返す"""
    if name in df.columns:
        return df[name].astype(str)
    return pd.Series("nan", index=df.index)


def pick_roads(feats):
    hw = col(feats, "highway")
    ok = hw.isin(ROAD_TYPES)
    if INCLUDE_PATHS:
        ok |= hw.isin(PATH_TYPES)
    if INCLUDE_RAILWAY:
        ok |= col(feats, "railway").isin({"rail", "light_rail"})

    # 地下のトンネルは地上の境界にならないので除外（建物内の通路は残す）
    bad = (
        col(feats, "footway").isin(EXCLUDE_FOOTWAY)
        | col(feats, "access").isin(EXCLUDE_ACCESS)
        | col(feats, "service").isin(EXCLUDE_SERVICE)
        | col(feats, "tunnel").isin({"yes"})
    )
    return feats[ok & ~bad]


def to_lines(geoms):
    lines = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        if isinstance(g, (Polygon, MultiPolygon)):
            g = g.boundary  # 歩行者広場など面で描かれた道
        if isinstance(g, LineString):
            lines.append(g)
        elif isinstance(g, MultiLineString):
            lines.extend(g.geoms)
        elif hasattr(g, "geoms"):
            lines.extend(to_lines(g.geoms))
    return lines


def merge_small(polys, min_area):
    """面積が小さいエリアを、境界線がいちばん長く接する隣へ統合する"""
    if min_area <= 0:
        return polys
    polys = list(polys)
    while True:
        gs = gpd.GeoSeries(polys)
        sindex = gs.sindex
        order = sorted(range(len(polys)), key=lambda i: polys[i].area)
        used = set()
        new = {}
        merged_any = False
        for i in order:
            if polys[i].area >= min_area:
                break
            if i in used:
                continue
            small = polys[i]
            best, best_len = None, 0.0
            for j in sindex.query(small, predicate="intersects"):
                j = int(j)
                if j == i or j in used:
                    continue
                shared = small.boundary.intersection(polys[j].boundary).length
                if shared > best_len:
                    best, best_len = j, shared
            if best is None:
                continue
            new[best] = unary_union([polys[best], small])
            used.update({i, best})
            merged_any = True
        if not merged_any:
            return polys
        polys = [new.get(k, p) for k, p in enumerate(polys) if k not in used or k in new]


def main():
    boundary_gdf = get_boundary()
    boundary_poly = boundary_gdf.geometry.iloc[0]

    print("OpenStreetMapから道路を取得中（数分かかることがあります）...")
    tags = {"highway": True}
    if INCLUDE_RAILWAY:
        tags["railway"] = ["rail", "light_rail"]
    feats = ox.features_from_polygon(boundary_poly, tags=tags)
    feats = pick_roads(feats)
    print(f"  道路・線路の数: {len(feats)}")

    feats_m = feats.to_crs(METRIC_CRS)
    boundary_m = boundary_gdf.to_crs(METRIC_CRS).geometry.iloc[0]

    lines = to_lines(feats_m.geometry)
    lines.append(boundary_m.boundary)  # 市境も境界線にして、市境で切る
    print("道路で囲まれたエリアを作成中...")
    noded = unary_union(lines)
    polys = [p for p in polygonize(noded) if p.representative_point().within(boundary_m)]
    print(f"  エリア数（統合前）: {len(polys)}")

    polys = merge_small(polys, MIN_AREA_M2)
    polys = [p for p in polys if not p.is_empty and p.area > 1]
    print(f"  エリア数（統合後）: {len(polys)}")

    gdf = gpd.GeoDataFrame(geometry=polys, crs=METRIC_CRS)
    gdf["area_m2"] = gdf.geometry.area.round(0).astype(int)
    gdf["cx"] = gdf.geometry.centroid.x
    gdf["cy"] = gdf.geometry.centroid.y
    # 北から南、西から東の順にIDを振る（作り直さない限り固定）
    gdf = gdf.sort_values(["cy", "cx"], ascending=[False, True]).reset_index(drop=True)
    gdf["id"] = gdf.index + 1
    gdf["geometry"] = gdf.geometry.simplify(0.5)  # 0.5m単位で簡略化してファイルを軽くする
    gdf = gdf[["id", "area_m2", "geometry"]].to_crs("EPSG:4326")

    gdf.to_file(OUTPUT, driver="GeoJSON", COORDINATE_PRECISION=6)
    print(f"保存しました: {OUTPUT}（{len(gdf)}エリア）")


if __name__ == "__main__":
    main()
