"""TCP/UDP por PID, somente leitura via IP Helper; conteúdo exclusivo de local.json.

Sem DNS, sockets ou subprocessos. As estruturas usam DWORD de 32 bits mesmo
nos testes fora do Windows. Nomes são resolvidos uma vez por PID por leitura.
"""
import ctypes
import ipaddress
import ntpath
import os

LIMITE = 5000
MAX_TABELA = 16 * 1024 * 1024
TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1
ERROR_INSUFFICIENT_BUFFER = 122
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERRO_WINDOWS = "conexões: plataforma sem suporte"
ERRO_LER = "conexões: sem leitura"
ESTADOS = ("CLOSED", "LISTEN", "SYN_SENT", "SYN_RCVD", "ESTABLISHED",
           "FIN_WAIT1", "FIN_WAIT2", "CLOSE_WAIT", "CLOSING", "LAST_ACK",
           "TIME_WAIT", "DELETE_TCB")
DWORD = ctypes.c_uint32
IPV6 = ctypes.c_ubyte * 16


class Tcp4(ctypes.Structure):
    _fields_ = [(nome, DWORD) for nome in
                ("estado", "local_ip", "local_porta", "remoto_ip", "remoto_porta", "pid")]


class Tcp6(ctypes.Structure):
    _fields_ = [("local_ip", IPV6), ("local_scope", DWORD), ("local_porta", DWORD),
                ("remoto_ip", IPV6), ("remoto_scope", DWORD), ("remoto_porta", DWORD),
                ("estado", DWORD), ("pid", DWORD)]


class Udp4(ctypes.Structure):
    _fields_ = [(nome, DWORD) for nome in ("local_ip", "local_porta", "pid")]


class Udp6(ctypes.Structure):
    _fields_ = [("local_ip", IPV6), ("local_scope", DWORD), ("local_porta", DWORD),
                ("pid", DWORD)]


LINHAS = {("tcp", 4): Tcp4, ("tcp", 6): Tcp6, ("udp", 4): Udp4, ("udp", 6): Udp6}


def _porta(valor):
    # Só os 16 bits baixos têm significado, em network order (Windows little endian).
    return ((valor & 255) << 8) | ((valor >> 8) & 255)


def _ip(valor, familia, scope=0):
    bruto = valor.to_bytes(4, 'little') if familia == 4 else bytes(valor)
    texto = str(ipaddress.ip_address(bruto))
    if familia == 6 and scope:
        # Medido no Windows: scope em host order; somente as portas trocam bytes.
        texto += '%' + str(scope)
    return texto


def analisar_tabela(bruto, proto, familia, limite=LIMITE):
    """(linhas limitadas, total declarado); rejeita tabela incompleta antes de percorrer."""
    tipo = LINHAS[proto, familia]
    if len(bruto) < 4:
        raise ValueError('tabela incompleta')
    total = int.from_bytes(bruto[:4], 'little')
    tamanho = ctypes.sizeof(tipo)
    if total > (len(bruto) - 4) // tamanho:
        raise ValueError('tabela incompleta')
    itens = []
    for indice in range(min(total, max(0, limite))):
        linha = tipo.from_buffer_copy(bruto, 4 + indice * tamanho)
        tcp = proto == 'tcp'
        estado = (ESTADOS[linha.estado - 1] if 1 <= linha.estado <= len(ESTADOS)
                  else 'DESCONHECIDO') if tcp else None
        itens.append({
            'proto': proto, 'familia': familia,
            'local_ip': _ip(linha.local_ip, familia, getattr(linha, 'local_scope', 0)),
            'local_porta': _porta(linha.local_porta),
            'remoto_ip': _ip(linha.remoto_ip, familia, getattr(linha, 'remoto_scope', 0)) if tcp else None,
            'remoto_porta': _porta(linha.remoto_porta) if tcp else None,
            'estado': estado, 'pid': int(linha.pid)})
    return itens, total


