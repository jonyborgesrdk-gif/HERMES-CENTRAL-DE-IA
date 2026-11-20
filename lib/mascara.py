"""Mascaramento conservador, puro e sem I/O; não garante remoção de segredos.

Regex só localiza âncoras, com quantificadores limitados e sem repetição aninhada.
Valores/tokens/PEM longos são percorridos por cursores, nunca por backtracking.
Falsos positivos deliberados: UUID, hash Git, caminho longo sem pontos e Basic usage.
"""
import json
import re

OCULTO = '‹oculto›'
NOMES_SENSIVEIS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSPHRASE', 'PASSWD',
                    'PASS', 'PWD', 'PIN', 'AUTH', 'COOKIE', 'SESSION', 'CREDENTIAL',
                    'SIGNATURE', 'SIG')
_IDENT = re.compile(r'[\w.-]{1,64}')
_URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]{0,31}://')
_LONGO = re.compile(r'[A-Za-z0-9_+/-]{32,64}')
_CONHECIDO = re.compile(r'(?:sk-|xai-|gh[pousr]_|github_pat_|glpat-|AKIA|AIza|xox[abp]-)[A-Za-z0-9_-]{1,64}')
_JWT = re.compile(r'eyJ[A-Za-z0-9_-]{0,64}\.eyJ[A-Za-z0-9_-]{0,64}\.[A-Za-z0-9_-]{1,64}')
_BASE64 = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+/-=')
_CONTROLES = dict.fromkeys(list(range(32)) + list(range(127, 160))
                           + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A))
                           + [0x200E, 0x200F])
_CONTROLES.update({c: ' ' for c in (9, 10, 11, 12, 13)})


def limpar_exibicao(texto):
    return texto.translate(_CONTROLES)


def sensivel(nome):
    normal = nome.upper()
    return len(nome) >= 3 and any(parte in normal for parte in NOMES_SENSIVEIS)


def _espacos(texto, pos):
    teto = min(len(texto), pos + 64)
    while pos < teto and texto[pos].isspace():
        pos += 1
    return pos


def _valor(texto, pos, aspas=None):
    """Intervalo do valor; aspas abertas no prefixo pertencem ao comando set/header."""
    pos = _espacos(texto, pos)
    inicio = pos
    if pos >= len(texto):
        return pos, pos
    if texto[pos] in "\"'":
        aspas = texto[pos]
        pos += 1
    if aspas:
        fim = texto.find(aspas, pos)
        return inicio, len(texto) if fim < 0 else fim + (texto[inicio] == aspas)
    while pos < len(texto) and not texto[pos].isspace() and texto[pos] not in ";&|<>\"'}]":
        pos += 1
    return inicio, pos


def _aplicar(texto, intervalos):
    partes, cursor = [], 0
    for inicio, fim in sorted(intervalos):
        if fim <= cursor or fim <= inicio:
            continue
        if inicio > cursor:
            partes.extend((texto[cursor:inicio], OCULTO))
        elif inicio == cursor:
            partes.append(OCULTO)
        cursor = fim
    partes.append(texto[cursor:])
    return ''.join(partes)


