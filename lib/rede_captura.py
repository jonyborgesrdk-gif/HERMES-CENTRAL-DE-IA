"""Uso de rede por programa (E22): lógica e camada ETW do processo de captura.

Só biblioteca padrão. Não abre soquete, não cria subprocesso, não lê arquivo e não cria
diretório; o único arquivo que grava é o resumo (e o temporário dele), de forma atômica e com
até 16 KiB. Com detalhe opt-in, grava também rede-sessao.json (até 2 MiB) na mesma pasta
protegida; só o canal local o consome. Sem detalhe, PID, endereço e porta ficam na memória: o resumo leva só
números, booleanos, o `produto` da tabela fixa da E16 e valores de lista fixa. Nada deste
módulo escreve em stdout ou stderr. Todos os colaboradores do laço entram por argumento.
"""
import ctypes
from ctypes import wintypes
import json
import math
import ipaddress
import os
import struct
import tempfile
import threading
import time

try:
    from .fontes import presenca_processos
except ImportError:
    from fontes import presenca_processos

NOME_SESSAO = "painel-ias-rede"
NOME_MUTEX = "Global\\painel-ias-rede-captura"
NOME_EVENTO_PARAR = "Global\\painel-ias-rede-parar"
SDDL_ADMIN = "D:P(A;;GA;;;BA)(A;;GA;;;SY)"
ARQ_RESUMO = "rede-resumo.json"
SCHEMA = 1
PROVEDOR = "7DD42A49-5329-4832-8DFD-43D979153A88"
IDS = (10, 11, 26, 27, 42, 43, 58, 59)
IDS_ENVIO = (10, 26, 42, 58)
IDS_V6 = (26, 27, 58, 59)
IDS_UDP = (42, 43, 58, 59)
IDS_REMOTO_NO_SEGUNDO = (43, 59)
CICLO_S = 2.0
CICLOS_JANELA = 30
CICLOS_POR_GRAVACAO = 2
CICLOS_POR_MAPA = 5
ESPERA_FIO_S = 10.0
TETO_CPU_PCT = 5.0
AMOSTRAS_TETO = 3
PISO_RETOMADA_PCT = 2.5
OBSERVACAO_S = 10.0
ESPERAS_S = (60.0, 120.0, 300.0, 600.0)
ESTAVEL_S = 600.0
MAX_FLUXOS_CICLO = 20000
MAX_CORTADAS_CICLO = 5000
MAX_FLUXOS_JANELA = 50000
MAX_CACHE_CATEGORIAS = 20000
MAX_RESUMO = 16 * 1024
ARQ_SESSAO = 'rede-sessao.json'
MAX_DETALHE = 2 * 1024 * 1024
MAX_FLUXOS_DETALHE = 4096
MAX_PIDS_DETALHE = 8192
MARGEM_REUSO_PID_S = 2.0
MAX_BYTES = 10 ** 15
MAX_CPU_PCT = 6400.0
MAX_EVENTOS_S = 10 ** 7
CATEGORIAS = ("local", "privada", "tailnet", "internet")
OUTROS = "outros"
ESTADOS = ("ativo", "pausado_carga", "encerrado", "erro")
MOTIVOS = ("sessao", "provedor", "consumidor")

# Parâmetros da sessão ETW (LEVANTAMENTO da spec).
NIVEL = 4
PALAVRAS_CHAVE = 0x30
MODO_TEMPO_REAL = 0x100
WNODE_FLAG_TRACED_GUID = 0x20000
MODO_CONSUMO = 0x100 | 0x10000000
ATIVAR_PROVEDOR = 1
CONTROLE_CONSULTA = 0
CONTROLE_PARAR = 1
SEM_SESSAO = 4201
FILTRO_TIPO_ID = 0x80000200
FILTRO_IDS = struct.pack("<BBH%dH" % len(IDS), 1, 0, len(IDS), *IDS)
_BYTES_NOME = 1024 * 2
_CONSUMO_INVALIDO = (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF)
_MAPEADO_V4 = b"\x00" * 10 + b"\xff\xff"
_PRODUTOS_TABELA = frozenset(linha["produto"] for linha in presenca_processos.PRODUTOS)


# ---------------------------------------------------------------- estruturas ETW

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.ULONG), ("Data2", wintypes.USHORT), ("Data3", wintypes.USHORT),
                ("Data4", ctypes.c_ubyte * 8)]


class WNODE_HEADER(ctypes.Structure):
    _fields_ = [("BufferSize", wintypes.ULONG), ("ProviderId", wintypes.ULONG),
                ("HistoricalContext", ctypes.c_uint64), ("KernelHandle", ctypes.c_uint64),
                ("Guid", GUID), ("ClientContext", wintypes.ULONG), ("Flags", wintypes.ULONG)]


class EVENT_TRACE_PROPERTIES(ctypes.Structure):
    _fields_ = [("Wnode", WNODE_HEADER), ("BufferSize", wintypes.ULONG),
                ("MinimumBuffers", wintypes.ULONG), ("MaximumBuffers", wintypes.ULONG),
                ("MaximumFileSize", wintypes.ULONG), ("LogFileMode", wintypes.ULONG),
                ("FlushTimer", wintypes.ULONG), ("EnableFlags", wintypes.ULONG),
                ("AgeLimit", wintypes.LONG), ("NumberOfBuffers", wintypes.ULONG),
                ("FreeBuffers", wintypes.ULONG), ("EventsLost", wintypes.ULONG),
                ("BuffersWritten", wintypes.ULONG), ("LogBuffersLost", wintypes.ULONG),
                ("RealTimeBuffersLost", wintypes.ULONG), ("LoggerThreadId", ctypes.c_void_p),
                ("LogFileNameOffset", wintypes.ULONG), ("LoggerNameOffset", wintypes.ULONG)]


class EVENT_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("Id", wintypes.USHORT), ("Version", ctypes.c_ubyte), ("Channel", ctypes.c_ubyte),
                ("Level", ctypes.c_ubyte), ("Opcode", ctypes.c_ubyte), ("Task", wintypes.USHORT),
                ("Keyword", ctypes.c_uint64)]


