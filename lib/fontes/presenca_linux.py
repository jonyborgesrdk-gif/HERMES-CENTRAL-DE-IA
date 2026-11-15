"""Presença no WSL: produtos de IA com processo vivo na distro Ubuntu-26.04 (opt-in, PAINEL_PRESENCA_LINUX=1).

Dois passos na mesma chamada de `ler`: primeiro a lista de distros ligadas
(`wsl.exe --list --running --quiet`); só se a distro estiver nela, UMA consulta com
`wsl.exe -d <distro> -e sh -c SCRIPT`. Distro parada nunca é consultada (nem ligada), e
qualquer dúvida na checagem fecha sem consulta. O script só lê `/proc/<pid>/exe` e
`/proc/<pid>/stat`; linha de comando, cwd e ambiente nunca são lidos. A saída só tem
constantes da tabela, o nome da distro e inteiros: PID, caminho, início e usuário nunca
saem daqui.
"""
import os
import re
import subprocess

try:
    from ..semjanela import sem_janela_kwargs
except ImportError:
    from semjanela import sem_janela_kwargs

from . import presenca_processos

DISTRO = "Ubuntu-26.04"
T_CHECAGEM_S = 2
T_CONSULTA_S = 5
ARGV_CHECAGEM = ['wsl.exe', '--list', '--running', '--quiet']
ERRO_PLATAFORMA = "presença Linux: plataforma sem suporte"
ERRO_DISTRO = "presença Linux: nome de distro inválido"
ERRO_CHECAGEM = "presença Linux: estado do WSL desconhecido"
ERRO_CONSULTA = "presença Linux: consulta à distro falhou"
ERRO_FORMATO = "presença Linux: resposta inesperada da distro"
SCRIPT = (
    "printf 'H\\t%s\\n' \"$HOME\"\n"
    "for p in /proc/[0-9]*; do\n"
    "  e=$(readlink \"$p/exe\" 2>/dev/null) || continue\n"
    "  s=$(cat \"$p/stat\" 2>/dev/null) || continue\n"
    "  s=${s##*) }\n"
    "  set -- $s\n"
    "  printf 'P\\t%s\\t%s\\t%s\\t%s\\n' \"${p#/proc/}\" \"$2\" \"${20}\" \"$e\"\n"
    "done\n"
    "printf 'F\\n'\n"
)

PRODUTOS = (
    {"produto": "claude_cli", "rotulo": "Claude Code CLI", "familia": "claude", "tipo": "cli",
     "padroes": ("{HOME}/.local/share/claude/versions/*",)},
    {"produto": "codex_cli", "rotulo": "Codex CLI", "familia": "codex", "tipo": "cli",
     "padroes": ("{HOME}/.local/bin/codex",
                 "{HOME}/.nvm/versions/node/*/lib/node_modules/@openai/codex/node_modules/"
                 "@openai/codex-linux-*/vendor/*/bin/codex")},
    {"produto": "grok_cli", "rotulo": "Grok CLI", "familia": "grok", "tipo": "cli",
     "padroes": ("{HOME}/.grok/downloads/grok-*",
                 "{HOME}/.nvm/versions/node/*/lib/node_modules/@xai-official/grok/bin/grok-native")},
    {"produto": "cursor_agent", "rotulo": "Cursor Agent CLI", "familia": "cursor", "tipo": "cli",
     "padroes": ("{HOME}/.local/share/cursor-agent/versions/*/node",)},
)

# O nome vai para a linha de comando do wsl.exe: nunca começa com '-' (não vira opção).
_NOME_DISTRO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_INTEIRO = re.compile(r"[0-9]+")
_APAGADO = " (deleted)"


def distros_ligadas(bruto):
    """Nomes das distros ligadas, em minúsculas. A listagem do wsl.exe sai em UTF-16LE sem BOM."""
    texto = bruto.decode('utf-16-le' if b'\x00' in bruto else 'utf-8', errors='replace')
    texto = texto.replace('\ufeff', '')
    return {linha.strip().lower() for linha in texto.splitlines() if linha.strip()}


def tabela_resolvida(home):
    """Tabela com {HOME} trocado pela casa da distro; casa que não é absoluta ou tem `*` é recusada."""
    if not isinstance(home, str) or not home.startswith('/') or '*' in home:
        raise ValueError('casa inválida')
    base = home.rstrip('/')
    return tuple(dict(linha, padroes=tuple(padrao.replace('{HOME}', base) for padrao in linha['padroes']))
                 for linha in PRODUTOS)


