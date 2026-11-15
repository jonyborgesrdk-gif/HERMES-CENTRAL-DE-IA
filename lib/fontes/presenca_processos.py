"""Presença por processo: produtos de IA com processo principal vivo (opt-in, PAINEL_PRESENCA=1).

Reconhece pelo caminho completo do executável contra uma tabela fixa; lê só a lista de
processos do kernel (Toolhelp) e, de cada candidato, o caminho da imagem e a hora de criação.
Nunca lê linha de comando, cwd, título de janela nem ambiente de outros processos, e não
dispara programa nenhum. A saída só tem constantes da tabela e inteiros: PID, caminho, hora
de criação e usuário nunca saem daqui.
"""
import ntpath
import os
import re

PRODUTOS = (
    {"produto": "claude_cli", "rotulo": "Claude Code CLI", "familia": "claude", "tipo": "cli",
     "padroes": ("{HOME}\\.local\\bin\\claude.exe", "{HOME}\\.local\\bin\\claude.exe.old.*")},
    {"produto": "claude_app", "rotulo": "Claude (app)", "familia": "claude", "tipo": "app",
     "padroes": ("{PF}\\WindowsApps\\Claude_*__pzs8sxrjxfjjc\\app\\claude.exe",)},
    {"produto": "codex_cli", "rotulo": "Codex CLI", "familia": "codex", "tipo": "cli",
     "padroes": ("{LOCAL}\\Programs\\OpenAI\\Codex\\bin\\codex.exe",
                 "{HOME}\\.codex\\packages\\standalone\\releases\\*\\bin\\codex.exe")},
    {"produto": "codex_app", "rotulo": "Codex (app)", "familia": "codex", "tipo": "app",
     "padroes": ("{PF}\\WindowsApps\\OpenAI.Codex_*__2p2nqsd0c76g0\\app\\codex.exe",)},
    {"produto": "grok_cli", "rotulo": "Grok CLI", "familia": "grok", "tipo": "cli",
     "padroes": ("{HOME}\\.grok\\bin\\grok.exe", "{HOME}\\.grok\\bin\\agent.exe")},
    {"produto": "grok_bot", "rotulo": "Grok Bot", "familia": "grok", "tipo": "app",
     "padroes": ("{LOCAL}\\Programs\\Grok Bot\\Grok Bot.exe",)},
    {"produto": "hermes_cli", "rotulo": "Hermes CLI", "familia": "hermes", "tipo": "cli",
     "padroes": ("{LOCAL}\\hermes\\bin\\hermes.exe",
                 "{LOCAL}\\hermes\\hermes-agent\\venv\\Scripts\\hermes.exe")},
    {"produto": "hermes_app", "rotulo": "Hermes (app)", "familia": "hermes", "tipo": "app",
     "padroes": ("{LOCAL}\\hermes\\hermes-agent\\apps\\desktop\\release\\win-unpacked\\Hermes.exe",)},
    {"produto": "cursor_app", "rotulo": "Cursor", "familia": "cursor", "tipo": "app",
     "padroes": ("{LOCAL}\\Programs\\cursor\\Cursor.exe",)},
    {"produto": "cursor_agent", "rotulo": "Cursor Agent CLI", "familia": "cursor", "tipo": "cli",
     "padroes": ("{LOCAL}\\cursor-agent\\versions\\*\\node.exe",)},
    {"produto": "copilot_cli", "rotulo": "Copilot CLI", "familia": "copilot", "tipo": "cli",
     "padroes": ("{LOCAL}\\GitHubCopilotCLI\\copilot.exe",)},
    {"produto": "copilot_app", "rotulo": "GitHub Copilot (app)", "familia": "copilot", "tipo": "app",
     "padroes": ("{LOCAL}\\Programs\\GitHub Copilot\\github.exe",)},
    {"produto": "maestri", "rotulo": "Maestri", "familia": "maestri", "tipo": "app",
     "padroes": ("{LOCAL}\\Programs\\Maestri\\Maestri.exe",)},
)
FAMILIAS = ("claude", "codex", "grok", "hermes", "cursor", "copilot", "maestri")
TIPOS = ("cli", "app")

# Marcador do padrão -> variáveis de ambiente, na ordem de preferência.
VARIAVEIS = (("{HOME}", ("USERPROFILE",)), ("{LOCAL}", ("LOCALAPPDATA",)),
             ("{PF}", ("ProgramW6432", "ProgramFiles")))
_MARCADOR = re.compile(r"\{(?:HOME|LOCAL|PF)\}")

ERRO_PLATAFORMA = "presença: plataforma sem suporte"
ERRO_LISTAR = "presença: falha ao listar processos"

TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TAMANHO_BUFFER = 32768


def _normalizar(caminho):
    """ntpath (não os.path): o resultado não depende do sistema onde roda."""
    return ntpath.normpath(caminho).lower()


def _casa_segmento(texto, modelo):
    """`*` casa qualquer sequência dentro do segmento; nenhum outro caractere é curinga."""
    partes = modelo.split("*")
    if len(partes) == 1:
        return texto == modelo
    inicio, fim = partes[0], partes[-1]
    if len(texto) < len(inicio) + len(fim) or not texto.startswith(inicio) or not texto.endswith(fim):
        return False
    posicao, limite = len(inicio), len(texto) - len(fim)
    for meio in partes[1:-1]:
        achou = texto.find(meio, posicao, limite)
        if achou < 0:
            return False
        posicao = achou + len(meio)
    return True


