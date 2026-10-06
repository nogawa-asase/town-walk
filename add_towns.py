"""
fuchu_areas.geojson の各エリアに「町丁目名」(town) を付け足すスクリプト。

エリアの形やIDは変えません（記録とずれません）。
町丁目の境界データ(e-Stat 国勢調査 町丁・字等境界)を、towns/ フォルダに置いて使います。

使い方:
    1. towns/ フォルダを作り、ダウンロードしたシェープファイル一式
       (.shp .shx .dbf .prj など)を入れる
    2. python add_towns.py

各エリアは、いちばん広く重なっている町丁目に所属させます。
"""

import glob
import re
import shutil

import geopandas as gpd
import pandas as pd

# ---------------- 設定 ----------------
AREAS = "fuchu_areas.geojson"
TOWNS_OUT = "fuchu_towns.geojson"   # 町丁目の範囲（アプリで点線の枠として表示）
TOWN_GLOB = "towns/*.shp"
NAME_FIELD = "S_NAME"          # e-Stat 境界データの町丁・字等名称
METRIC_CRS = "EPSG:6677"       # メートルで面積を比べるため
# --------------------------------------

JP = re.compile(r"[ぁ-んァ-ヶ一-龥]")


def read_towns():
    files = sorted(glob.glob(TOWN_GLOB))
    if not files:
        raise SystemExit(
            f"{TOWN_GLOB} が見つかりません。towns/ フォルダに町丁目のシェープファイル(.shp)を入れてください。"
        )
    parts = []
    for f in files:
        g = None
        # e-Statのshpは文字コードがShift-JIS。UTF-8の場合もあるので両方試す
        for enc in ("cp932", "utf-8"):
            try:
                cand = gpd.read_file(f, encoding=enc)
            except Exception:  # noqa: BLE001
                continue
            if NAME_FIELD in cand.columns and cand[NAME_FIELD].astype(str).str.contains(JP).any():
                g = cand
                break
        if g is None:
            print(f"  スキップ: {f}（{NAME_FIELD} の列が読めませんでした）")
            continue
        if g.crs is None:
            g = g.set_crs("EPSG:6668")  # 世界測地系 緯度経度
        parts.append(g.to_crs(METRIC_CRS))
        print(f"  読み込み: {f}（{len(g)}件）")
    if not parts:
        raise SystemExit("町丁目データを読み込めませんでした。")

    g = pd.concat(parts, ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs=METRIC_CRS)

    if "HCODE" in g.columns:                       # 水面（川・池）は除く
        g = g[g["HCODE"].astype(str) != "8154"]
    if "CITY_NAME" in g.columns:                   # 東京都全体のファイルでも府中市だけにする
        sub = g[g["CITY_NAME"].astype(str).str.contains("府中市")]
        if len(sub):
            g = sub
    g = g.copy()
    g["name"] = g[NAME_FIELD].astype(str).str.strip()
    g = g[~g["name"].isin(["", "nan", "None"])]
    g["geometry"] = g.geometry.buffer(0)
    # 飛び地などで同じ名前が複数あるものは1つにまとめる
    return g[["name", "geometry"]].dissolve(by="name")


def main():
    print("エリアを読み込み中...")
    areas = gpd.read_file(AREAS)
    areas_m = areas.to_crs(METRIC_CRS)

    print("町丁目データを読み込み中...")
    towns = read_towns()
    print(f"  町丁目の数: {len(towns)}")

    sidx = towns.sindex
    town_geoms = list(towns.geometry)
    town_names = list(towns.index)

    result = []
    for poly in areas_m.geometry:
        poly = poly.buffer(0)
        best, best_area = None, 1.0  # 1m²未満の重なりは無視
        for j in sidx.query(poly, predicate="intersects"):
            a = poly.intersection(town_geoms[int(j)]).area
            if a > best_area:
                best, best_area = town_names[int(j)], a
        result.append(best)

    areas["town"] = result
    missing = sum(1 for r in result if r is None)
    print(f"町丁目を割り当てたエリア: {len(result) - missing} / {len(result)}")
    if missing:
        print(f"  町丁目が見つからないエリア: {missing}（アプリでは町丁目の表示が出ません）")

    shutil.copyfile(AREAS, AREAS.replace(".geojson", ".backup.geojson"))
    areas.to_file(AREAS, driver="GeoJSON", COORDINATE_PRECISION=6)
    print(f"保存しました: {AREAS}（元のファイルは .backup.geojson に残してあります）")

    # 町丁目の範囲 = その町丁目に割り当てたエリアを合体させたもの
    # （パーセントの計算と、地図に出す枠の形が一致する）
    tdf = gpd.GeoDataFrame({"name": result}, geometry=list(areas_m.geometry), crs=METRIC_CRS)
    tdf = tdf.dropna(subset=["name"])
    merged = tdf.dissolve(by="name")
    # エリア間の細いすき間を閉じてから、少しだけ簡略化する
    merged["geometry"] = merged.geometry.buffer(1.5).buffer(-1.5).simplify(0.5)
    out = merged.reset_index()[["name", "geometry"]].to_crs("EPSG:4326")
    out.to_file(TOWNS_OUT, driver="GeoJSON", COORDINATE_PRECISION=6)
    print(f"保存しました: {TOWNS_OUT}（{len(out)}町丁目）")

    counts = pd.Series([r for r in result if r]).value_counts()
    print("\n町丁目ごとのエリア数（上位10）:")
    print(counts.head(10).to_string())


if __name__ == "__main__":
    main()
