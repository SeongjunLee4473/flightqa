"""설정 화면 (CLAUDE.md §6.3-4).

임계값 편집(단위·근거 툴팁), 프리셋, exiftool 경로, 테마, 언어, 캐시.
"""

from __future__ import annotations

import re
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from flightqa.config.schema import Config, default_config_dict, load_config
from flightqa.gui.state import AppState, presets_dir
from flightqa.gui.widgets.common import Banner, button, home_dir, label, section

SECTIONS = [
    ("flight", "비행 궤적·고도"),
    ("overlap", "중복도"),
    ("survey_area", "조사 구역 추정 · GIS 내보내기"),
    ("metashape", "후처리 품질 (Metashape 보고서)"),
    ("integrity", "캡처 무결성"),
    ("exposure", "노출·포화"),
    ("irradiance", "조도·태양"),
    ("panel", "캘리브레이션 패널"),
    ("rtk", "RTK (M3M)"),
    ("discovery", "발견·비행 분리"),
    ("judgement", "종합 판정"),
    ("report", "보고서 (배경지도)"),
]


def _comments() -> dict[str, str]:
    """default.toml 의 `key = value  # 주석` 을 툴팁으로."""
    text = resources.files("flightqa.config").joinpath("default.toml").read_text(encoding="utf-8")
    out: dict[str, str] = {}
    sec = ""
    for line in text.splitlines():
        m = re.match(r"^\[([^\]]+)\]", line)
        if m:
            sec = m.group(1)
            continue
        m = re.match(r"^([a-z_]+)\s*=\s*[^#]*#\s*(.+)$", line)
        if m:
            out[f"{sec}.{m.group(1)}"] = m.group(2).strip()
    return out


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    lang_changed = Signal(str)
    config_changed = Signal()

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.comments = _comments()
        self.fields: dict[str, QWidget] = {}
        self.defaults = default_config_dict()
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(12)
        root.addWidget(label("설정", "title"))
        self.banner = Banner()
        root.addWidget(self.banner)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._thresholds_tab(), "임계값")
        self.tabs.addTab(self._app_tab(), "앱")
        self.load_preset(self.state.preset_path or None)

    # ---- 임계값 탭
    def _thresholds_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("프리셋 이름 (예: 광릉_잣나무_100m)")
        row.addWidget(label("프리셋", "muted"))
        row.addWidget(self.preset_name, 1)
        row.addWidget(button("저장", variant="primary", on_click=self.save_preset))
        row.addWidget(button("불러오기…", on_click=self._load_dialog))
        row.addWidget(button("내보내기…", on_click=self._export_dialog))
        row.addWidget(button("기본값", variant="ghost", on_click=lambda: self.load_preset(None)))
        v.addLayout(row)
        self.preset_lb = label("현재: 기본값", "muted")
        v.addWidget(self.preset_lb)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        for key, title in SECTIONS:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(_left())
            for name, val in self.defaults.get(key, {}).items():
                fw = self._field(val)
                if fw is None:
                    continue
                lab = label(name)
                tip = self.comments.get(f"{key}.{name}", "")
                row_w = QWidget()
                rh = QHBoxLayout(row_w)
                rh.setContentsMargins(0, 0, 0, 0)
                rh.setSpacing(10)
                if not isinstance(fw, QLineEdit):
                    fw.setMaximumWidth(180)
                    fw.setMinimumWidth(120)
                else:
                    fw.setMaximumWidth(360)
                rh.addWidget(fw)
                desc = label(tip, "muted", wrap=True)
                rh.addWidget(desc, 1)
                if tip:
                    lab.setToolTip(tip)
                    fw.setToolTip(tip)
                form.addRow(lab, row_w)
                self.fields[f"{key}.{name}"] = fw
            iv.addWidget(box)
        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)
        return w

    def _field(self, val: Any) -> QWidget | None:
        if isinstance(val, bool):
            cb = QCheckBox()
            cb.setChecked(val)
            cb.stateChanged.connect(lambda _s: self.config_changed.emit())
            return cb
        if isinstance(val, int):
            sb = QSpinBox()
            sb.setRange(0, 2_000_000_000)  # raster_max_pixels 같은 큰 정수도 잘리지 않게
            sb.setValue(val)
            sb.valueChanged.connect(lambda _v: self.config_changed.emit())
            return sb
        if isinstance(val, float):
            ds = QDoubleSpinBox()
            ds.setRange(-100000.0, 1_000_000.0)
            ds.setDecimals(3 if val < 1 else 1)
            ds.setSingleStep(0.1 if val < 1 else 1.0)
            ds.setValue(val)
            ds.valueChanged.connect(lambda _v: self.config_changed.emit())
            return ds
        if isinstance(val, list):
            le = QLineEdit(", ".join(str(x) for x in val))
            le.setToolTip("쉼표로 구분")
            le.editingFinished.connect(self.config_changed.emit)
            return le
        if isinstance(val, str):
            le = QLineEdit(val)
            le.editingFinished.connect(self.config_changed.emit)
            return le
        return None

    def _read_fields(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, w in self.fields.items():
            sec, name = key.split(".", 1)
            default = self.defaults[sec][name]
            v: Any
            if isinstance(w, QCheckBox):
                v = w.isChecked()
            elif isinstance(w, QDoubleSpinBox):
                v = float(w.value())
            elif isinstance(w, QSpinBox):
                v = int(w.value())
            elif isinstance(w, QLineEdit):
                t = w.text().strip()
                if isinstance(default, list):
                    items = [x.strip() for x in t.split(",") if x.strip()]
                    v = [int(x) if x.lstrip("-").isdigit() else x for x in items]
                elif isinstance(default, str) and default == "auto" and t and t != "auto":
                    try:
                        v = float(t)
                    except ValueError:
                        v = t
                else:
                    v = t
            else:
                continue
            data.setdefault(sec, {})[name] = v
        return data

    def _write_fields(self, cfg: dict[str, Any]) -> None:
        for key, w in self.fields.items():
            sec, name = key.split(".", 1)
            v = cfg.get(sec, {}).get(name, self.defaults[sec][name])
            w.blockSignals(True)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(v))
            elif isinstance(w, QSpinBox):
                w.setValue(int(v))
            elif isinstance(w, QLineEdit):
                w.setText(", ".join(str(x) for x in v) if isinstance(v, list) else str(v))
            w.blockSignals(False)

    def current_config(self) -> Config:
        """현재 필드 값으로 Config. 잘못된 값이면 배너에 알리고 기본값을 돌려준다."""
        data = default_config_dict()
        for sec, vals in self._read_fields().items():
            data.setdefault(sec, {}).update(vals)
        try:
            return Config.model_validate(data)
        except Exception as e:  # noqa: BLE001 - 사용자 입력 오류를 배너로
            self.banner.show_message("fail", f"설정 값 오류: {e}")
            return load_config()

    def to_toml(self) -> str:
        lines: list[str] = ["# FlightQA 프리셋 (default.toml 위에 덮어씀)"]
        for sec, vals in self._read_fields().items():
            lines.append(f"\n[{sec}]")
            for k, v in vals.items():
                lines.append(f"{k} = {_toml_value(v)}")
        return "\n".join(lines) + "\n"

    def save_preset(self) -> None:
        name = self.preset_name.text().strip() or "preset"
        safe = re.sub(r"[^\w\-가-힣]+", "_", name)
        path = presets_dir() / f"{safe}.toml"
        path.write_text(self.to_toml(), encoding="utf-8")
        self.state.preset_path = str(path)
        self.preset_lb.setText(f"현재: {path.stem}")
        self.banner.show_message("pass", f"프리셋 저장: {path}")
        self.config_changed.emit()

    def load_preset(self, path: str | None) -> None:
        if not path or not Path(path).exists():
            self._write_fields(self.defaults)
            self.preset_lb.setText("현재: 기본값")
            self.preset_name.setText("")
            self.state.preset_path = ""
            return
        try:
            with Path(path).open("rb") as fh:
                data = tomllib.load(fh)
            merged = default_config_dict()
            for sec, vals in data.items():
                if isinstance(vals, dict):
                    merged.setdefault(sec, {}).update(vals)
            Config.model_validate(merged)
        except Exception as e:  # noqa: BLE001 - 깨진 프리셋
            self.banner.show_message("fail", f"프리셋을 읽을 수 없습니다: {e}")
            return
        self._write_fields(merged)
        self.preset_lb.setText(f"현재: {Path(path).stem}")
        self.preset_name.setText(Path(path).stem)
        self.state.preset_path = path
        self.config_changed.emit()

    def _load_dialog(self) -> None:
        f, _ = QFileDialog.getOpenFileName(
            self, "프리셋 불러오기", str(presets_dir()), "TOML (*.toml)"
        )
        if f:
            self.load_preset(f)

    def _export_dialog(self) -> None:
        f, _ = QFileDialog.getSaveFileName(
            self, "프리셋 내보내기", str(presets_dir() / "preset.toml"), "TOML (*.toml)"
        )
        if f:
            Path(f).write_text(self.to_toml(), encoding="utf-8")
            self.banner.show_message("pass", f"내보냄: {f}")

    # ---- 앱 탭
    def _app_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        form.setLabelAlignment(_left())
        self.theme = QComboBox()
        self.theme.addItem("시스템 설정 따르기", "system")
        self.theme.addItem("라이트", "light")
        self.theme.addItem("다크", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.state.theme)))
        self.theme.currentIndexChanged.connect(
            lambda _i: self.theme_changed.emit(str(self.theme.currentData()))
        )
        form.addRow(label("테마"), self.theme)
        self.lang = QComboBox()
        self.lang.addItem("한국어", "ko")
        self.lang.addItem("English", "en")
        self.lang.setCurrentIndex(max(0, self.lang.findData(self.state.lang)))
        self.lang.currentIndexChanged.connect(
            lambda _i: self.lang_changed.emit(str(self.lang.currentData()))
        )
        form.addRow(label("보고서 언어"), self.lang)

        ex = QHBoxLayout()
        self.exiftool = QLineEdit(self.state.exiftool_path)
        self.exiftool.setPlaceholderText("비우면 번들본 → PATH 순으로 찾음")
        self.exiftool.editingFinished.connect(self._exiftool_changed)
        ex.addWidget(self.exiftool, 1)
        ex.addWidget(button("찾기…", on_click=self._pick_exiftool))
        ex.addWidget(button("확인", on_click=self._test_exiftool))
        exw = QWidget()
        exw.setLayout(ex)
        form.addRow(label("exiftool 경로"), exw)
        self.exiftool_lb = label("", "muted")
        form.addRow(QWidget(), self.exiftool_lb)

        self.fast = QCheckBox("초고속 모드 (포화 분석 4×4 샘플링)")
        self.fast.setChecked(self.state.fast_mode)
        self.fast.stateChanged.connect(
            lambda _s: setattr(self.state, "fast_mode", self.fast.isChecked())
        )
        form.addRow(label("분석"), self.fast)
        self.cache = QCheckBox("증분 캐시 사용 (같은 폴더 재분석 시 변경 파일만 처리)")
        self.cache.setChecked(self.state.use_cache)
        self.cache.stateChanged.connect(
            lambda _s: setattr(self.state, "use_cache", self.cache.isChecked())
        )
        form.addRow(QWidget(), self.cache)

        out = QHBoxLayout()
        self.out_mode = QComboBox()
        self.out_mode.addItem("원본 폴더 안 (flightqa_out)", "inside")
        self.out_mode.addItem("지정 폴더 아래", "custom")
        self.out_mode.setCurrentIndex(max(0, self.out_mode.findData(self.state.out_mode)))
        self.out_root = QLineEdit(self.state.out_root)
        self.out_root.setPlaceholderText("결과 상위 폴더")
        self.out_mode.currentIndexChanged.connect(self._out_changed)
        self.out_root.editingFinished.connect(self._out_changed)
        out.addWidget(self.out_mode)
        out.addWidget(self.out_root, 1)
        out.addWidget(button("찾기…", on_click=self._pick_out))
        ow = QWidget()
        ow.setLayout(out)
        form.addRow(label("결과 위치"), ow)
        v.addLayout(form)

        v.addWidget(section("캐시"))
        cache_row = QHBoxLayout()
        self.cache_lb = label("", "muted")
        cache_row.addWidget(self.cache_lb, 1)
        cache_row.addWidget(
            button("캐시 비우기", variant="danger", icon_name="trash", on_click=self._clear_cache)
        )
        v.addLayout(cache_row)
        v.addStretch(1)
        self.refresh_cache_info()
        return w

    def _exiftool_changed(self) -> None:
        self.state.exiftool_path = self.exiftool.text().strip()

    def _pick_exiftool(self) -> None:
        f, _ = QFileDialog.getOpenFileName(
            self, "exiftool 실행 파일", home_dir(), "exiftool (exiftool*)"
        )
        if f:
            self.exiftool.setText(f)
            self._exiftool_changed()

    def _test_exiftool(self) -> None:
        from flightqa.core.exiftool import ExifToolNotFoundError, exiftool_version, find_exiftool

        self._exiftool_changed()
        try:
            exe = find_exiftool(self.exiftool.text().strip() or None)
            self.exiftool_lb.setText(f"exiftool {exiftool_version(exe)} — {exe}")
        except (ExifToolNotFoundError, OSError) as e:
            self.exiftool_lb.setText(f"찾을 수 없음: {e}")

    def _out_changed(self) -> None:
        self.state.out_mode = str(self.out_mode.currentData())
        self.state.out_root = self.out_root.text().strip()

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "결과 상위 폴더", home_dir())
        if d:
            self.out_root.setText(d)
            self.out_mode.setCurrentIndex(self.out_mode.findData("custom"))
            self._out_changed()

    def refresh_cache_info(self) -> None:
        p = load_config().cache.resolve_path()
        size = p.stat().st_size / 1e6 if p.exists() else 0.0
        self.cache_lb.setText(f"{p}  ·  {size:.1f} MB")

    def _clear_cache(self) -> None:
        from flightqa.core.cache import MetadataCache

        p = load_config().cache.resolve_path()
        try:
            with MetadataCache(p, None) as c:
                c.clear()
            self.banner.show_message("pass", "캐시를 비웠습니다.")
        except Exception as e:  # noqa: BLE001
            self.banner.show_message("fail", f"캐시를 비우지 못했습니다: {e}")
        self.refresh_cache_info()


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _left() -> Any:
    from PySide6.QtCore import Qt

    return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