class EVENT_HEADER(ctypes.Structure):
    _fields_ = [("Size", wintypes.USHORT), ("HeaderType", wintypes.USHORT),
                ("Flags", wintypes.USHORT), ("EventProperty", wintypes.USHORT),
                ("ThreadId", wintypes.ULONG), ("ProcessId", wintypes.ULONG),
                ("TimeStamp", ctypes.c_int64), ("ProviderId", GUID),
                ("EventDescriptor", EVENT_DESCRIPTOR), ("ProcessorTime", ctypes.c_uint64),
                ("ActivityId", GUID)]


class EVENT_RECORD(ctypes.Structure):
    _fields_ = [("EventHeader", EVENT_HEADER), ("ProcessorNumber", ctypes.c_ubyte),
                ("Alignment", ctypes.c_ubyte), ("LoggerId", wintypes.USHORT),
                ("ExtendedDataCount", wintypes.USHORT), ("UserDataLength", wintypes.USHORT),
                ("ExtendedData", ctypes.c_void_p), ("UserData", ctypes.c_void_p),
                ("UserContext", ctypes.c_void_p)]


class EVENT_TRACE_HEADER(ctypes.Structure):
    _fields_ = [("Size", wintypes.USHORT), ("FieldTypeFlags", wintypes.USHORT),
                ("Version", wintypes.ULONG), ("ThreadId", wintypes.ULONG),
                ("ProcessId", wintypes.ULONG), ("TimeStamp", ctypes.c_int64), ("Guid", GUID),
                ("ProcessorTime", ctypes.c_uint64)]


class EVENT_TRACE(ctypes.Structure):
    _fields_ = [("Header", EVENT_TRACE_HEADER), ("InstanceId", wintypes.ULONG),
                ("ParentInstanceId", wintypes.ULONG), ("ParentGuid", GUID),
                ("MofData", ctypes.c_void_p), ("MofLength", wintypes.ULONG),
                ("ClientContext", wintypes.ULONG)]


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [(nome, wintypes.WORD) for nome in ("wYear", "wMonth", "wDayOfWeek", "wDay",
                                                   "wHour", "wMinute", "wSecond", "wMilliseconds")]


class TIME_ZONE_INFORMATION(ctypes.Structure):
    _fields_ = [("Bias", wintypes.LONG), ("StandardName", ctypes.c_wchar * 32),
                ("StandardDate", SYSTEMTIME), ("StandardBias", wintypes.LONG),
                ("DaylightName", ctypes.c_wchar * 32), ("DaylightDate", SYSTEMTIME),
                ("DaylightBias", wintypes.LONG)]


class TRACE_LOGFILE_HEADER(ctypes.Structure):
    _fields_ = [("BufferSize", wintypes.ULONG), ("Version", wintypes.ULONG),
                ("ProviderVersion", wintypes.ULONG), ("NumberOfProcessors", wintypes.ULONG),
                ("EndTime", ctypes.c_int64), ("TimerResolution", wintypes.ULONG),
                ("MaximumFileSize", wintypes.ULONG), ("LogFileMode", wintypes.ULONG),
                ("BuffersWritten", wintypes.ULONG), ("LogInstanceGuid", GUID),
                ("LoggerName", ctypes.c_void_p), ("LogFileName", ctypes.c_void_p),
                ("TimeZone", TIME_ZONE_INFORMATION), ("BootTime", ctypes.c_int64),
                ("PerfFreq", ctypes.c_int64), ("StartTime", ctypes.c_int64),
                ("ReservedFlags", wintypes.ULONG), ("BuffersLost", wintypes.ULONG)]


_FABRICA_RETORNO = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
TIPO_RETORNO = _FABRICA_RETORNO(None, ctypes.POINTER(EVENT_RECORD))


class EVENT_TRACE_LOGFILEW(ctypes.Structure):
    _fields_ = [("LogFileName", ctypes.c_wchar_p), ("LoggerName", ctypes.c_wchar_p),
                ("CurrentTime", ctypes.c_int64), ("BuffersRead", wintypes.ULONG),
                ("ProcessTraceMode", wintypes.ULONG), ("CurrentEvent", EVENT_TRACE),
                ("LogfileHeader", TRACE_LOGFILE_HEADER), ("BufferCallback", ctypes.c_void_p),
                ("BufferSize", wintypes.ULONG), ("Filled", wintypes.ULONG),
                ("EventsLost", wintypes.ULONG), ("EventRecordCallback", TIPO_RETORNO),
                ("IsKernelTrace", wintypes.ULONG), ("Context", ctypes.c_void_p)]


class EVENT_FILTER_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("Ptr", ctypes.c_uint64), ("Size", wintypes.ULONG), ("Type", wintypes.ULONG)]


class ENABLE_TRACE_PARAMETERS(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("EnableProperty", wintypes.ULONG),
                ("ControlFlags", wintypes.ULONG), ("SourceId", GUID),
                ("EnableFilterDesc", ctypes.POINTER(EVENT_FILTER_DESCRIPTOR)),
                ("FilterDescCount", wintypes.ULONG)]


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


if ctypes.sizeof(ctypes.c_void_p) == 8:
    assert ctypes.sizeof(EVENT_TRACE_PROPERTIES) == 120
    assert ctypes.sizeof(EVENT_HEADER) == 80
    assert ctypes.sizeof(EVENT_RECORD) == 112
    assert ctypes.sizeof(EVENT_TRACE_LOGFILEW) == 448


# ---------------------------------------------------------------- funções puras

def categoria(remoto, local):
    """Categoria de destino pelo endereço remoto (D4); a tailnet vem antes de "privada"."""
    if not isinstance(remoto, (bytes, bytearray)) or len(remoto) not in (4, 16):
        return "internet"
    if remoto == local:
        return "local"
    if len(remoto) == 16 and remoto[:12] == _MAPEADO_V4:
        local4 = local
        if isinstance(local, (bytes, bytearray)) and len(local) == 16 and local[:12] == _MAPEADO_V4:
            local4 = local[12:]
        return categoria(bytes(remoto[12:]), local4)
    if len(remoto) == 4:
        a, b = remoto[0], remoto[1]
        if a == 127:
            return "local"
        if a == 100 and 64 <= b <= 127:
            return "tailnet"
        if (a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
                or (a == 169 and b == 254) or 224 <= a <= 239 or remoto == b"\xff\xff\xff\xff"):
            return "privada"
        return "internet"
    if remoto == b"\x00" * 15 + b"\x01":
        return "local"
    if remoto[:6] == b"\xfd\x7a\x11\x5c\xa1\xe0":
        return "tailnet"
    if (remoto[0] & 0xFE) == 0xFC or (remoto[0] == 0xFE and (remoto[1] & 0xC0) == 0x80) or remoto[0] == 0xFF:
        return "privada"
    return "internet"