class SistemaWindows:
    """Fronteira ctypes; sem efeitos no import nem permissões de alteração de processo."""

    def __init__(self):
        from ctypes import wintypes
        self.ip = ctypes.WinDLL('iphlpapi', use_last_error=True)
        for nome in ('GetExtendedTcpTable', 'GetExtendedUdpTable'):
            funcao = getattr(self.ip, nome)
            funcao.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWORD), wintypes.BOOL,
                               DWORD, ctypes.c_int, DWORD]
            funcao.restype = DWORD
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [DWORD, wintypes.BOOL, DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL

    def tabela(self, proto, familia):
        funcao = self.ip.GetExtendedTcpTable if proto == 'tcp' else self.ip.GetExtendedUdpTable
        classe = TCP_TABLE_OWNER_PID_ALL if proto == 'tcp' else UDP_TABLE_OWNER_PID
        af = 2 if familia == 4 else 23  # AF_INET / AF_INET6 no Windows.
        tamanho = DWORD()
        codigo = funcao(None, ctypes.byref(tamanho), False, af, classe, 0)
        for _ in range(3):  # A tabela pode crescer entre a consulta de tamanho e a cópia.
            if codigo not in (0, ERROR_INSUFFICIENT_BUFFER) or not 4 <= tamanho.value <= MAX_TABELA:
                raise OSError(ERRO_LER)
            capacidade = min(MAX_TABELA, tamanho.value + (tamanho.value + 3) // 4)
            buffer = ctypes.create_string_buffer(capacidade)
            tamanho.value = capacidade
            codigo = funcao(buffer, ctypes.byref(tamanho), False, af, classe, 0)
            if codigo == 0:
                if tamanho.value > ctypes.sizeof(buffer):
                    raise OSError(ERRO_LER)
                return buffer.raw[:tamanho.value]
        raise OSError(ERRO_LER)

    def nome(self, pid):
        if pid in (0, 4):
            return 'System Idle' if pid == 0 else 'System'
        handle = self.kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return 'sem leitura'
        try:
            tamanho = DWORD(32768)
            buffer = ctypes.create_unicode_buffer(tamanho.value)
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(tamanho)):
                return 'sem leitura'
            return ntpath.basename(buffer[:tamanho.value]) or 'sem leitura'
        finally:
            self.kernel.CloseHandle(handle)


def agregar(itens, omitidas):
    """Portas por endpoint, preservando todos os donos; contagens só das linhas incluídas."""
    portas, processos = {}, {}
    for item in itens:
        pid, nome = item['pid'], item['processo']
        processo = processos.setdefault(pid, {'pid': pid, 'processo': nome, 'tcp': 0, 'udp': 0,
                                              'listen': 0, 'established': 0, 'total': 0})
        processo[item['proto']] += 1
        processo['total'] += 1
        processo['listen'] += item['estado'] == 'LISTEN'
        processo['established'] += item['estado'] == 'ESTABLISHED'
        if item['proto'] == 'udp' or item['estado'] == 'LISTEN':
            chave = item['proto'], item['local_ip'], item['local_porta']
            porta = portas.setdefault(chave, {'proto': chave[0], 'local_ip': chave[1],
                                               'porta': chave[2], 'processos': {}})
            porta['processos'][pid] = {'pid': pid, 'processo': nome}
    ordem = lambda p: (p['processo'].casefold(), p['pid'])
    abertas = []
    for chave in sorted(portas):
        porta = portas[chave]
        abertas.append(dict(porta, processos=sorted(porta['processos'].values(), key=ordem)))
    return {'estado': 'ok', 'itens': itens, 'portas_abertas': abertas,
            'processos': sorted(processos.values(), key=ordem), 'omitidas': omitidas}


def ler(ctx):
    sistema = ctx.get('conexoes_sistema')
    if sistema is None:
        if os.name != 'nt':
            return {'erro': ERRO_WINDOWS}
        try:
            sistema = SistemaWindows()
        except (OSError, AttributeError):
            return {'erro': ERRO_LER}
    try:
        itens, omitidas, nomes = [], 0, {}
        for proto, familia in LINHAS:
            linhas, total = analisar_tabela(sistema.tabela(proto, familia), proto, familia, LIMITE - len(itens))
            omitidas += total - len(linhas)
            for linha in linhas:
                pid = linha['pid']
                if pid not in nomes:
                    nomes[pid] = ('System Idle' if pid == 0 else 'System') if pid in (0, 4) else sistema.nome(pid)
                linha['processo'] = ntpath.basename(nomes[pid]) or 'sem leitura'
            itens.extend(linhas)
        return {'conexoes': agregar(itens, omitidas)}
    except (OSError, ValueError, TypeError, OverflowError, ctypes.ArgumentError):
        return {'erro': ERRO_LER}
