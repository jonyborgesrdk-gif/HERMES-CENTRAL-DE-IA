"""Mascaramento conservador, puro e sem I/O; não garante remoção de segredos.

Regex só localiza âncoras, com quantificadores limitados e sem repetição aninhada.
Valores/tokens/PEM longos são percorridos por cursores, nunca por backtracking.
Falsos positivos deliberados: UUID, hash Git, caminho longo sem pontos e Basic usage.
"""
import json
import ntpath
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
_CONTROLES_COMANDOS = dict(_CONTROLES)
_CONTROLES_COMANDOS.update({10: '\n', 13: '\r'})
_ASPAS_ESPACOS = str.maketrans({'"': ' ', "'": ' '})
_QUEBRAS_SEPARADORES = str.maketrans({'\n': ';', '\r': ';'})
_USUARIO = re.compile(r'''[^\s;\&|<>"'\}\]]{0,256}''')
# (programa, subcomando opcional): (flag exata, aceita valor junto à flag).
SENHAS_PROGRAMA = {
    ('docker', 'login'): (('-p', False), ('--password', False)),
    ('az', 'login'): (('-p', False),),
    ('redis-cli', None): (('-a', False),),
    ('7z', None): (('-p', True),),
    ('zip', None): (('-P', False),),
    ('unzip', None): (('-P', False),),
    ('mysql', None): (('-p', True),),
    ('mariadb', None): (('-p', True),),
    ('sshpass', None): (('-p', True),),
    ('sqlcmd', None): (('-P', False),),
    ('ldapsearch', None): (('-w', False),),
    ('ssh-keygen', None): (('-N', False),),
    ('openssl', None): (('-k', False), ('-pass', False)),
}


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


def _segmentos_shell(texto, inicio, limite):
    """Intervalos relativos ao texto original; aspas não são removidas do texto."""
    tokens, pos, pipe = [], inicio, None
    while pos < limite:
        if texto[pos].isspace():
            pos += 1
            continue
        if texto[pos] in '|;&':
            yield tokens, pipe
            tokens = []
            pipe = pos if texto[pos] == '|' else None
            pos += 1
            continue
        inicio, aspas = pos, None
        while pos < limite:
            char = texto[pos]
            if char in ('"', "'"):
                aspas = None if char == aspas else char if aspas is None else aspas
            elif aspas is None and (char.isspace() or char in '|;&'):
                break
            pos += 1
        tokens.append((inicio, pos, texto[inicio:pos].strip('\"\'')))
    yield tokens, pipe


def _senhas_programa(texto):
    """Um cursor por trecho e até quatro níveis de wrappers; nunca executa shell."""
    minusculo = texto.lower()
    if not any(p in minusculo for p in ('docker', 'az', 'redis-cli', '7z', 'zip', 'mysql',
                                      'mariadb', 'sshpass', 'sqlcmd', 'ldapsearch', 'ssh-keygen', 'openssl', 'net')):
        return []
    intervalos = []
    fila = [(0, len(texto), 0)]
    wrappers = {'bash': ('-c', '-lc'), 'sh': ('-c', '-lc'), 'cmd': ('/c',),
                'powershell': ('-command',)}
    while fila:
        inicio_trecho, fim_trecho, nivel = fila.pop()
        for tokens, pipe in _segmentos_shell(texto, inicio_trecho, fim_trecho):
            regras, stdin_pipe, rede, oculto_ate = (), None, 0, -1
            for indice, (inicio, fim, valor) in enumerate(tokens):
                if inicio < oculto_ate:
                    continue
                # net use: unidade e opções podem anteceder a senha, após o UNC.
                if rede == 1 and valor.startswith('\\'):
                    rede = 2
                    continue
                if rede == 2:
                    if valor.startswith('/'):
                        continue
                    intervalos.append((inicio, fim))
                    rede = 0
                    continue
                programa = ntpath.basename(valor.lstrip('$&{').rstrip('}')).lower().removesuffix('.exe')
                if programa.startswith('mysql'):
                    programa = 'mysql'
                elif programa.startswith('mariadb'):
                    programa = 'mariadb'
                sub = tokens[indice + 1][2].lower() if indice + 1 < len(tokens) else None
                if programa in wrappers and sub in wrappers[programa] and indice + 2 < len(tokens) and nivel < 4:
                    a, b, _ = tokens[indice + 2]
                    if b - a >= 2 and texto[a] in ('"', "'") and texto[b - 1] == texto[a]:
                        fila.append((a + 1, b - 1, nivel + 1))
                if programa == 'net' and sub == 'use':
                    rede = 1
                if (programa, sub) in SENHAS_PROGRAMA or (programa, None) in SENHAS_PROGRAMA:
                    regras = SENHAS_PROGRAMA.get((programa, sub), SENHAS_PROGRAMA.get((programa, None), ()))
                    stdin_pipe = pipe if programa == 'docker' and sub == 'login' else None
                if valor == '--password-stdin' and stdin_pipe is not None:
                    intervalos.append((0, stdin_pipe))
                for flag, junto in regras:
                    if valor == flag or valor.startswith(flag + '='):
                        if valor.startswith(flag + '='):
                            intervalo = _valor(texto, inicio + len(flag) + 1)
                        elif indice + 1 < len(tokens):
                            intervalo = (tokens[indice + 1][0], tokens[indice + 1][1])
                        else:
                            continue
                        intervalos.append(intervalo)
                        oculto_ate = intervalo[1]
                        break
                    if junto and valor.startswith(flag) and len(valor) > len(flag):
                        intervalos.append((inicio + len(flag), fim))
                        break
    return intervalos