def lados(eid, ender):
    """(remoto, local, porta_remota, porta_local) em bytes, ou None se o tamanho não é o do id."""
    v6 = eid in IDS_V6
    tam = 16 if v6 else 4
    if not isinstance(ender, (bytes, bytearray)) or len(ender) != (36 if v6 else 12):
        return None
    ender = bytes(ender)
    a1, a2 = ender[:tam], ender[tam:2 * tam]
    p1, p2 = ender[2 * tam:2 * tam + 2], ender[2 * tam + 2:2 * tam + 4]
    if eid in IDS_REMOTO_NO_SEGUNDO:
        return a2, a1, p2, p1
    return a1, a2, p1, p2


def mapa_pid_produto(listar, tabela):
    """pid -> produto da tabela da E16; None se a listagem falhou (quem chama mantém o anterior)."""
    def predicado(nome_exe):
        return presenca_processos.interessa(nome_exe, tabela)

    try:
        processos = listar(predicado)
    except OSError:
        return None
    mapa = {}
    for processo in processos:
        if not isinstance(processo, dict):
            continue
        pid, caminho = processo.get("pid"), processo.get("caminho")
        if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(caminho, str):
            continue
        produto = presenca_processos.classificar(caminho, tabela)
        if produto is not None:
            mapa[pid] = produto
    return mapa


# ---------------------------------------------------------------- acumulação

class Acumulador:
    """O que o retorno do ETW alimenta; um só para a vida inteira do processo (D21)."""

    def __init__(self):
        self.bruto = {}
        self.eventos = 0
        self.ignorados = 0
        self.curtos = 0
        self.falhas = 0
        self.cortados = False
        self._trava = threading.Lock()

    def criar_retorno(self):
        def retorno(prec):
            try:
                r = prec.contents
                eid = r.EventHeader.EventDescriptor.Id
                self.eventos += 1
                if eid not in IDS:
                    self.ignorados += 1
                    return
                n = 44 if eid in IDS_V6 else 20
                if not r.UserData or r.UserDataLength < n:
                    self.curtos += 1
                    return
                dados = ctypes.string_at(r.UserData, n)
                pid, tam = struct.unpack_from("<II", dados)
                chave = (eid, pid, dados[8:])
                with self._trava:
                    bruto = self.bruto
                    if chave in bruto:
                        bruto[chave] += tam
                    elif len(bruto) < MAX_FLUXOS_CICLO:
                        bruto[chave] = tam
                    else:
                        cortada = (eid, pid, None)
                        if cortada not in bruto and len(bruto) >= MAX_FLUXOS_CICLO + MAX_CORTADAS_CICLO:
                            cortada = (eid, None, None)
                        bruto[cortada] = bruto.get(cortada, 0) + tam
                        self.cortados = True
            except Exception:
                self.falhas += 1
        return retorno

    def trocar(self):
        with self._trava:
            atual = self.bruto
            self.bruto = {}
        return atual


def _teto(v):
    return min(int(v), MAX_BYTES)


def _par_teto(par):
    if par is None:
        return [0, 0]
    return [_teto(par[0]), _teto(par[1])]


class Detalhe:
    """Contadores limitados por identidade de processo e fluxo, só no opt-in local."""

    def __init__(self):
        self.pids, self.fluxos, self.fatias = {}, {}, {}
        self.nao_atribuidos = [0, 0]
        # Total por PID é autoritativo; fluxos_total é uma contagem paralela,
        # nunca somada de novo ao total de bytes expirados.
        self.expirados = {'janela': [0, 0], 'total': [0, 0], 'fluxos_total': [0, 0]}
        self.fluxos_omitidos = self.pids_omitidos = 0

    @staticmethod
    def _somar(mapa, chave, coluna, tam):
        par = mapa.setdefault(chave, [0, 0])
        par[coluna] = min(MAX_BYTES, par[coluna] + tam)

    def adicionar(self, ciclo, pid, criado, fluxo, coluna, tam):
        fatia = self.fatias.setdefault(ciclo, ({}, {}, [0, 0]))
        ident = (pid, criado)
        if pid in (None, 0) or ident not in self.pids and len(self.pids) >= MAX_PIDS_DETALHE:
            self.nao_atribuidos[coluna] = min(MAX_BYTES, self.nao_atribuidos[coluna] + tam)
            fatia[2][coluna] = min(MAX_BYTES, fatia[2][coluna] + tam)
            self.pids_omitidos = min(MAX_BYTES, self.pids_omitidos + 1)
            return
        self._somar(self.pids, ident, coluna, tam)
        self._somar(fatia[0], ident, coluna, tam)
        if fluxo is None:
            self.fluxos_omitidos = min(MAX_BYTES, self.fluxos_omitidos + 1)
            return
        chave = (fluxo[0], pid, criado, *fluxo[2:])
        if chave not in self.fluxos and len(self.fluxos) >= MAX_FLUXOS_DETALHE:
            self.fluxos_omitidos = min(MAX_BYTES, self.fluxos_omitidos + 1)
            return
        self._somar(self.fluxos, chave, coluna, tam)
        self._somar(fatia[1], chave, coluna, tam)

    def expirar(self, ciclo, identidades):
        for c in [c for c in self.fatias if ciclo - c >= CICLOS_JANELA]:
            del self.fatias[c]
        presentes = set((identidades or {}).items())
        pids_ativos, fluxos_ativos = set(), set()
        for pids, fluxos, _ in self.fatias.values():
            pids_ativos.update(pids)
            fluxos_ativos.update(fluxos)
        for chave in list(self.fluxos):
            if chave[1:3] not in presentes and chave not in fluxos_ativos:
                total = self.fluxos.pop(chave)
                for col in range(2):
                    self.expirados['fluxos_total'][col] = min(
                        MAX_BYTES, self.expirados['fluxos_total'][col] + total[col])
        for ident in list(self.pids):
            if ident not in presentes and ident not in pids_ativos:
                total = self.pids.pop(ident)
                for col in range(2):
                    self.expirados['total'][col] = min(MAX_BYTES, self.expirados['total'][col] + total[col])

    def retrato(self, estado, agora_s, desde):
        jp, jf, nao = {}, {}, [0, 0]
        for pids, fluxos, incertos in self.fatias.values():
            for destino, origem in ((jp, pids), (jf, fluxos)):
                for chave, par in origem.items():
                    for col in range(2):
                        self._somar(destino, chave, col, par[col])
            for col in range(2):
                nao[col] = min(MAX_BYTES, nao[col] + incertos[col])
        pids = [dict(pid=pid, criado=criado, janela=jp.get(chave, [0, 0]), total=total[:])
                for chave, total in self.pids.items() for pid, criado in [chave]]
        fluxos = []
        for chave, total in self.fluxos.items():
            udp, pid, criado, porta, remoto, porta_remota = chave
            fluxos.append(dict(pid=pid, criado=criado, proto='udp' if udp else 'tcp',
                local_porta=int.from_bytes(porta, 'big'), remoto=str(ipaddress.ip_address(remoto)),
                remoto_porta=int.from_bytes(porta_remota, 'big'),
                janela=jf.get(chave, [0, 0]), total=total[:]))
        fluxos.sort(key=lambda f: -sum(f['janela']))
        return dict(schema=1, estado=estado, gerado_em=agora_s, desde=desde, janela_s=60,
            pids=pids, fluxos=fluxos, nao_atribuidos=dict(janela=nao, total=self.nao_atribuidos[:]),
            expirados={k: v[:] for k, v in self.expirados.items()},
            fluxos_omitidos=self.fluxos_omitidos, pids_omitidos=self.pids_omitidos)


