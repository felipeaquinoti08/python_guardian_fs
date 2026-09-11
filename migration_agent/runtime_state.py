"""Estado em memória compartilhado entre o poller/peer-listener e a UI
local de status (issue #112: aba de configuração/conexão)."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Deque, List, Optional


@dataclass
class ActivityEntry:
    timestamp: float
    message: str


@dataclass
class GuardianConnectionStatus:
    """Status da conexão de long-poll com o Guardian -- atualizado pelo
    AgentPoller a cada ciclo (ver poller.py::_run_one_cycle)."""

    connected: bool = False
    last_success: Optional[float] = None
    last_error: Optional[str] = None


@dataclass
class PeerConnectionStatus:
    """Status de uma transferência agent<->agent em andamento (só existe
    enquanto há uma ação de verdade acontecendo -- fora disso, `active` é
    False). Atualizado pelo AgentPoller (lado que envia, push_to_agent) e
    pelo peer_listener (lado que recebe, receive_from_agent)."""

    active: bool = False
    label: str = ""
    started_at: Optional[float] = None


class RuntimeState:
    def __init__(self, max_entries: int = 100):
        self._lock = threading.Lock()
        self._entries: Deque[ActivityEntry] = deque(maxlen=max_entries)
        self._guardian_status = GuardianConnectionStatus()
        self._peer_status = PeerConnectionStatus()

    def record(self, message: str) -> None:
        with self._lock:
            self._entries.append(ActivityEntry(timestamp=time.time(), message=message))

    def recent(self) -> List[ActivityEntry]:
        with self._lock:
            return list(self._entries)

    def set_guardian_status(self, connected: bool, error: Optional[str] = None) -> None:
        with self._lock:
            if connected:
                self._guardian_status = GuardianConnectionStatus(connected=True, last_success=time.time())
            else:
                self._guardian_status = replace(self._guardian_status, connected=False, last_error=error)

    def guardian_status(self) -> GuardianConnectionStatus:
        with self._lock:
            return replace(self._guardian_status)

    def set_peer_status(self, label: Optional[str]) -> None:
        with self._lock:
            self._peer_status = PeerConnectionStatus(active=True, label=label, started_at=time.time()) if label else PeerConnectionStatus()

    def peer_status(self) -> PeerConnectionStatus:
        with self._lock:
            return replace(self._peer_status)
