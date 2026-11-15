"""Estado passivo da ponte; nunca acessa a sessão do WhatsApp."""
from datetime import datetime
import json
import os
from pathlib import Path

import re

from . import agora


def pid_vivo(pid):
    """Consulta o kernel, sem subprocesso, argv ou linha de comando."""
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            codigo = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(codigo))) and codigo.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False


def _data(valor):
    if not isinstance(valor, str):
        return None, None
    try:
        data = datetime.fromisoformat(valor.replace('Z', '+00:00'))
        # Sem fuso é ambíguo; anos extremos não são horários úteis do gateway.
        if data.tzinfo is None or data.year in (1, 9999):
            return None, None
        return data.isoformat(), data.timestamp()
    except (ValueError, OverflowError, OSError):
        return None, None


def ler(ctx):
    caminho = Path(ctx.get('whatsapp_path') or
                   Path(os.environ.get('LOCALAPPDATA') or Path.home()) /
                   'hermes' / 'gateway_state.json')
    try:
        dado = json.loads(caminho.read_text(encoding='utf-8'))
        if not isinstance(dado, dict) or not isinstance(dado.get('platforms', {}), dict):
            raise ValueError('estrutura inválida')
        ponte = dado.get('platforms', {}).get('whatsapp')
        if ponte is None:
            return {'whatsapp': {'estado': 'ausente'}, 'erro': 'plataforma WhatsApp ausente'}
        if not isinstance(ponte, dict):
            raise ValueError('plataforma inválida')
    except FileNotFoundError:
        return {'whatsapp': {'estado': 'ausente'}, 'erro': 'estado do gateway ausente'}
    except (OSError, ValueError):
        return {'whatsapp': {'estado': 'erro'}, 'erro': 'estado do gateway inválido ou inacessível'}

    _, atualizado = _data(dado.get('updated_at'))
    hora_ponte, _ = _data(ponte.get('updated_at'))
    estado = ponte.get('state')
    if estado not in ('connected', 'disconnected', 'connecting', 'reconnecting',
                      'starting', 'stopped', 'error', 'failed', 'disabled', 'retrying'):
        estado = 'desconhecido'
    gateway = dado.get('gateway_state')
    if gateway not in ('running', 'starting', 'stopping', 'stopped', 'error', 'failed'):
        gateway = 'desconhecido'
    codigo = ponte.get('error_code')
    if not ((type(codigo) is int and 0 <= codigo <= 9999) or
            (isinstance(codigo, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_-]{0,63}|\d{1,4}', codigo))):
        codigo = None
    return {'whatsapp': {'estado': estado,
                        'needs_attention': ponte.get('needs_attention') is True or atualizado is None or hora_ponte is None,
                        'error_code': codigo,
                        'tem_mensagem_erro': isinstance(ponte.get('error_message'), str) and bool(ponte['error_message']),
                        'updated_at': hora_ponte,
                        'gateway_state': gateway,
                        'pid_vivo': ctx.get('pid_vivo', pid_vivo)(dado.get('pid')),
                        'idade_s': max(0, agora(ctx) - atualizado) if atualizado is not None else None}}