def _casa_caminho(segmentos, padrao):
    modelos = _normalizar(padrao).split("\\")
    return len(modelos) == len(segmentos) and all(
        _casa_segmento(texto, modelo) for texto, modelo in zip(segmentos, modelos))


def tabela_resolvida(ambiente):
    """Tabela com as variáveis substituídas; padrão cuja variável falta (ou é vazia) fica de fora."""
    valores = {}
    for marcador, nomes in VARIAVEIS:
        for nome in nomes:
            valor = ambiente.get(nome)
            if valor:
                valores[marcador] = valor
                break
    tabela = []
    for linha in PRODUTOS:
        padroes = []
        for padrao in linha["padroes"]:
            if all(marcador in valores for marcador in _MARCADOR.findall(padrao)):
                # Uma passada só: o texto vindo do ambiente nunca é relido como marcador.
                resolvido = _MARCADOR.sub(lambda achado: valores[achado.group(0)], padrao)
                padroes.append(_normalizar(resolvido))
        tabela.append(dict(linha, padroes=tuple(padroes)))
    return tuple(tabela)


def classificar(caminho, tabela):
    """Produto cujo padrão casa com o caminho completo, ou None."""
    if not caminho:
        return None
    segmentos = _normalizar(caminho).split("\\")
    for linha in tabela:
        if any(_casa_caminho(segmentos, padrao) for padrao in linha["padroes"]):
            return linha["produto"]
    return None


def interessa(nome_exe, tabela):
    """Pré-filtro de custo pelo nome do executável; a classificação usa sempre o caminho completo."""
    if not nome_exe:
        return False
    nome = nome_exe.lower()
    return any(_casa_segmento(nome, _normalizar(padrao).rsplit("\\", 1)[-1])
               for linha in tabela for padrao in linha["padroes"])


def agregar(processos, tabela):
    """Conta raízes por produto. Filho = pai direto reconhecido, do mesmo produto e criado antes (ou junto)."""
    reconhecidos, ilegiveis = {}, 0
    for processo in processos:
        caminho, criado = processo.get("caminho"), processo.get("criado")
        if caminho is None or criado is None:
            ilegiveis += 1
            continue
        produto = classificar(caminho, tabela)
        if produto is not None:
            reconhecidos[processo["pid"]] = (produto, processo["ppid"], criado)
    instancias = {}
    for pid, (produto, ppid, criado) in reconhecidos.items():
        pai = reconhecidos.get(ppid) if ppid != pid else None
        if pai is not None and pai[0] == produto and pai[2] <= criado:
            continue
        instancias[produto] = instancias.get(produto, 0) + 1
    itens = [{"produto": linha["produto"], "rotulo": linha["rotulo"], "familia": linha["familia"],
              "tipo": linha["tipo"], "instancias": instancias[linha["produto"]]}
             for linha in tabela if linha["produto"] in instancias]
    return {"estado": "ok", "itens": itens, "ilegiveis": ilegiveis}


def listar_windows(predicado):
    """Toolhelp + QueryFullProcessImageNameW + GetProcessTimes; só abre quem passa no predicado."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                  ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    invalido = wintypes.HANDLE(-1).value
    buffer = ctypes.create_unicode_buffer(TAMANHO_BUFFER)

    def detalhar(pid):
        """(caminho, criado) ou (None, None) quando o processo não abre ou não responde."""
        handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None, None
        try:
            tamanho = wintypes.DWORD(TAMANHO_BUFFER)
            if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(tamanho)):
                return None, None
            caminho = buffer[:tamanho.value]
            criacao, saida, nucleo, usuario = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(criacao), ctypes.byref(saida),
                                          ctypes.byref(nucleo), ctypes.byref(usuario)):
                return None, None
            return caminho, (criacao.dwHighDateTime << 32) | criacao.dwLowDateTime
        finally:
            kernel.CloseHandle(handle)

    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == invalido:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entrada = PROCESSENTRY32W()
        entrada.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entrada)):
            # Snapshot sem nem o próprio processo: lista vazia seria um "ninguém online" falso.
            raise ctypes.WinError(ctypes.get_last_error())
        processos = []
        while True:
            nome = entrada.szExeFile
            if predicado(nome):
                pid = entrada.th32ProcessID
                caminho, criado = detalhar(pid)
                processos.append({"pid": pid, "ppid": entrada.th32ParentProcessID, "nome": nome,
                                  "caminho": caminho, "criado": criado})
            if not kernel.Process32NextW(snapshot, ctypes.byref(entrada)):
                break
        return processos
    finally:
        kernel.CloseHandle(snapshot)


def ler(ctx):
    listar = ctx.get("listar_processos")
    if listar is None:
        if os.name != "nt":
            return {"erro": ERRO_PLATAFORMA}
        listar = listar_windows
    tabela = tabela_resolvida(ctx["ambiente"] if "ambiente" in ctx else os.environ)

    def predicado(nome_exe):
        return interessa(nome_exe, tabela)

    try:
        processos = listar(predicado)
    except OSError:
        return {"erro": ERRO_LISTAR}
    return {"presenca_processos": agregar(processos, tabela)}
