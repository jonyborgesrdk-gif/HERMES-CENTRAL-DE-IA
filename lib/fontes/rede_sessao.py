"""E28: detalhe ETW somente local; identidade = PID + criação Win32, nunca cmdline.

Lê um arquivo limitado e enumera processos via Toolhelp/ctypes da presença. Não
infere identificadores de transcript a partir de produto, horário ou atividade.
"""
import ipaddress
import json
import math
import ntpath
import os
from collections import Counter
from pathlib import Path

from . import agora, diretorio, presenca_processos

ARQUIVO = 'rede-sessao.json'
MAX_ARQUIVO = 2 * 1024 * 1024
MAX_FLUXOS = 4096
MAX_PIDS = 8192
MAX_PROCESSOS = 16384
MAX_SESSOES = 100
MAX_CONEXOES = 2000
MAX_CONEXOES_SESSAO = 100
MAX_PROFUNDIDADE = 64
MAX_BYTES = 10 ** 15
ERRO = 'rede por sessão: sem leitura'
INSTRUCAO = 'Inicie painel-rede capturar --detalhe em terminal de administrador (após encerrar sua captura anterior).'
AGENTES = {'claude_cli': 'claude', 'codex_cli': 'codex', 'grok_cli': 'grok',
           'hermes_cli': 'hermes', 'cursor_agent': 'cursor-agent'}


def inteiro(v, limite=MAX_BYTES):
    return type(v) is int and 0 <= v <= limite


def par(v):
    if not isinstance(v, list) or len(v) != 2 or not all(inteiro(n) for n in v):
        raise ValueError(ERRO)
    return v[:]


def somar(destino, origem):
    for chave in ('janela', 'total'):
        for i in range(2):
            destino[chave][i] = min(MAX_BYTES, destino[chave][i] + origem[chave][i])


def vazio():
    return {'janela': [0, 0], 'total': [0, 0]}


def identidade(item):
    pid, criado = item.get('pid'), item.get('criado')
    if not inteiro(pid, 0xffffffff) or not pid:
        raise ValueError(ERRO)
    if criado is not None and (not inteiro(criado, 0xffffffffffffffff) or not criado):
        raise ValueError(ERRO)
    return pid, criado


def snapshot(listar):
    """Somente campos necessários; ausência de criação nunca vira identidade válida."""
    return {p['pid']: p for p in listar(lambda _: True)[:MAX_PROCESSOS]
            if isinstance(p, dict) and inteiro(p.get('pid'), 0xffffffff) and p['pid']}


def raiz(pid, processos, tabela):
    vistos, candidata, agente = set(), None, None
    for _ in range(MAX_PROFUNDIDADE):
        if pid in vistos:
            return None
        vistos.add(pid)
        p = processos.get(pid)
        if p is None or not inteiro(p.get('criado'), 0xffffffffffffffff) or not p['criado']:
            return None
        caminho = p.get('caminho')
        produto = presenca_processos.classificar(caminho, tabela) if isinstance(caminho, str) else None
        encontrado = AGENTES.get(produto)
        if encontrado:
            if agente is not None and encontrado != agente:
                return candidata
            agente, candidata = encontrado, (encontrado, pid, p['criado'])
        pai = processos.get(p.get('ppid'))
        if pai is None:
            return candidata
        # Um pai reutilizado depois do nascimento do filho não é seu ancestral.
        if (not inteiro(pai.get('criado'), 0xffffffffffffffff) or not pai['criado']
                or pai['criado'] > p['criado']):
            return candidata
        pid = pai['pid']
    return None