class Agregador:
    """Bytes por (produto, categoria) na janela de 60 s e no total; fluxos da janela (D5, D6)."""

    def __init__(self, detalhe=False):
        self._fatias = {}
        self._total = {}
        self._fluxos = {}
        self.cache_categorias = {}
        self.no_teto = False
        self._recusa = None
        self.detalhe = Detalhe() if detalhe else None

    def fechar_ciclo(self, bruto, mapa, ciclo, identidades=None, atuais=None):
        if self.detalhe is not None:
            self.detalhe.expirar(ciclo, identidades if atuais is None else atuais)
        fatia = self._fatias.setdefault(ciclo, {})
        for (eid, pid, ender), tam in bruto.items():
            produto = mapa.get(pid, OUTROS)
            if produto not in _PRODUTOS_TABELA:
                produto = OUTROS
            partes = lados(eid, ender) if ender is not None else None
            if partes is None:
                cat, fluxo = "internet", None
                if ender is None:
                    self._recusa = ciclo
            else:
                remoto, local, porta_remota, porta_local = partes
                par = (remoto, local)
                cat = self.cache_categorias.get(par)
                if cat is None:
                    if len(self.cache_categorias) >= MAX_CACHE_CATEGORIAS:
                        self.cache_categorias.clear()
                    cat = categoria(remoto, local)
                    self.cache_categorias[par] = cat
                fluxo = (eid in IDS_UDP, pid, porta_local, remoto, porta_remota)
            coluna = 0 if eid in IDS_ENVIO else 1
            if self.detalhe is not None:
                self.detalhe.adicionar(ciclo, pid, (identidades or {}).get(pid), fluxo, coluna, tam)
            for destino in (fatia, self._total):
                soma = destino.get((produto, cat))
                if soma is None:
                    soma = destino[(produto, cat)] = [0, 0]
                soma[coluna] += tam
            if fluxo is not None:
                conhecido = self._fluxos.get(fluxo)
                if conhecido is not None:
                    self._fluxos[fluxo] = (ciclo, conhecido[1])
                elif len(self._fluxos) < MAX_FLUXOS_JANELA:
                    self._fluxos[fluxo] = (ciclo, produto)
                else:
                    self._recusa = ciclo
        for antigo in [c for c in self._fatias if ciclo - c >= CICLOS_JANELA]:
            del self._fatias[antigo]
        for velho in [f for f, (c, _p) in self._fluxos.items() if ciclo - c >= CICLOS_JANELA]:
            del self._fluxos[velho]
        self.no_teto = self._recusa is not None and ciclo - self._recusa < CICLOS_JANELA

    def retrato(self):
        janela = {}
        for fatia in self._fatias.values():
            for chave, (env, rec) in fatia.items():
                soma = janela.setdefault(chave, [0, 0])
                soma[0] += env
                soma[1] += rec
        conexoes = {}
        for _ciclo, produto in self._fluxos.values():
            conexoes[produto] = conexoes.get(produto, 0) + 1

        def bloco(produto):
            return {"conexoes": _teto(conexoes.get(produto, 0)),
                    "janela": {cat: _par_teto(janela.get((produto, cat))) for cat in CATEGORIAS},
                    "total": {cat: _par_teto(self._total.get((produto, cat))) for cat in CATEGORIAS}}

        programas = []
        for linha in presenca_processos.PRODUTOS:
            produto = linha["produto"]
            dados = bloco(produto)
            if dados["conexoes"] > 0 or any(v for par in dados["total"].values() for v in par):
                programas.append(dict(produto=produto, **dados))
        return {"programas": programas, "outros": bloco(OUTROS),
                "conexoes_no_teto": bool(self.no_teto)}


# ---------------------------------------------------------------- teto de carga

class ControleCarga:
    """Teto de CPU com pausa, esperas crescentes e observação na volta (D8, D9). Puro."""

    def __init__(self):
        self.estado = "ativo"
        self.retoma_em = None
        self.pausas = 0
        self._amostras = []
        self._desde = None

    def _pausar(self, agora):
        espera = ESPERAS_S[min(self.pausas, len(ESPERAS_S) - 1)]
        self.pausas += 1
        self.retoma_em = agora + espera
        self.estado = "pausado"
        self._amostras = []
        return "pausar"

    def amostra(self, cpu_pct, agora):
        if self.estado == "pausado":
            return "continuar"
        if self._desde is None:
            self._desde = agora
        if self.estado == "observacao":
            self._amostras.append(cpu_pct)
            if len(self._amostras) >= 2 and sum(self._amostras) / len(self._amostras) > PISO_RETOMADA_PCT:
                return self._pausar(agora)
            if agora - self._desde >= OBSERVACAO_S:
                self.estado = "ativo"
                self._amostras = []
            return "continuar"
        if agora - self._desde >= ESTAVEL_S:
            self.pausas = 0
        self._amostras.append(cpu_pct)
        self._amostras = self._amostras[-AMOSTRAS_TETO:]
        if len(self._amostras) == AMOSTRAS_TETO and sum(self._amostras) / AMOSTRAS_TETO > TETO_CPU_PCT:
            return self._pausar(agora)
        return "continuar"

    def deve_retomar(self, agora):
        return self.estado == "pausado" and self.retoma_em is not None and agora >= self.retoma_em

    def retomou(self, agora):
        self.estado = "observacao"
        self._amostras = []
        self.retoma_em = None
        self._desde = agora


