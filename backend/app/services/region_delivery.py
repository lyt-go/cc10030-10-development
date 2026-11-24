"""区域合作方授权交付领域服务。

面向区域合作方的统计交付，核心约束：
1. 合作方只获得当前授权子区内的网格匿名统计；终端标识与足以还原单条轨迹的
   字段（原始坐标、时间戳、点位编号等）不得随包发出。
2. 网格跨授权区/未授权区时，按点的原始位置逐点判定子区归属，交付前核对每个
   跨界格实际贡献的子区与点数，未授权子区的贡献只在边界说明中留痕、不出数据。
3. 授权随时点生效：新下载一律按发送时刻的有效授权裁切；授权收窄不影响已交付
   包，内部仍保留完整子区贡献副本用于复核。
4. 审校员（外部视角）只能查看与该包对应的共享观测归属；越权查看其他区域或请求
   整格明细时，只返回边界说明，不返回数据。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.services.base import BaseModuleService

# 随包禁止外发的字段：终端标识、点位编号、原始/转换坐标、时间戳等可还原单条轨迹的数据。
FORBIDDEN_PACKAGE_FIELDS = {
    "terminal", "point_id", "point_ids", "points",
    "lat", "lon", "orig_lat", "orig_lon", "trans_lat", "trans_lon",
    "timestamp", "bearing", "speed", "accuracy",
}

# 授权状态文件中“未登记子区”的内部占位编码，永不会进入交付包。
UNREGISTERED_CODE = "_unregistered"


class RegionDeliveryService(BaseModuleService):
    MODULE = "region_delivery"
    ENTITY = "区域交付任务"
    OPERATION_TERM = "交付"
    LIST_FIELDS = ["交付编号", "合作方编号", "请求编号", "授权子区", "网格数量", "匿名行数", "窗口范围", "交付水位"]
    REQUIRED_FIELDS = ["交付编号", "合作方编号", "请求编号"]
    STATUS_ORDER = ["草稿", "待授权", "已授权", "已交付"]
    ACTION_RULES = {"提交授权": "待授权", "确认授权": "已授权", "确认交付": "已交付"}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 状态持久化（JSONL 数据集，整体覆写保证授权更正可追溯）
    # ------------------------------------------------------------------

    def _state_path(self, name: str) -> Path:
        return self._data_dir("region_delivery") / name

    def _load_state(self, name: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._state_path(name))

    def _save_state(self, name: str, rows: list[dict[str, Any]]) -> Path:
        path = self._state_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return path

    # ------------------------------------------------------------------
    # 子区登记与点-区判定
    # ------------------------------------------------------------------

    def register_subzone(self, subzone_code: str, subzone_name: str,
                         polygon: list[list[float]], parent_region: str = "") -> dict[str, Any]:
        """登记授权子区及其多边形边界（顶点为 [纬度, 经度]，首尾不必闭合）。"""
        rows = self._load_state("subzones.jsonl")
        if any(row["subzone_code"] == subzone_code for row in rows):
            raise ValueError(f"子区 {subzone_code} 已登记，不能重复登记")
        record = {
            "subzone_code": subzone_code,
            "subzone_name": subzone_name,
            "parent_region": parent_region,
            "polygon": [[float(lat), float(lon)] for lat, lon in polygon],
            "registered_at": time.time(),
        }
        rows.append(record)
        self._save_state("subzones.jsonl", rows)
        return record

    def list_subzones(self) -> list[dict[str, Any]]:
        return self._load_state("subzones.jsonl")

    @staticmethod
    def point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
        """射线法判定点是否在多边形内（边界点按约定归北侧/东侧多边形，测试数据不取临界值）。"""
        inside = False
        if len(polygon) < 3:
            return False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            lat_i, lon_i = polygon[i]
            lat_j, lon_j = polygon[j]
            intersects = ((lat_i > lat) != (lat_j > lat)) and (
                lon < (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i) + lon_i
            )
            if intersects:
                inside = not inside
            j = i
        return inside

    def _locate_subzone(self, lat: float, lon: float) -> str | None:
        for sub in self.list_subzones():
            if self.point_in_polygon(lat, lon, sub["polygon"]):
                return sub["subzone_code"]
        return None

    def _subzone_name_map(self) -> dict[str, str]:
        return {row["subzone_code"]: row["subzone_name"] for row in self.list_subzones()}

    # ------------------------------------------------------------------
    # 授权台账：授权/收窄按时点留痕，有效授权按“每个子区最近一条生效记录”计算
    # ------------------------------------------------------------------

    def grant_authorization(self, partner_id: str, subzone_codes: list[str],
                            note: str = "") -> dict[str, Any]:
        """为合作方追加（或覆盖式更新）子区授权，记录生效时点。"""
        known = {row["subzone_code"] for row in self.list_subzones()}
        unknown = [code for code in subzone_codes if code not in known]
        if unknown:
            raise ValueError(f"子区未登记，无法授权：{'、'.join(unknown)}")
        rows = self._load_state("authorizations.jsonl")
        now = time.time()
        for code in subzone_codes:
            rows.append({
                "partner_id": partner_id,
                "subzone_code": code,
                "granted": True,
                "effective_from": now,
                "note": note,
                "updated_at": now,
            })
        self._save_state("authorizations.jsonl", rows)
        return {"partner_id": partner_id, "granted": list(subzone_codes), "effective_from": now}

    def revoke_authorization(self, partner_id: str, subzone_codes: list[str],
                             note: str = "") -> dict[str, Any]:
        """收窄授权：对指定子区写入停用记录，已交付历史包不受影响。"""
        rows = self._load_state("authorizations.jsonl")
        now = time.time()
        for code in subzone_codes:
            rows.append({
                "partner_id": partner_id,
                "subzone_code": code,
                "granted": False,
                "effective_from": now,
                "note": note,
                "updated_at": now,
            })
        self._save_state("authorizations.jsonl", rows)
        return {"partner_id": partner_id, "revoked": list(subzone_codes), "effective_from": now}

    def effective_authorization(self, party_id: str, at: float | None = None) -> list[str]:
        """返回某一方在指定时点（默认当前）的有效授权子区，按子区编码排序。"""
        if at is None:
            at = time.time()
        latest: dict[str, dict[str, Any]] = {}
        for row in self._load_state("authorizations.jsonl"):
            if row["partner_id"] != party_id or float(row["effective_from"]) > at:
                continue
            old = latest.get(row["subzone_code"])
            if old is None or float(row["effective_from"]) >= float(old["effective_from"]):
                latest[row["subzone_code"]] = row
        return sorted(code for code, row in latest.items() if row["granted"])

    def authorization_history(self, partner_id: str) -> list[dict[str, Any]]:
        return [row for row in self._load_state("authorizations.jsonl") if row["partner_id"] == partner_id]

    # ------------------------------------------------------------------
    # 网格 × 子区实际贡献统计（逐点依据原始位置判定）
    # ------------------------------------------------------------------

    def write_batch_points(self, batch_key: str, points: list[dict[str, Any]]) -> str:
        """写入批次定位点数据集（测试/接入用），保留原始位置与终端字段于内部库。"""
        rows = []
        for idx, point in enumerate(points, start=1):
            row = dict(point)
            row.setdefault("point_id", idx)
            row["batch_key"] = batch_key
            rows.append(row)
        path = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        self._write_jsonl(path, rows)
        return str(path)

    def _read_batch_points(self, batch_key: str) -> list[dict[str, Any]]:
        transformed = self._data_dir("coord_transformed") / f"{batch_key}_transformed.jsonl"
        raw = self._data_dir("geo_points") / f"{batch_key}.jsonl"
        path = transformed if transformed.exists() else raw
        if not path.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的定位点数据不存在")
        return self._read_jsonl(path)

    def compute_grid_subzone_stats(self, batch_key: str, precision: int = 6) -> dict[str, Any]:
        """按原始位置逐点判定子区归属，统计每个网格内各子区实际贡献的点数。"""
        points = self._read_batch_points(batch_key)
        name_map = self._subzone_name_map()
        # geohash -> subzone_code -> 聚合桶
        grids: dict[str, dict[str, Any]] = {}
        for point in points:
            lat = float(point.get("trans_lat", point.get("lat")))
            lon = float(point.get("trans_lon", point.get("lon")))
            geohash = self._geohash_encode(lat, lon, precision)
            subzone_code = self._locate_subzone(lat, lon) or UNREGISTERED_CODE
            bucket = grids.setdefault(geohash, {})
            contrib = bucket.setdefault(subzone_code, {"point_count": 0, "point_ids": []})
            contrib["point_count"] += 1
            contrib["point_ids"].append(point.get("point_id"))

        records = []
        for geohash, bucket in sorted(grids.items()):
            subzone_contrib = []
            unregistered_points = 0
            for code in sorted(bucket):
                info = bucket[code]
                if code == UNREGISTERED_CODE:
                    unregistered_points = info["point_count"]
                    continue
                subzone_contrib.append({
                    "subzone_code": code,
                    "subzone_name": name_map.get(code, code),
                    "point_count": info["point_count"],
                    "point_ids": list(info["point_ids"]),
                })
            total = sum(item["point_count"] for item in subzone_contrib) + unregistered_points
            records.append({
                "batch_key": batch_key,
                "geohash": geohash,
                "precision": precision,
                "total_points": total,
                "cross_boundary": len(subzone_contrib) > 1,
                "unregistered_points": unregistered_points,
                "subzone_contrib": subzone_contrib,
            })

        out = self._data_dir("grid_subzone_stats") / f"{batch_key}_grid_subzone.jsonl"
        self._write_state_absolute(out, records)
        cross = [r["geohash"] for r in records if r["cross_boundary"]]
        return {
            "batch_key": batch_key,
            "precision": precision,
            "grids": len(records),
            "cross_boundary_grids": cross,
            "total_points": len(points),
            "output_file": str(out),
        }

    def _write_state_absolute(self, path: Path, rows: list[dict[str, Any]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return path

    def get_grid_subzone_stats(self, batch_key: str) -> list[dict[str, Any]]:
        path = self._data_dir("grid_subzone_stats") / f"{batch_key}_grid_subzone.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"批次 {batch_key} 的网格子区贡献统计不存在，请先计算")
        return self._read_jsonl(path)

    def verify_cross_boundary_contribution(self, batch_key: str) -> list[dict[str, Any]]:
        """交付前核对：逐个跨界格列出实际贡献子区与点数，并校验合计与网格总量一致。"""
        result = []
        for grid in self.get_grid_subzone_stats(batch_key):
            if not grid["cross_boundary"]:
                continue
            contrib_sum = sum(item["point_count"] for item in grid["subzone_contrib"])
            checked_total = contrib_sum + int(grid.get("unregistered_points", 0))
            result.append({
                "batch_key": batch_key,
                "geohash": grid["geohash"],
                "cross_boundary": True,
                "subzone_contrib": [
                    {"subzone_code": item["subzone_code"],
                     "subzone_name": item["subzone_name"],
                     "point_count": item["point_count"]}
                    for item in grid["subzone_contrib"]
                ],
                "unregistered_points": grid.get("unregistered_points", 0),
                "grid_total_points": grid["total_points"],
                "contrib_check_passed": checked_total == grid["total_points"],
            })
        return result

    # ------------------------------------------------------------------
    # 交付请求与授权裁切发包
    # ------------------------------------------------------------------

    def create_delivery_request(self, request_id: str, partner_id: str, batch_key: str,
                                requested_subzones: list[str], windows: list[str] | None = None
                                ) -> dict[str, Any]:
        """登记合作方的匿名统计交付请求，并快照提出请求时的授权范围。"""
        if self._load_state("requests.jsonl") and any(
                row["request_id"] == request_id for row in self._load_state("requests.jsonl")):
            raise ValueError(f"交付请求 {request_id} 已存在")
        # 没有统计则先按原始位置计算，保证后续裁切有依据。
        stats_path = self._data_dir("grid_subzone_stats") / f"{batch_key}_grid_subzone.jsonl"
        if not stats_path.exists():
            self.compute_grid_subzone_stats(batch_key)
        record = {
            "request_id": request_id,
            "partner_id": partner_id,
            "batch_key": batch_key,
            "requested_subzones": list(requested_subzones),
            "windows": list(windows or []),
            "reviewer_id": partner_id,
            "status": "待授权",
            "authorization_at_request": self.effective_authorization(partner_id),
            "created_at": time.time(),
            "delivered_package_id": None,
        }
        rows = self._load_state("requests.jsonl")
        rows.append(record)
        self._save_state("requests.jsonl", rows)
        return record

    def list_requests(self) -> list[dict[str, Any]]:
        return self._load_state("requests.jsonl")

    def _get_request(self, request_id: str) -> dict[str, Any]:
        for row in self._load_state("requests.jsonl"):
            if row["request_id"] == request_id:
                return row
        raise KeyError(f"交付请求 {request_id} 不存在")

    @staticmethod
    def _boundary_note(code: str, message: str, **extra: Any) -> dict[str, Any]:
        note = {"code": code, "message": message}
        note.update(extra)
        return note

    def _sanitize_package_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """白名单式清洗：剔除终端标识与一切可还原单条轨迹的字段。"""
        return {key: value for key, value in row.items() if key not in FORBIDDEN_PACKAGE_FIELDS}

    def deliver_request(self, request_id: str) -> dict[str, Any]:
        """按发送时刻的有效授权裁切并生成匿名统计交付包。

        - 实际交付范围 = 请求子区 ∩ 当前有效授权；
        - 未授权子区、跨界格中被截留的贡献只写边界说明，不出数据；
        - 内部保留完整子区贡献副本，供后续内部复核。
        """
        request = self._get_request(request_id)
        partner_id = request["partner_id"]
        batch_key = request["batch_key"]
        current_auth = set(self.effective_authorization(partner_id))
        requested = list(request["requested_subzones"])
        delivered_subzones = sorted(code for code in requested if code in current_auth)
        denied_subzones = sorted(code for code in requested if code not in current_auth)

        grids = self.get_grid_subzone_stats(batch_key)
        name_map = self._subzone_name_map()
        package_rows: list[dict[str, Any]] = []
        boundary_notes: list[dict[str, Any]] = []
        delivered_grid_set: set[str] = set()

        for code in denied_subzones:
            boundary_notes.append(self._boundary_note(
                "SUBZONE_NOT_AUTHORIZED",
                f"子区{code}不在合作方当前授权范围内，该子区本次不出数据",
                level="subzone", target=code))

        for grid in grids:
            contrib_map = {item["subzone_code"]: item for item in grid["subzone_contrib"]}
            withheld = [
                {"subzone_code": code,
                 "subzone_name": name_map.get(code, code),
                 "point_count": item["point_count"]}
                for code, item in sorted(contrib_map.items())
                if code not in delivered_subzones
            ]
            for code in delivered_subzones:
                item = contrib_map.get(code)
                if item is None:
                    continue
                raw_row = {
                    "batch_key": batch_key,
                    "geohash": grid["geohash"],
                    "subzone_code": code,
                    "subzone_name": item["subzone_name"],
                    "point_count": item["point_count"],
                    "cross_boundary_grid": grid["cross_boundary"],
                    "window_scope": request["windows"],
                }
                package_rows.append(self._sanitize_package_row(raw_row))
                delivered_grid_set.add(grid["geohash"])
            if withheld and grid["cross_boundary"]:
                boundary_notes.append(self._boundary_note(
                    "GRID_PARTIAL_WITHHELD",
                    f"网格{grid['geohash']}为跨界格，未授权子区贡献已截留，仅交付授权子区部分",
                    level="grid", target=grid["geohash"], withheld=withheld))
            elif withheld and not grid["cross_boundary"]:
                boundary_notes.append(self._boundary_note(
                    "GRID_OUT_OF_SCOPE",
                    f"网格{grid['geohash']}完全位于未授权子区，整格不出数据",
                    level="grid", target=grid["geohash"], withheld=withheld))

        delivered_codes_with_data = {row["subzone_code"] for row in package_rows}
        for code in delivered_subzones:
            if code not in delivered_codes_with_data:
                boundary_notes.append(self._boundary_note(
                    "SUBZONE_NO_DATA",
                    f"子区{code}在本批次内无落点观测，授权有效但无统计数据可交付",
                    level="subzone", target=code))

        package_id = f"PKG-{request_id}"
        package_record = {
            "package_id": package_id,
            "request_id": request_id,
            "partner_id": partner_id,
            "batch_key": batch_key,
            "requested_subzones": requested,
            "authorization_at_request": request["authorization_at_request"],
            "authorization_used_at_delivery": sorted(current_auth),
            "delivered_subzones": delivered_subzones,
            "denied_subzones": denied_subzones,
            "windows": request["windows"],
            "rows": package_rows,
            "boundary_notes": boundary_notes,
            "row_count": len(package_rows),
            "grid_count": len(delivered_grid_set),
            "delivered_at": time.time(),
        }
        # 外发包：仅匿名统计行 + 边界说明。
        package_path = self._data_dir("region_delivery") / "packages" / f"{package_id}.jsonl"
        self._write_state_absolute(package_path, [package_record])
        # 内部复核副本：保留完整网格子区贡献（含未授权子区点数与点位引用），不对外。
        internal_record = {
            "package_id": package_id,
            "request_id": request_id,
            "partner_id": partner_id,
            "batch_key": batch_key,
            "delivered_subzones": delivered_subzones,
            "full_grid_subzone_stats": grids,
            "package_manifest": {k: v for k, v in package_record.items() if k != "rows"},
            "archived_at": time.time(),
        }
        internal_path = self._data_dir("region_delivery") / "internal" / f"{package_id}_internal.jsonl"
        self._write_state_absolute(internal_path, [internal_record])

        # 登记交付任务并流转到已交付。
        entry, _ = self.create_entry({
            "交付编号": f"DEL-{request_id}",
            "合作方编号": partner_id,
            "请求编号": request_id,
            "授权子区": "、".join(delivered_subzones) or "无",
            "网格数量": str(len(delivered_grid_set)),
            "匿名行数": str(len(package_rows)),
            "窗口范围": "、".join(request["windows"]) or "全量窗口",
            "交付水位": f"已交付{len(package_rows)}行/{len(delivered_subzones)}子区",
        })
        if entry:
            self.run_action(entry["id"], "确认交付")

        requests = self._load_state("requests.jsonl")
        for row in requests:
            if row["request_id"] == request_id:
                row["status"] = "已交付"
                row["delivered_package_id"] = package_id
                row["actual_delivered_subzones"] = delivered_subzones
                row["delivered_at"] = package_record["delivered_at"]
        self._save_state("requests.jsonl", requests)

        return package_record

    def get_package(self, request_id: str) -> dict[str, Any]:
        package_id = f"PKG-{request_id}"
        path = self._data_dir("region_delivery") / "packages" / f"{package_id}.jsonl"
        rows = self._read_jsonl(path)
        if not rows:
            raise KeyError(f"请求 {request_id} 的交付包不存在")
        return rows[0]

    # ------------------------------------------------------------------
    # 审校视角：外部共享观测归属 vs 内部完整复核
    # ------------------------------------------------------------------

    def review_package(self, request_id: str, viewer_id: str,
                       full_grid: bool = False) -> dict[str, Any]:
        """外部审校员查看交付包：只能看到自身授权子区内的共享观测归属。"""
        package = self.get_package(request_id)
        viewer_auth = set(self.effective_authorization(viewer_id))
        notes: list[dict[str, Any]] = []
        visible_rows = [
            row for row in package["rows"] if row["subzone_code"] in viewer_auth
        ]
        hidden_subzones = sorted({
            row["subzone_code"] for row in package["rows"] if row["subzone_code"] not in viewer_auth
        })
        for code in hidden_subzones:
            notes.append(self._boundary_note(
                "REVIEW_SCOPE_WITHHELD",
                f"审校员{viewer_id}未获子区{code}授权，对应共享观测归属不予展示",
                level="subzone", target=code))
        if full_grid:
            notes.append(self._boundary_note(
                "FULL_GRID_DETAIL_WITHHELD",
                "整格明细涉及未授权子区贡献，按边界约定不出数据，仅返回授权子区匿名统计",
                level="grid"))
        if not visible_rows:
            notes.append(self._boundary_note(
                "NO_AUTHORIZED_OBSERVATION",
                f"审校员{viewer_id}对该包不持有任何授权子区，本次无可见数据",
                level="package", target=request_id))
        return {
            "request_id": request_id,
            "package_id": package["package_id"],
            "viewer_id": viewer_id,
            "viewer_authorized_subzones": sorted(viewer_auth),
            "rows": visible_rows,
            "row_count": len(visible_rows),
            "boundary_notes": notes,
        }

    def internal_review_package(self, request_id: str) -> dict[str, Any]:
        """内部复核：返回已交付包的完整子区贡献副本（含未授权部分的点数与点位引用）。"""
        package_id = f"PKG-{request_id}"
        path = self._data_dir("region_delivery") / "internal" / f"{package_id}_internal.jsonl"
        rows = self._read_jsonl(path)
        if not rows:
            raise KeyError(f"请求 {request_id} 的内部复核副本不存在")
        return rows[0]

    # ------------------------------------------------------------------
    # 可领取/可重领范围：始终以当前有效授权为准
    # ------------------------------------------------------------------

    def _delivered_windows_by_subzone(self, partner_id: str) -> dict[str, set[str]]:
        """按实际交付了数据行的子区统计已领取窗口；授权但无数据的子区不计领取。"""
        delivered: dict[str, set[str]] = {}
        for request in self._load_state("requests.jsonl"):
            if request["partner_id"] != partner_id or request.get("status") != "已交付":
                continue
            package = self.get_package(request["request_id"])
            delivered_codes = {row["subzone_code"] for row in package["rows"]}
            for code in delivered_codes:
                delivered.setdefault(code, set()).update(request.get("windows", []))
        return delivered

    def get_claimable_scope(self, partner_id: str, windows: list[str]) -> dict[str, Any]:
        """当前授权内、尚未领取过的窗口范围（新下载只能落在该范围内）。"""
        current_auth = self.effective_authorization(partner_id)
        delivered = self._delivered_windows_by_subzone(partner_id)
        subzones = []
        for code in current_auth:
            taken = sorted(delivered.get(code, set()))
            available = sorted(window for window in windows if window not in delivered.get(code, set()))
            subzones.append({
                "subzone_code": code,
                "subzone_name": self._subzone_name_map().get(code, code),
                "status": "authorized",
                "delivered_windows": taken,
                "available_windows": available,
            })
        return {"partner_id": partner_id, "windows": list(windows), "subzones": subzones}

    def get_reclaimable_scope(self, partner_id: str, windows: list[str]) -> dict[str, Any]:
        """授权更正后的重领视图：含当前授权可领窗口，及被收窄子区的阻断说明。"""
        current_auth = set(self.effective_authorization(partner_id))
        delivered = self._delivered_windows_by_subzone(partner_id)
        touched = set(current_auth) | set(delivered)
        subzones = []
        for code in sorted(touched):
            taken = sorted(delivered.get(code, set()))
            if code in current_auth:
                available = sorted(window for window in windows if window not in delivered.get(code, set()))
                subzones.append({
                    "subzone_code": code,
                    "subzone_name": self._subzone_name_map().get(code, code),
                    "status": "authorized",
                    "delivered_windows": taken,
                    "reclaimable_windows": available,
                })
            else:
                subzones.append({
                    "subzone_code": code,
                    "subzone_name": self._subzone_name_map().get(code, code),
                    "status": "revoked",
                    "delivered_windows": taken,
                    "reclaimable_windows": [],
                    "blocked_note": f"子区{code}授权已收窄，历史交付留存内部复核，不再支持新下载或重领",
                })
        return {"partner_id": partner_id, "windows": list(windows), "subzones": subzones}

    # ------------------------------------------------------------------
    # 交付包匿名化自检
    # ------------------------------------------------------------------

    def assert_package_is_anonymous(self, request_id: str) -> dict[str, Any]:
        """递归检查外发包不含终端标识或可还原单条轨迹的字段。"""
        package = self.get_package(request_id)

        def scan(obj: Any, path: str = "") -> list[str]:
            leaks = []
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key in FORBIDDEN_PACKAGE_FIELDS:
                        leaks.append(f"{path}.{key}" if path else key)
                    leaks.extend(scan(value, f"{path}.{key}" if path else key))
            elif isinstance(obj, list):
                for idx, value in enumerate(obj):
                    leaks.extend(scan(value, f"{path}[{idx}]"))
            return leaks

        leaks = scan(package.get("rows", []))
        return {"request_id": request_id, "anonymous": not leaks, "leaked_fields": leaks}
