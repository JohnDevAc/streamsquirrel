from pydantic import BaseModel, Field
from typing import Optional, List

class NDISource(BaseModel):
    name: str

class SlotConfig(BaseModel):
    slot_id: int = Field(ge=1, le=4)
    ndi_source_name: Optional[str] = None
    aes67_stream_name: str
    mcast_ip: str
    mcast_port: int

class SystemConfig(BaseModel):
    slots: List[SlotConfig]

class Status(BaseModel):
    running: bool
    message: str = ""
