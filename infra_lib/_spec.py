from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VMSpec:
    cpu: int = 2
    ram_gb: int = 4
    storage_gb: int = 30
    # reserved for future use
    # gpu: bool = False
    # spot: bool = False
    # min_network_gbps: float = None


@dataclass
class ResolvedSize:
    name: str
    cpu: int
    ram_gb: float
    price_per_hour: Optional[float] = None

    def __str__(self):
        price = f"~${self.price_per_hour:.4f}/hr" if self.price_per_hour else "price unknown"
        return f"{self.name} ({self.cpu} vCPU, {self.ram_gb}GB RAM, {price})"