def _mascarar_texto(texto):
    # Só apresentação: nenhuma destas strings é executada pelo painel.
    texto = limpar_exibicao(texto)
    texto = texto.replace('\\"', '"').replace("\\'", "'")
    intervalos, pos = [], 0
    while True:
        inicio = texto.find('-----BEGIN ', pos)
        if inicio < 0:
            break
        cabecalho = texto.find('KEY-----', inicio, inicio + 96)
        if cabecalho < 0:
            pos = inicio + 11
            continue
        fim = texto.find('-----END ', cabecalho + 8)
        encerramento = texto.find('KEY-----', fim, fim + 96) if fim >= 0 else -1
        pos = encerramento + 8 if encerramento >= 0 else len(texto)
        intervalos.append((inicio, pos))
    for match in _URL.finditer(texto):
        inicio = match.end()
        fim = inicio
        while fim < len(texto) and not texto[fim].isspace() and texto[fim] not in '/?#\"\'':
            fim += 1
        arroba = texto.rfind('@', inicio, fim)
        if arroba >= 0 and ':' in texto[inicio:arroba]:
            intervalos.append((inicio, arroba))

    maiusculo = texto.upper()
    tem_nome = any(parte in maiusculo for parte in NOMES_SENSIVEIS + ('BEARER', 'BASIC', 'SECURESTRING'))
    especiais = any(parte in maiusculo for parte in (' -U ', ' --USER ', 'MYSQL', 'SSHPASS', 'SQLCMD'))
    if (tem_nome or especiais) and any(c in texto for c in ('=', ':', ' ', '\t', '\n')):
        oculto_ate = -1
        for match in _IDENT.finditer(texto):
            if match.start() < oculto_ate:
                continue
            nome, pos = match[0], match.end()
            normal = nome.upper()
            intervalo = None
            if normal == 'SETX':
                inicio_nome = _espacos(texto, pos)
                proximo = _IDENT.match(texto, inicio_nome)
                if proximo and sensivel(proximo[0]):
                    intervalo = _valor(texto, proximo.end())
            elif normal == 'SETENVIRONMENTVARIABLE':
                pos = _espacos(texto, pos)
                if texto[pos:pos + 1] == '(':
                    pos = _espacos(texto, pos + 1)
                    aspas = texto[pos:pos + 1]
                    if aspas in ('"', "'"):
                        fim_nome = texto.find(aspas, pos + 1, pos + 131)
                        if fim_nome >= 0 and sensivel(texto[pos + 1:fim_nome]):
                            pos = _espacos(texto, fim_nome + 1)
                            if texto[pos:pos + 1] == ',':
                                intervalo = _valor(texto, pos + 1)
            elif normal in ('BEARER', 'BASIC', 'CONVERTTO-SECURESTRING'):
                intervalo = _valor(texto, pos)
            elif normal in ('-U', '--USER'):
                candidato = _valor(texto, pos)
                if ':' in texto[candidato[0]:candidato[1]]:
                    intervalo = candidato
            elif normal.startswith('-P') and 'MYSQL' in maiusculo and len(nome) > 2:
                intervalo = _valor(texto, match.start() + 2)
            elif nome == '-p' and 'SSHPASS' in maiusculo or nome == '-P' and 'SQLCMD' in maiusculo:
                intervalo = _valor(texto, pos)
            elif sensivel(nome):
                anterior = texto[match.start() - 1:match.start()] if match.start() else ''
                pos = _espacos(texto, pos)
                fechou_aspas = texto[match.end():match.end() + 1] in ('"', "'")
                if fechou_aspas:
                    pos = _espacos(texto, pos + 1)
                    if texto[pos:pos + 1] == ']':
                        pos = _espacos(texto, pos + 1)
                if texto[pos:pos + 1] in ('=', ':'):
                    aspas = anterior if anterior in ('"', "'") and not fechou_aspas else None
                    intervalo = _valor(texto, pos + 1, aspas)
                elif nome.startswith('-') and pos > match.end():
                    intervalo = _valor(texto, pos)
            if intervalo and intervalo[1] > intervalo[0]:
                intervalos.append(intervalo)
                oculto_ate = intervalo[1]
    for padrao in (_LONGO, _CONHECIDO, _JWT):
        oculto_ate = -1
        for match in padrao.finditer(texto):
            if match.start() < oculto_ate:
                continue
            fim = match.end()
            while fim < len(texto) and texto[fim] in _BASE64:
                fim += 1
            intervalos.append((match.start(), fim))
            oculto_ate = fim
    return limpar_exibicao(_aplicar(texto, intervalos))


def mascarar(texto):
    """Recebe/devolve str; JSON válido é percorrido para tratar campos aninhados."""
    def limpar(valor):
        if isinstance(valor, dict):
            return {_mascarar_texto(chave): OCULTO if sensivel(chave) else limpar(item)
                    for chave, item in valor.items()}
        if isinstance(valor, list):
            return [limpar(item) for item in valor]
        return _mascarar_texto(valor) if isinstance(valor, str) else valor

    if texto.lstrip()[:1] in ('{', '['):
        try:
            valor = json.loads(texto)
            return json.dumps(limpar(valor), ensure_ascii=False, separators=(',', ':'))
        except (ValueError, RecursionError):
            pass
    return _mascarar_texto(texto)
