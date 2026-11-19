"""Retrato da máquina (opt-in, só leitura, sem rede e sem subprocesso): CPU e discos fixos.

CPU: ``kernel32.GetSystemTimes`` com delta entre duas leituras (a primeira passada
publica ``cpu_pct`` nulo). Discos: ``GetLogicalDrives`` + ``GetDriveTypeW`` (só
DRIVE_FIXED) + ``GetDiskFreeSpaceExW``. A saída tem só números e a letra da
unidade (``C:``); nenhum rótulo de volume, caminho ou texto do sistema.
"""
import ctypes
import re

from . import agora

CHAVE = "maquina"
DRIVE_FIXED = 3
ERRO_WINDOWS = "só funciona no Windows"
ERRO_LER = "Máquina: sem leitura"
UNIDADE_OK = re.compile(r"^[A-Z]:$")

# Estado entre passadas (leitura anterior de GetSystemTimes) quando o ctx não injeta o seu.
_ESTADO = {}


class SistemaWindows:
    """Camada fina sobre kernel32; criada só quando a fonte roda (não no import)."""

    def __init__(self):
        from ctypes import wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetSystemTimes.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)] * 3
        k.GetSystemTimes.restype = wintypes.BOOL
        k.GetLogicalDrives.argtypes = []
        k.GetLogicalDrives.restype = wintypes.DWORD
        k.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        k.GetDriveTypeW.restype = wintypes.UINT
        k.GetDiskFreeSpaceExW.argtypes = [wintypes.LPCWSTR] + [ctypes.POINTER(ctypes.c_ulonglong)] * 3
        k.GetDiskFreeSpaceExW.restype = wintypes.BOOL
        self._k = k

    def _falha(self):
        return ctypes.WinError(ctypes.get_last_error())

    def tempos_cpu(self):
        """(idle, kernel, user) em unidades de 100 ns; kernel já inclui idle."""
        idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        if not self._k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise self._falha()
        return idle.value, kernel.value, user.value

    def mascara_unidades(self):
        mascara = self._k.GetLogicalDrives()
        if not mascara:
            raise self._falha()
        return int(mascara)

    def tipo(self, letra):
        return int(self._k.GetDriveTypeW(letra + "\\"))

    def espaco(self, letra):
        """(livre_bytes, total_bytes)."""
        para_o_chamador, total, livre = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        if not self._k.GetDiskFreeSpaceExW(letra + "\\", ctypes.byref(para_o_chamador),
                                           ctypes.byref(total), ctypes.byref(livre)):
            raise self._falha()
        return livre.value, total.value


def _inteiro(valor):
    return isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0


def _cpu(sistema, estado, agora_s):
    """Devolve (cpu_pct, janela_s) e guarda a leitura atual como base da próxima."""
    idle, kernel, user = sistema.tempos_cpu()
    if not (_inteiro(idle) and _inteiro(kernel) and _inteiro(user)):
        raise ValueError("tempos")
    anterior = estado.get("cpu")
    estado["cpu"] = (agora_s, idle, kernel, user)
    if not anterior:
        return None, None
    antes_s, antes_idle, antes_kernel, antes_user = anterior
    d_idle, d_kernel, d_user = idle - antes_idle, kernel - antes_kernel, user - antes_user
    janela = agora_s - antes_s
    total = d_kernel + d_user
    if d_idle < 0 or d_kernel < 0 or d_user < 0 or total <= 0 or d_idle > total or janela <= 0:
        return None, None
    pct = (1 - d_idle / total) * 100
    return round(min(100.0, max(0.0, pct)), 1), round(janela, 3)


def _discos(sistema):
    mascara = sistema.mascara_unidades()
    discos, sem_leitura = [], 0
    for indice in range(26):
        if not mascara >> indice & 1:
            continue
        letra = chr(65 + indice) + ":"
        if not UNIDADE_OK.match(letra):
            continue
        try:
            if sistema.tipo(letra) != DRIVE_FIXED:
                continue
            livre, total = sistema.espaco(letra)
        except (OSError, ValueError, TypeError, ctypes.ArgumentError):
            sem_leitura += 1
            continue
        if not (_inteiro(livre) and _inteiro(total)) or total <= 0 or livre > total:
            sem_leitura += 1
            continue
        discos.append({"unidade": letra, "total_bytes": total, "livre_bytes": livre,
                       "usado_pct": round((total - livre) / total * 100, 1)})
    return discos, sem_leitura


def ler(ctx):
    sistema = ctx.get('maquina_sistema')
    if sistema is None:
        if getattr(ctypes, 'windll', None) is None:
            return {"erro": ERRO_WINDOWS}
        try:
            sistema = SistemaWindows()
        except (OSError, AttributeError):
            return {"erro": ERRO_LER}
    estado = ctx.get('maquina_estado')
    if estado is None:
        estado = _ESTADO
    agora_s = agora(ctx)
    try:
        cpu_pct, janela = _cpu(sistema, estado, agora_s)
        discos, sem_leitura = _discos(sistema)
    except (OSError, ValueError, TypeError, OverflowError, ctypes.ArgumentError):
        return {"erro": ERRO_LER}
    return {CHAVE: {"cpu_pct": cpu_pct, "cpu_janela_s": janela, "discos": discos,
                    "discos_sem_leitura": sem_leitura, "lido_em_epoch": agora_s}}