def montar(documento, processos, tabela, agora_s):
    if not isinstance(documento, dict) or documento.get('schema') != 1:
        raise ValueError(ERRO)
    gerado, desde = documento.get('gerado_em'), documento.get('desde')
    for valor in (gerado, desde):
        if type(valor) not in (int, float) or not 0 < valor < 10 ** 11 or not math.isfinite(valor):
            raise ValueError(ERRO)
    if gerado > agora_s + 300 or desde > gerado:
        raise ValueError(ERRO)
    estado = documento.get('estado')
    if estado not in ('ativo', 'pausado_carga', 'encerrado', 'erro'):
        raise ValueError(ERRO)
    pids, fluxos = documento.get('pids'), documento.get('fluxos')
    if (not isinstance(pids, list) or len(pids) > MAX_PIDS
            or not isinstance(fluxos, list) or len(fluxos) > MAX_FLUXOS):
        raise ValueError(ERRO)
    destinos = {pid: raiz(pid, processos, tabela) for pid in processos}
    quantidades = Counter(destinos.values())
    sessoes, donos, vistos = {}, {}, set()
    outros = dict(vazio(), processos=0, nao_atribuidos=vazio())
    sem_identidade = documento.get('nao_atribuidos', vazio())
    if not isinstance(sem_identidade, dict):
        raise ValueError(ERRO)
    desconhecidos = {k: par(sem_identidade.get(k)) for k in ('janela', 'total')}
    somar(outros, desconhecidos)
    somar(outros['nao_atribuidos'], desconhecidos)
    for item in pids:
        if not isinstance(item, dict):
            raise ValueError(ERRO)
        ident = identidade(item)
        if ident in vistos:
            raise ValueError(ERRO)
        vistos.add(ident)
        contagem = {k: par(item.get(k)) for k in ('janela', 'total')}
        pid, criado = ident
        p = processos.get(pid)
        valido = criado is not None and p is not None and p.get('criado') == criado
        destino = destinos.get(pid) if valido else None
        donos[ident] = destino
        if destino is None:
            somar(outros, contagem)
            outros['processos'] += 1
            # Sem árvore/identidade confiável não se afirma que é outro produto.
            if not valido or raiz(pid, processos, tabela) is None:
                somar(outros['nao_atribuidos'], contagem)
            continue
        agente, pid_raiz, criado_raiz = destino
        sessao = sessoes.setdefault(destino, dict(vazio(), agente=agente, pid_raiz=pid_raiz,
            inicio_epoch=criado_raiz / 10 ** 7 - 11644473600,
            sessao=None, processos=quantidades[destino],
            conexoes=[], conexoes_omitidas=0))
        somar(sessao, contagem)
    conexoes = []
    for item in fluxos:
        if not isinstance(item, dict):
            raise ValueError(ERRO)
        ident = identidade(item)
        if ident not in donos or item.get('proto') not in ('tcp', 'udp'):
            raise ValueError(ERRO)
        local, remoto = item.get('local_porta'), item.get('remoto_porta')
        if not inteiro(local, 65535) or not inteiro(remoto, 65535):
            raise ValueError(ERRO)
        if not isinstance(item.get('remoto'), str):
            raise ValueError(ERRO)
        endereco = str(ipaddress.ip_address(item['remoto']))
        c = {k: par(item.get(k)) for k in ('janela', 'total')}
        p = processos.get(ident[0], {})
        nome = ntpath.basename(p.get('nome') or '')[:128] if donos[ident] is not None else ''
        c.update(proto=item['proto'], local_porta=local, remoto=endereco,
                 remoto_porta=remoto, processo=nome)
        conexoes.append((donos[ident], c))
    ordenadas = sorted(sessoes.values(), key=lambda s: (-sum(s['janela']), s['agente'], s['pid_raiz']))
    exibidas = {(s['agente'], s['pid_raiz']): s for s in ordenadas[:MAX_SESSOES]}
    omitidas = len(ordenadas) - len(exibidas)
    conexoes.sort(key=lambda dupla: -sum(dupla[1]['janela']))
    quantidade = 0
    for destino, conexao in conexoes:
        if destino is None:
            continue
        sessao = exibidas.get(destino[:2])
        if sessao is None:
            continue
        if len(sessao['conexoes']) >= MAX_CONEXOES_SESSAO or quantidade >= MAX_CONEXOES:
            sessao['conexoes_omitidas'] += 1
        else:
            sessao['conexoes'].append(conexao)
            quantidade += 1
    contadores = {}
    for chave in ('fluxos_omitidos', 'pids_omitidos'):
        valor = documento.get(chave, 0)
        if not inteiro(valor):
            raise ValueError(ERRO)
        contadores[chave] = valor
    return dict(estado='parado' if agora_s - gerado > 30 else estado,
        gerado_em_epoch=gerado, desde_epoch=desde, idade_s=max(0, agora_s-gerado),
        janela_s=60, sessoes=ordenadas[:MAX_SESSOES], sessoes_omitidas=omitidas,
        outros=outros, **contadores)


def ler(ctx):
    caminho = Path(ctx.get('rede_sessao') or diretorio(ctx) / ARQUIVO)
    try:
        with caminho.open('rb') as arquivo:
            bruto = arquivo.read(MAX_ARQUIVO + 1)
        if len(bruto) > MAX_ARQUIVO:
            raise ValueError(ERRO)
        documento = json.loads(bruto)
        processos = snapshot(ctx.get('listar_processos', presenca_processos.listar_windows))
        tabela = presenca_processos.tabela_resolvida(ctx.get('ambiente', os.environ))
        return {'rede_sessao': montar(documento, processos, tabela, agora(ctx))}
    except FileNotFoundError:
        return {'rede_sessao': {'estado': 'sem captura detalhada', 'instrucao': INSTRUCAO}}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return {'erro': ERRO}