# ---------------------------------------------------------------- resumo

def _epoch(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, 3)


def _cpu(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    if isinstance(v, float) and not math.isfinite(v):
        return 0.0
    return round(float(min(MAX_CPU_PCT, max(0.0, v))), 1)


def _inteiro(v, maximo):
    return v if type(v) is int and 0 <= v <= maximo else 0


def _bloco_limpo(bloco):
    bloco = bloco if isinstance(bloco, dict) else {}
    saida = {"conexoes": _inteiro(bloco.get("conexoes"), MAX_BYTES)}
    for recorte in ("janela", "total"):
        dados = bloco.get(recorte)
        dados = dados if isinstance(dados, dict) else {}
        pares = {}
        for cat in CATEGORIAS:
            par = dados.get(cat)
            if isinstance(par, (list, tuple)) and len(par) == 2:
                pares[cat] = [_inteiro(par[0], MAX_BYTES), _inteiro(par[1], MAX_BYTES)]
            else:
                pares[cat] = [0, 0]
        saida[recorte] = pares
    return saida


def montar_resumo(estado, motivo, agora_s, desde_s, retrato, cpu_pct, eventos_s, perdidos, retoma_em_s):
    """Resumo com exatamente as 13 chaves; só números finitos, booleanos e valores de lista fixa."""
    retrato = retrato if isinstance(retrato, dict) else {}
    programas, vistos = [], set()
    itens = retrato.get("programas")
    for item in itens if isinstance(itens, list) else []:
        produto = item.get("produto") if isinstance(item, dict) else None
        if isinstance(produto, str) and produto in _PRODUTOS_TABELA and produto not in vistos:
            vistos.add(produto)
            programas.append(dict(produto=produto, **_bloco_limpo(item)))
    return {
        "schema": SCHEMA,
        "gerado_em": _epoch(agora_s),
        "estado": estado if isinstance(estado, str) and estado in ESTADOS else "erro",
        "motivo": motivo if isinstance(motivo, str) and motivo in MOTIVOS else None,
        "desde": _epoch(desde_s),
        "janela_s": int(CICLO_S * CICLOS_JANELA),
        "cpu_pct": _cpu(cpu_pct),
        "eventos_s": _inteiro(eventos_s, MAX_EVENTOS_S),
        "perdidos": _inteiro(perdidos, MAX_BYTES),
        "retoma_em": _epoch(retoma_em_s),
        "conexoes_no_teto": retrato.get("conexoes_no_teto") is True,
        "programas": programas,
        "outros": _bloco_limpo(retrato.get("outros")),
    }


def gravar_resumo(caminho_pasta, resumo):
    """Grava o resumo de forma atômica em `caminho_pasta` (já presa). Nunca levanta; não cria pasta."""
    temporario = None
    try:
        if not os.path.isdir(caminho_pasta):
            return False
        try:
            texto = json.dumps(resumo, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
        except ValueError:
            return False
        dados = texto.encode("ascii")
        if len(dados) > MAX_RESUMO:
            return False
        fd, temporario = tempfile.mkstemp(prefix="." + ARQ_RESUMO + ".", suffix=".tmp", dir=caminho_pasta)
        with os.fdopen(fd, "wb") as arquivo:
            arquivo.write(dados)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        destino = os.path.join(caminho_pasta, ARQ_RESUMO)
        for tentativa in range(6):
            try:
                os.replace(temporario, destino)
                temporario = None
                return True
            except PermissionError:
                if tentativa == 5:
                    raise
                time.sleep(0.05)
        return False
    except Exception:
        return False
    finally:
        if temporario is not None:
            try:
                os.unlink(temporario)
            except OSError:
                pass


def gravar_detalhe(pasta, detalhe, estado=None, agora_s=None, desde=None):
    """Mesma pasta protegida/presa do resumo; corta só fluxos, nunca totais por PID."""
    temporario = None
    try:
        if estado is not None:
            detalhe = detalhe.retrato(estado, agora_s, desde)
        def serializar():
            return (json.dumps(detalhe, ensure_ascii=True, allow_nan=False,
                               separators=(',', ':')) + '\n').encode('ascii')
        dados = serializar()
        while len(dados) > MAX_DETALHE and detalhe['fluxos']:
            quantidade = max(1, len(detalhe['fluxos']) // 4)
            del detalhe['fluxos'][-quantidade:]
            detalhe['fluxos_omitidos'] = min(MAX_BYTES, detalhe['fluxos_omitidos'] + quantidade)
            dados = serializar()
        if len(dados) > MAX_DETALHE or not os.path.isdir(pasta):
            return False
        fd, temporario = tempfile.mkstemp(prefix='.' + ARQ_SESSAO + '.', suffix='.tmp', dir=pasta)
        with os.fdopen(fd, 'wb') as arquivo:
            arquivo.write(dados)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        for tentativa in range(6):
            try:
                os.replace(temporario, os.path.join(pasta, ARQ_SESSAO))
                temporario = None
                return True
            except PermissionError:
                if tentativa == 5:
                    raise
                time.sleep(.05)
    except Exception:
        return False
    finally:
        if temporario is not None:
            try:
                os.unlink(temporario)
            except OSError:
                pass


# ---------------------------------------------------------------- Windows: bibliotecas

_KERNEL32 = None
_ADVAPI32 = None


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        H, D, B, W = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR
        k.CreateFileW.argtypes = [W, D, D, ctypes.c_void_p, D, D, H]
        k.CreateFileW.restype = H
        k.GetFileInformationByHandle.argtypes = [H, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
        k.GetFileInformationByHandle.restype = B
        k.GetFinalPathNameByHandleW.argtypes = [H, wintypes.LPWSTR, D, D]
        k.GetFinalPathNameByHandleW.restype = D
        k.CloseHandle.argtypes = [H]
        k.CloseHandle.restype = B
        k.CreateMutexW.argtypes = [ctypes.c_void_p, B, W]
        k.CreateMutexW.restype = H
        k.OpenMutexW.argtypes = [D, B, W]
        k.OpenMutexW.restype = H
        k.CreateEventW.argtypes = [ctypes.c_void_p, B, B, W]
        k.CreateEventW.restype = H
        k.OpenEventW.argtypes = [D, B, W]
        k.OpenEventW.restype = H
        k.SetEvent.argtypes = [H]
        k.SetEvent.restype = B
        k.WaitForSingleObject.argtypes = [H, D]
        k.WaitForSingleObject.restype = D
        k.LocalFree.argtypes = [ctypes.c_void_p]
        k.LocalFree.restype = ctypes.c_void_p
        _KERNEL32 = k
    return _KERNEL32


def _advapi32():
    global _ADVAPI32
    if _ADVAPI32 is None:
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        U, U64 = wintypes.ULONG, ctypes.c_uint64
        a.StartTraceW.argtypes = [ctypes.POINTER(U64), wintypes.LPCWSTR, ctypes.c_void_p]
        a.StartTraceW.restype = U
        a.EnableTraceEx2.argtypes = [U64, ctypes.POINTER(GUID), U, ctypes.c_ubyte, U64, U64, U,
                                     ctypes.POINTER(ENABLE_TRACE_PARAMETERS)]
        a.EnableTraceEx2.restype = U
        a.OpenTraceW.argtypes = [ctypes.POINTER(EVENT_TRACE_LOGFILEW)]
        a.OpenTraceW.restype = U64
        a.ProcessTrace.argtypes = [ctypes.POINTER(U64), U, ctypes.c_void_p, ctypes.c_void_p]
        a.ProcessTrace.restype = U
        a.ControlTraceW.argtypes = [U64, wintypes.LPCWSTR, ctypes.c_void_p, U]
        a.ControlTraceW.restype = U
        a.CloseTrace.argtypes = [U64]
        a.CloseTrace.restype = U
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(U)]
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        _ADVAPI32 = a
    return _ADVAPI32


# ---------------------------------------------------------------- pasta de dados presa (D14)

_IDENTIFICADOR_INVALIDO = ctypes.c_void_p(-1).value


def _abrir_pasta(k, p):
    h = k.CreateFileW(p, 0x0001, 0x3, None, 3, 0x02000000 | 0x00200000, None)
    if not h or h == _IDENTIFICADOR_INVALIDO:
        return None
    return h


def _info_pasta(k, h):
    info = BY_HANDLE_FILE_INFORMATION()
    if not k.GetFileInformationByHandle(h, ctypes.byref(info)):
        return None
    return info


class PastaPresa:
    """Pasta de dados aberta por um identificador que impede renomear e apagar ela e as de cima."""

    def __init__(self, identificador, caminho, kernel32):
        self.caminho = caminho
        self._h = identificador
        self._k = kernel32

    def fechar(self):
        h, self._h = self._h, None
        if h is not None:
            try:
                self._k.CloseHandle(h)
            except Exception:
                pass


def prender_pasta(caminho):
    """PastaPresa ou None. Nunca levanta e nunca cria nada."""
    k = h = None
    try:
        k = _kernel32()
        p = os.path.abspath(caminho)
        h = _abrir_pasta(k, p)
        if h is None:
            return None
        info = _info_pasta(k, h)
        if info is None or not info.dwFileAttributes & 0x10 or info.dwFileAttributes & 0x400:
            return None
        x = p
        while True:
            if os.lstat(x).st_file_attributes & 0x400:
                return None
            acima = os.path.dirname(x)
            if acima == x:
                break
            x = acima
        h2 = _abrir_pasta(k, p)
        if h2 is None:
            return None
        try:
            info2 = _info_pasta(k, h2)
        finally:
            k.CloseHandle(h2)
        identidade = (info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow)
        if info2 is None or (info2.dwVolumeSerialNumber, info2.nFileIndexHigh, info2.nFileIndexLow) != identidade:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        n = k.GetFinalPathNameByHandleW(h, buf, 1024, 0)
        if n == 0 or n > 1023:
            return None
        final = buf.value
        if final.startswith("\\\\?\\UNC\\") or not final.startswith("\\\\?\\"):
            return None
        pasta = PastaPresa(h, final[4:], k)
        h = None
        return pasta
    except Exception:
        return None
    finally:
        if h is not None and k is not None:
            try:
                k.CloseHandle(h)
            except Exception:
                pass


# ---------------------------------------------------------------- objetos nomeados (D10, D13)

def _criar_objeto(sddl, criar):
    """Chama criar(atributos) e devolve (identificador, erro); o descritor é liberado depois."""
    k = _kernel32()
    if sddl is None:
        ctypes.set_last_error(0)
        h = criar(None)
        return h, ctypes.get_last_error()
    descritor = ctypes.c_void_p()
    ctypes.set_last_error(0)
    convertido = _advapi32().ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descritor), None)
    ctypes.get_last_error()
    if not convertido or not descritor.value:
        return None, -1
    try:
        atributos = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descritor.value, False)
        ctypes.set_last_error(0)
        h = criar(ctypes.byref(atributos))
        return h, ctypes.get_last_error()
    finally:
        k.LocalFree(descritor.value)


