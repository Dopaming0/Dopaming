"""Load and validate config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


@dataclass
class CoupangConf:
    vendor_id: str
    access_key: str
    secret_key: str


@dataclass
class TelegramConf:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class SupplierConf:
    key: str
    name: str
    columns: list[dict] = field(default_factory=list)


@dataclass
class Config:
    coupang: CoupangConf
    telegram: TelegramConf
    suppliers: dict[str, SupplierConf]
    batches: list[str]
    timezone: str = "Asia/Seoul"
    database: str = "data/dopaming.db"
    order_files_dir: str = "data/orders"
    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self._resolve(self.database)

    @property
    def orders_dir(self) -> Path:
        return self._resolve(self.order_files_dir)

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else self.base_dir / path


def load_config(path: str | os.PathLike = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"설정 파일이 없습니다: {path} — config.example.yaml 을 복사해 만들어 주세요."
        )
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    coupang = CoupangConf(**{k: str(raw["coupang"][k]) for k in ("vendor_id", "access_key", "secret_key")})
    tg = raw.get("telegram") or {}
    telegram = TelegramConf(bot_token=str(tg.get("bot_token") or ""), chat_id=str(tg.get("chat_id") or ""))

    suppliers: dict[str, SupplierConf] = {}
    for key, sconf in (raw.get("suppliers") or {}).items():
        columns = ((sconf.get("order_sheet") or {}).get("columns")) or []
        suppliers[key] = SupplierConf(key=key, name=sconf.get("name", key), columns=columns)
    if not suppliers:
        raise ValueError("suppliers 설정이 비어 있습니다. 공급처를 1개 이상 정의하세요.")

    batches = [str(b) for b in (raw.get("batches") or ["09:00", "21:00"])]

    return Config(
        coupang=coupang,
        telegram=telegram,
        suppliers=suppliers,
        batches=batches,
        timezone=raw.get("timezone", "Asia/Seoul"),
        database=raw.get("database", "data/dopaming.db"),
        order_files_dir=raw.get("order_files_dir", "data/orders"),
        base_dir=path.resolve().parent,
    )