def _continuacoes_comandos(texto):
    """Cópia de mesmo comprimento: uma passagem, sem procurar prefixos de novo."""
    caracteres, ultimo, ultimo_pos = list(texto), None, None
    for pos, char in enumerate(texto):
        if char in ('\n', '\r'):
            if char == '\r' and texto[pos + 1:pos + 2] == '\n':
                caracteres[pos] = ' '  # CR de CRLF não encerra a linha antes do LF.
                continue
            caracteres[pos] = ' ' if ultimo in ('\\', '`', '^', '|', '&') else ';'
            if ultimo in ('\\', '`', '^') and (ultimo_pos == 0
                    or texto[ultimo_pos - 1].isspace() or texto[ultimo_pos - 1] in '|;&'):
                caracteres[ultimo_pos] = ' '  # Token isolado; não apagar a barra de um caminho.
            ultimo, ultimo_pos = char, pos
        elif not char.isspace():
            ultimo, ultimo_pos = char, pos
    return ''.join(caracteres)


def _mascarar_texto(texto, sequencias_longas=True):
    # Só apresentação: nenhuma destas strings é executada pelo painel.
    comandos = texto.translate(_CONTROLES_COMANDOS).replace('\\"', '"').replace("\\'", "'")
    separados = comandos.translate(_QUEBRAS_SEPARADORES)
    comandos = _continuacoes_comandos(comandos)
    texto = limpar_exibicao(texto)
    texto = texto.replace('\\"', '"').replace("\\'", "'")
    intervalos, pos = _senhas_programa(comandos), 0
    intervalos.extend(_senhas_programa(comandos.translate(_ASPAS_ESPACOS)))
    intervalos.extend(_senhas_programa(separados))  # Preserva as ocultações sem continuação.
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
    especiais = any(parte in maiusculo for parte in ('-U ', '--USER '))
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
                inicio = _espacos(texto, pos)
                if texto[inicio:inicio + 1] in ('"', "'"):
                    aspas = texto[inicio]
                    inicio += 1
                    limite = min(len(texto), inicio + 256)
                    fim = texto.find(aspas, inicio, limite)
                    candidato = (inicio, limite if fim < 0 else fim)
                else:
                    candidato = (inicio, _USUARIO.match(texto, inicio).end())
                if ':' in texto[candidato[0]:candidato[1]]:
                    intervalo = _valor(texto, pos)
                # Mesmo candidato recusado tem custo limitado e o cursor avança.
                oculto_ate = match.end()
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
    for padrao in ((_LONGO, _CONHECIDO, _JWT) if sequencias_longas else (_CONHECIDO, _JWT)):
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


def mascarar(texto, *, sequencias_longas=True):
    """Recebe/devolve str; JSON válido é percorrido para tratar campos aninhados."""
    if isinstance(texto, list) and all(isinstance(item, str) for item in texto):
        texto = ' '.join(texto)  # Forma argv; mantém o contexto entre programa, flag e valor.
    def limpar(valor):
        if isinstance(valor, dict):
            return {_mascarar_texto(chave, sequencias_longas): OCULTO if sensivel(chave) else limpar(item)
                    for chave, item in valor.items()}
        if isinstance(valor, list):
            return [limpar(item) for item in valor]
        return _mascarar_texto(valor, sequencias_longas) if isinstance(valor, str) else valor

    if texto.lstrip()[:1] in ('{', '['):
        try:
            valor = json.loads(texto)
            return json.dumps(limpar(valor), ensure_ascii=False, separators=(',', ':'))
        except (ValueError, RecursionError):
            pass
    return _mascarar_texto(texto, sequencias_longas)