def _fechar_identificador(h):
    if h:
        try:
            _kernel32().CloseHandle(h)
        except Exception:
            pass


class MutexNomeado:
    def __init__(self, nome, sddl=SDDL_ADMIN):
        self.nome = nome
        self.sddl = sddl
        self._h = None

    def adquirir(self):
        """True só se ESTE processo criou o objeto (identificador e erro 0)."""
        try:
            k = _kernel32()
            h, erro = _criar_objeto(self.sddl, lambda atributos: k.CreateMutexW(atributos, False, self.nome))
        except Exception:
            return False
        if h and erro == 0 and self._h is None:
            self._h = h
            return True
        _fechar_identificador(h)
        return False

    def liberar(self):
        h, self._h = self._h, None
        _fechar_identificador(h)

    def existe(self):
        try:
            k = _kernel32()
            ctypes.set_last_error(0)
            h = k.OpenMutexW(0x00100000, False, self.nome)
            erro = ctypes.get_last_error()
        except Exception:
            return True
        if h:
            _fechar_identificador(h)
            return True
        return erro != 2


class EventoNomeado:
    def __init__(self, nome, sddl=SDDL_ADMIN):
        self.nome = nome
        self.sddl = sddl
        self._h = None

    def criar(self):
        """Reset manual, desligado; True só se criou (identificador e erro 0)."""
        try:
            k = _kernel32()
            h, erro = _criar_objeto(self.sddl, lambda atributos: k.CreateEventW(atributos, True, False, self.nome))
        except Exception:
            return False
        if h and erro == 0 and self._h is None:
            self._h = h
            return True
        _fechar_identificador(h)
        return False

    def sinalizado(self):
        if self._h is None:
            return False
        try:
            ctypes.set_last_error(0)
            resultado = _kernel32().WaitForSingleObject(self._h, 0)
            ctypes.get_last_error()
        except Exception:
            return False
        return resultado == 0

    def pedir(self):
        try:
            k = _kernel32()
            ctypes.set_last_error(0)
            h = k.OpenEventW(0x0002, False, self.nome)
            ctypes.get_last_error()
            if not h:
                return False
            try:
                ctypes.set_last_error(0)
                k.SetEvent(h)
                ctypes.get_last_error()
            finally:
                _fechar_identificador(h)
            return True
        except Exception:
            return False

    def fechar(self):
        h, self._h = self._h, None
        _fechar_identificador(h)


