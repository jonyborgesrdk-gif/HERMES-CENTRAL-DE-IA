"""Compatibilidade com a execução autônoma de `python backend.py` em cotas/.

Cópia mínima de lib/semjanela.py: o sidecar não inclui lib/ no sys.path.
O teste test_sem_janela verifica o mesmo contrato para os dois helpers.
"""
import os
import subprocess
from typing import TypedDict


class OpcoesSemJanela(TypedDict, total=False):
    creationflags: int


def sem_janela_kwargs() -> OpcoesSemJanela:
    """No Windows, não criar console; nos demais sistemas, preservar os defaults."""
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}