def classificar(exe, tabela):
    """Produto cujo padrão casa com o caminho inteiro, segmento a segmento (maiúsculas contam), ou None."""
    if not exe:
        return None
    if exe.endswith(_APAGADO):
        exe = exe[:-len(_APAGADO)]
    segmentos = exe.split('/')
    for linha in tabela:
        for padrao in linha['padroes']:
            modelos = padrao.split('/')
            if len(modelos) == len(segmentos) and all(
                    presenca_processos._casa_segmento(texto, modelo)
                    for texto, modelo in zip(segmentos, modelos)):
                return linha['produto']
    return None


def interpretar(bruto):
    """(casa, processos) a partir da saída do SCRIPT; resposta sem `H` única ou sem `F` final -> ValueError."""
    linhas = bruto.decode('utf-8', errors='replace').split('\n')
    casas = [linha[2:] for linha in linhas if linha.startswith('H\t')]
    preenchidas = [linha for linha in linhas if linha]
    if len(casas) != 1 or not preenchidas or preenchidas[-1] != 'F':
        raise ValueError('resposta inesperada')
    processos = []
    for linha in linhas:
        partes = linha.split('\t', 4)
        if partes[0] != 'P' or len(partes) != 5:
            continue
        _, pid, ppid, inicio, exe = partes
        if not all(_INTEIRO.fullmatch(campo) for campo in (pid, ppid, inicio)) or not exe.startswith('/'):
            continue
        processos.append({"pid": int(pid), "ppid": int(ppid), "inicio": int(inicio), "exe": exe})
    return casas[0], processos


def agregar(processos, tabela):
    """Conta raízes por produto. Filho = pai direto reconhecido, do mesmo produto e iniciado antes (ou junto)."""
    reconhecidos = {}
    for processo in processos:
        produto = classificar(processo['exe'], tabela)
        if produto is not None:
            reconhecidos[processo['pid']] = (produto, processo['ppid'], processo['inicio'])
    instancias = {}
    for pid, (produto, ppid, inicio) in reconhecidos.items():
        pai = reconhecidos.get(ppid) if ppid != pid else None
        if pai is not None and pai[0] == produto and pai[2] <= inicio:
            continue
        instancias[produto] = instancias.get(produto, 0) + 1
    return [{"produto": linha["produto"], "rotulo": linha["rotulo"], "familia": linha["familia"],
             "tipo": linha["tipo"], "instancias": instancias[linha["produto"]]}
            for linha in tabela if linha["produto"] in instancias]


def _rodar(ctx, argv, timeout):
    if 'executar' in ctx:
        return ctx['executar'](argv, timeout=timeout)
    r = subprocess.run(argv, capture_output=True, timeout=timeout, **sem_janela_kwargs())
    if r.returncode != 0:
        raise OSError('wsl.exe retornou %s' % r.returncode)
    return r.stdout


def ler(ctx):
    distro = ctx.get('distro', DISTRO)
    if not isinstance(distro, str) or not _NOME_DISTRO.fullmatch(distro):
        return {"erro": ERRO_DISTRO}
    if 'executar' not in ctx and os.name != 'nt':
        return {"erro": ERRO_PLATAFORMA}
    # Checagem imediatamente antes da consulta, sem cache: na dúvida, a distro não é tocada.
    try:
        bruto = _rodar(ctx, ARGV_CHECAGEM, T_CHECAGEM_S)
        ligadas = distros_ligadas(bruto)
    except Exception:
        return {"erro": ERRO_CHECAGEM}
    if distro.lower() not in ligadas:
        return {"presenca_linux": {"estado": "parada", "host": distro, "itens": []}}
    try:
        saida = _rodar(ctx, ['wsl.exe', '-d', distro, '-e', 'sh', '-c', SCRIPT], T_CONSULTA_S)
    except Exception:
        return {"erro": ERRO_CONSULTA}
    try:
        home, processos = interpretar(saida)
        tabela = tabela_resolvida(home)
    except ValueError:
        return {"erro": ERRO_FORMATO}
    return {"presenca_linux": {"estado": "ok", "host": distro, "itens": agregar(processos, tabela)}}