# ---------------------------------------------------------------- camada ETW

def _guid(texto):
    partes = texto.split("-")
    g = GUID()
    g.Data1, g.Data2, g.Data3 = int(partes[0], 16), int(partes[1], 16), int(partes[2], 16)
    for i, valor in enumerate(bytes.fromhex(partes[3] + partes[4])):
        g.Data4[i] = valor
    return g


def _propriedades(iniciar):
    """Buffer de EVENT_TRACE_PROPERTIES com espaço para os nomes."""
    base = ctypes.sizeof(EVENT_TRACE_PROPERTIES)
    buf = ctypes.create_string_buffer(base + 2 * _BYTES_NOME)
    p = EVENT_TRACE_PROPERTIES.from_buffer(buf)
    p.Wnode.BufferSize = len(buf)
    p.LoggerNameOffset = base
    if iniciar:
        p.Wnode.Flags = WNODE_FLAG_TRACED_GUID
        p.Wnode.ClientContext = 1
        p.BufferSize = 64
        p.MinimumBuffers = 16
        p.MaximumBuffers = 64
        p.LogFileMode = MODO_TEMPO_REAL
        p.FlushTimer = 1
        p.LogFileNameOffset = 0
    else:
        p.LogFileNameOffset = base + _BYTES_NOME
    return buf


class SessaoETW:
    """Sessão de tempo real `painel-ias-rede`; as funções de advapi32 entram por `api`."""

    def __init__(self, api=None):
        self.api = api
        self.retorno_c = None
        self.referencias = []
        self._sessao = None
        self._consumo = None
        self._fio = None

    def _a(self):
        if self.api is None:
            self.api = _advapi32()
        return self.api

    def limpar_orfa(self):
        try:
            rc = self._a().ControlTraceW(0, NOME_SESSAO, _propriedades(False), CONTROLE_PARAR)
        except Exception:
            return False
        return rc in (0, SEM_SESSAO)

    def _parar_pelo_identificador(self, api):
        h, self._sessao = self._sessao, None
        if h is not None:
            try:
                api.ControlTraceW(h, None, _propriedades(False), CONTROLE_PARAR)
            except Exception:
                pass

    def _consumir(self, api, alca):
        try:
            api.ProcessTrace(alca, 1, None, None)
        except Exception:
            pass

    def abrir(self, retorno):
        if self._fio is not None and self._fio.is_alive():
            return "consumidor"
        api = self._a()
        if self.retorno_c is None:
            self.retorno_c = TIPO_RETORNO(retorno)
        props = _propriedades(True)
        identificador = ctypes.c_uint64(0)
        guid = _guid(PROVEDOR)
        filtro = ctypes.create_string_buffer(FILTRO_IDS, len(FILTRO_IDS))
        descritor = EVENT_FILTER_DESCRIPTOR(ctypes.addressof(filtro), len(FILTRO_IDS), FILTRO_TIPO_ID)
        parametros = ENABLE_TRACE_PARAMETERS()
        parametros.Version = 2
        parametros.EnableFilterDesc = ctypes.pointer(descritor)
        parametros.FilterDescCount = 1
        logfile = EVENT_TRACE_LOGFILEW()
        logfile.LoggerName = NOME_SESSAO
        logfile.ProcessTraceMode = MODO_CONSUMO
        logfile.EventRecordCallback = self.retorno_c
        alca = (ctypes.c_uint64 * 1)()
        self.referencias.append((self.retorno_c, props, identificador, guid, filtro, descritor,
                                 parametros, logfile, alca))
        rc = api.StartTraceW(ctypes.pointer(identificador), NOME_SESSAO, props)
        self._sessao = identificador.value if rc == 0 else None
        if rc != 0:
            return "sessao"
        rc = api.EnableTraceEx2(self._sessao, ctypes.pointer(guid), ATIVAR_PROVEDOR, NIVEL,
                                PALAVRAS_CHAVE, 0, 0, ctypes.pointer(parametros))
        if rc != 0:
            rc = api.EnableTraceEx2(self._sessao, ctypes.pointer(guid), ATIVAR_PROVEDOR, NIVEL,
                                    PALAVRAS_CHAVE, 0, 0, None)
            if rc != 0:
                self._parar_pelo_identificador(api)
                return "provedor"
        consumo = api.OpenTraceW(ctypes.pointer(logfile))
        if not isinstance(consumo, int) or (consumo & 0xFFFFFFFFFFFFFFFF) in _CONSUMO_INVALIDO:
            self._parar_pelo_identificador(api)
            return "consumidor"
        self._consumo = consumo
        alca[0] = consumo
        fio = threading.Thread(target=self._consumir, args=(api, alca), name="painel-rede-etw", daemon=True)
        self._fio = fio
        fio.start()
        return None

    def fechar(self):
        try:
            if self._sessao is not None:
                self._parar_pelo_identificador(self._a())
        except Exception:
            pass
        try:
            fio = self._fio
            if fio is not None and fio.is_alive():
                fio.join(ESPERA_FIO_S)
        except Exception:
            pass
        try:
            if self._consumo is not None:
                c, self._consumo = self._consumo, None
                self._a().CloseTrace(c)
        except Exception:
            pass

    def perdidos(self):
        if self._sessao is None:
            return 0
        try:
            props = _propriedades(False)
            if self._a().ControlTraceW(self._sessao, None, props, CONTROLE_CONSULTA) != 0:
                return 0
            p = EVENT_TRACE_PROPERTIES.from_buffer(props)
            return int(p.EventsLost) + int(p.RealTimeBuffersLost)
        except Exception:
            return 0


