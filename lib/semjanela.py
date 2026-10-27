"""Opções de subprocesso que não abrem console quando o painel usa pythonw."""
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
