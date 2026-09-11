"""Estado em memória compartilhado entre o poller/peer-listener e a UI
local de status (issue #112: aba de configuração/conexão)."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Deque, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ActivityEntry:
    timestamp: float
    message: str


def classify_activity(message: str) -> str:
    """Heurística simples (baseada nas mensagens que o proprio agent gera
    -- ver agent_runtime.py/poller.py/status_server.py, todo lugar que
    chama state.record) usada tanto pra colorir a linha na tabela da UI
    local (status_server.py) quanto pra decidir o "status" ao reportar um
    evento operacional pro histórico de 30 dias do Guardian
    (agent_runtime.py::_report_event_async, issue #113)."""
    lowered = message.lower()
    if any(term in lowered for term in ("falh", "erro", "inválid", "invalid", "inacessível", "inacessivel")):
        return "error"
    if any(term in lowered for term in ("sucesso", "concluíd", "concluid", "pareado", "disponível", "disponivel")):
        return "success"
    return "info"


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
    def __init__(self, max_entries: int = 100, on_record: Optional[Callable[[str], None]] = None):
        self._lock = threading.Lock()
        self._entries: Deque[ActivityEntry] = deque(maxlen=max_entries)
        self._guardian_status = GuardianConnectionStatus()
        self._peer_status = PeerConnectionStatus()
        # Issue #113: hook opcional pra reportar o evento pro histórico de
        # 30 dias do Guardian (ver agent_runtime.py::_report_event_async).
        # Chamado FORA do lock -- é o próprio callback quem decide se isso
        # vira uma chamada de rede em thread separada; um callback lento ou
        # que lança exceção nunca pode comprometer o registro local (que já
        # aconteceu antes disso).
        self._on_record = on_record

    def record(self, message: str) -> None:
        with self._lock:
            self._entries.append(ActivityEntry(timestamp=time.time(), message=message))
        if self._on_record is not None:
            try:
                self._on_record(message)
            except Exception:
                logger.debug("on_record callback falhou (ignorado)", exc_info=True)

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
