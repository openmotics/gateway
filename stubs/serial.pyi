import io
from typing import Any, Optional


class Serial(io.RawIOBase):
    def  __init__(self, port: str, baudrate=9600, **Any): ...

    @property
    def timeout(self) -> Optional[int]: ...

    @timeout.setter
    def timeout(self, timeout: Optional[int]): ...

    @property
    def write_timeout(self) -> Optional[int]: ...

    @write_timeout.setter
    def write_timeout(self, timeout: Optional[int]): ...

    @property
    def break_condition(self) -> bool: ...

    @break_condition.setter
    def break_condition(self, break_condition: bool): ...

    @property
    def in_waiting(self) -> int: ...

    def inWaiting(self) -> int: ...

    def flushInput(self) -> None: ...

    # Serial doesn't return Optional[bytes] like RawIOBase
    def read(self, size=1) -> bytes: ...