# ---------------------------------------------------------------- laço

def capturar(pasta, sessao, listar, tabela, parar, relogio, cpu, dormir, agora, mutex, evento, detalhe=False):
    """Laço da captura; devolve o código de saída (0, 1, 3 ou 4). Nunca propaga Exception."""
    if not mutex.adquirir():
        return 3
    if not evento.criar():
        mutex.liberar()
        return 4
    codigo = 1
    saiu_por_abrir = False
    agregador = None
    desde = None
    ultima = None
    try:
        acumulador = Acumulador()
        agregador = Agregador(detalhe=detalhe)
        controle = ControleCarga()
        retorno = acumulador.criar_retorno()
        desde = agora()
        anteriores, limite_anterior = {}, desde
        def identidades():
            try:
                return {p['pid']: p.get('criado') for p in listar(lambda _: True)[:16384]
                        if isinstance(p, dict) and type(p.get('pid')) is int
                        and type(p.get('criado')) is int and p['criado'] > 0}
            except OSError:
                return {}
        if detalhe:
            anteriores = identidades()
        sessao.limpar_orfa()
        motivo = sessao.abrir(retorno)
        if motivo is not None:
            saiu_por_abrir = True
            gravar_resumo(pasta.caminho, montar_resumo("erro", motivo, agora(), desde, None, 0.0, 0, 0, None))
        else:
            mapa = mapa_pid_produto(listar, tabela)
            if mapa is None:
                mapa = {}
            cpu_anterior = cpu()
            relogio_anterior = relogio()
            eventos_anterior = acumulador.eventos
            relogio_gravacao = relogio_anterior
            ciclo = 0
            pausado = False
            while not parar() and not evento.sinalizado():
                dormir(CICLO_S)
                ciclo += 1
                if ciclo % CICLOS_POR_MAPA == 0:
                    novo = mapa_pid_produto(listar, tabela)
                    if novo is not None:
                        mapa = novo
                relogio_atual = relogio()
                cpu_atual = cpu()
                d = relogio_atual - relogio_anterior
                cpu_pct = 100.0 * (cpu_atual - cpu_anterior) / d if d > 0 else None
                cpu_anterior, relogio_anterior = cpu_atual, relogio_atual
                if cpu_pct is not None:
                    ultima = cpu_pct
                if not pausado:
                    bruto = acumulador.trocar()
                    estaveis = None
                    if detalhe:
                        limite = agora()
                        atuais = identidades()
                        estaveis = {pid: criado for pid, criado in atuais.items()
                                    if anteriores.get(pid) == criado
                                    and criado / 10 ** 7 - 11644473600
                                    <= limite_anterior - MARGEM_REUSO_PID_S}
                        anteriores, limite_anterior = atuais, limite
                    agregador.fechar_ciclo(bruto, mapa, ciclo, estaveis, atuais if detalhe else None)
                    if cpu_pct is not None and controle.amostra(cpu_pct, relogio()) == "pausar":
                        sessao.fechar()
                        if not sessao.limpar_orfa():
                            saiu_por_abrir = True
                            gravar_resumo(pasta.caminho, montar_resumo(
                                "erro", None, agora(), desde, None, ultima if ultima is not None else 0.0,
                                0, 0, None))
                            break
                        pausado = True
                else:
                    agregador.fechar_ciclo({}, mapa, ciclo, atuais=identidades() if detalhe else None)
                    if controle.deve_retomar(relogio()):
                        motivo = sessao.abrir(retorno)
                        if motivo is not None:
                            saiu_por_abrir = True
                            gravar_resumo(pasta.caminho, montar_resumo(
                                "erro", motivo, agora(), desde, None, ultima if ultima is not None else 0.0,
                                0, 0, None))
                            break
                        controle.retomou(relogio())
                        pausado = False
                if ciclo % CICLOS_POR_GRAVACAO == 0:
                    relogio_g = relogio()
                    decorrido = relogio_g - relogio_gravacao
                    eventos = acumulador.eventos
                    if pausado or decorrido <= 0:
                        eventos_s = 0
                    else:
                        eventos_s = int((eventos - eventos_anterior) / decorrido)
                    eventos_anterior, relogio_gravacao = eventos, relogio_g
                    agora_s = agora()
                    retoma = None
                    if pausado:
                        retoma = agora_s + max(0.0, controle.retoma_em - relogio())
                    gravar_resumo(pasta.caminho, montar_resumo(
                        "pausado_carga" if pausado else "ativo", None, agora_s, desde,
                        agregador.retrato(), ultima if ultima is not None else 0.0, eventos_s,
                        0 if pausado else sessao.perdidos(), retoma))
                    if detalhe:
                        gravar_detalhe(pasta.caminho, agregador.detalhe,
                            'pausado_carga' if pausado else 'ativo', agora_s, desde)
            codigo = 1 if saiu_por_abrir else 0
    except Exception:
        codigo = 1
    finally:
        if detalhe and agregador is not None and desde is not None:
            try:
                gravar_detalhe(pasta.caminho, agregador.detalhe,
                    'erro' if codigo else 'encerrado', agora(), desde)
            except Exception:
                pass
        try:
            sessao.fechar()
        except Exception:
            pass
        try:
            sessao.limpar_orfa()
        except Exception:
            pass
        if not saiu_por_abrir:
            try:
                retrato = agregador.retrato() if agregador is not None else None
            except Exception:
                retrato = None
            try:
                gravar_resumo(pasta.caminho, montar_resumo(
                    "encerrado", None, agora(), desde, retrato, ultima if ultima is not None else 0.0,
                    0, 0, None))
            except Exception:
                pass
        try:
            evento.fechar()
        except Exception:
            pass
        try:
            mutex.liberar()
        except Exception:
            pass
        try:
            pasta.fechar()
        except Exception:
            pass
    return codigo
