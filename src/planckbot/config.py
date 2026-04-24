"""Global configuration with hardware auto-detection."""

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path


def _detect_gpu() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return f"cuda ({torch.cuda.get_device_name(0)})"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _get_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024**3), 1)
    except ImportError:
        # Fallback for Linux
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        return round(kb / (1024**2), 1)
        except FileNotFoundError:
            return 0.0
    return 0.0


def _default_data_dir() -> Path:
    """Absolute path to the PlanckBot data directory.

    `PLANCK_DATA_DIR` still wins (for tests, CI, alternative layouts). When
    unset we anchor to the package location so subprocesses whose cwd comes
    from their parent — Claude Code's MCP subprocesses, the systemd cron
    daemon, etc. — all read and write the same DB no matter where they were
    launched from. Until this fix the MCP spawned by a Claude Code session
    on /orquesta was writing triples to /orquesta/data/planckbot.db.
    """
    env = os.environ.get("PLANCK_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # src/planckbot/config.py → parents[2] is the repo root for editable
    # installs, which is the layout we ship.
    return Path(__file__).resolve().parents[2] / "data"


@dataclass
class PlanckBotConfig:
    # Paths
    data_dir: Path = field(default_factory=_default_data_dir)
    db_path: Path = field(default=None)

    # Hardware (auto-detected)
    device: str = field(default_factory=_detect_gpu)
    ram_gb: float = field(default_factory=_get_ram_gb)
    cpu_count: int = field(default_factory=lambda: os.cpu_count() or 1)

    # Model defaults
    default_base_model: str = "HuggingFaceTB/SmolLM2-135M-Instruct"
    default_lora_rank: int = 8
    default_learning_rate: float = 1e-4
    default_epochs: int = 3
    default_batch_size: int = 16
    default_max_seq_length: int = 512

    # Confidence thresholds
    filter_confidence: float = 0.90
    short_circuit_confidence: float = 0.95

    # Server
    host: str = "0.0.0.0"
    port: int = 8080

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.db_path is None:
            self.db_path = self.data_dir / "planckbot.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "checkpoints").mkdir(exist_ok=True)
        (self.data_dir / "exports").mkdir(exist_ok=True)

    @property
    def system_info(self) -> dict:
        return {
            "python": platform.python_version(),
            "platform": platform.system(),
            "cpu_count": self.cpu_count,
            "ram_gb": self.ram_gb,
            "device": self.device,
        }


# Global singleton
config = PlanckBotConfig()
