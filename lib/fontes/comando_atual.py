"""E27: chamadas locais, mascaradas na entrada do cache, sem escrita/rede/subprocesso.

JSONL: só a cauda inicial e bytes novos (até 256 KiB por arquivo/passada).
Zstandard: cursor/descompressor em memória; até 256 KiB de entrada E saída
por passada. Não publica um prefixo ainda em aquecimento como se fosse atual.
Não grava estado. IDs completos/caminhos só existem na memória privada da fonte.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import ntpath
import os
from pathlib import Path
import re
import sqlite3
import stat

from . import agora, epoch
try:
    from ..mascara import limpar_exibicao, mascarar
except ImportError:
    from mascara import limpar_exibicao, mascarar
try:
    from compression import zstd
except ImportError:  # Python anterior a 3.14: os JSONL continuam disponíveis.
    zstd = None
ERROS_LEITURA = (OSError, ValueError, EOFError)
if zstd is not None:
    ERROS_LEITURA += (zstd.ZstdError,)

AGENTES = ('claude', 'codex', 'grok', 'hermes')
LIMITE_ATIVIDADE_S = 600
MAX_SESSOES = 30
JANELA = 256 * 1024
MAX_CHAMADAS = 256
MAX_MENSAGENS = 128
MAX_TEXTO = 2000
MAX_ENTRADA = 4 * MAX_TEXTO
INTERVALO_DESCOBERTA = 30
JANELA_STAT_S = 24 * 60 * 60
MAX_TOOL_CALLS = 65536
ERRO = 'comando atual: sem leitura'
_CACHE = {}
_UUID = re.compile(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}')
_SHELL = {'bash', 'shell', 'terminal', 'exec_command', 'run_terminal_command',
          'shell_command', 'run_command', 'execute', 'powershell'}
_EDICAO = {'edit', 'write', 'write_file', 'search_replace', 'patch', 'apply_patch', 'multiedit'}
_LEITURA = {'read', 'read_file', 'skill_view', 'list_dir'}
_BUSCA = {'grep', 'glob', 'search_files', 'search', 'find', 'rg'}
_REDE = {'web_search', 'web_fetch', 'web_extract', 'browser_exec', 'fetch', 'http_get'}
SQL_SESSOES = ("SELECT id, last_activity_at FROM sessions WHERE ended_at IS NULL "
               "AND typeof(last_activity_at) IN ('real','integer') AND last_activity_at >= ? "
               "AND last_activity_at <= ? ORDER BY last_activity_at DESC")
SQL_MENSAGENS = ("SELECT role, tool_calls, tool_call_id, timestamp FROM messages "
                 "WHERE session_id = ? AND (tool_calls IS NULL OR "
                 "(typeof(tool_calls) = 'text' AND length(tool_calls) <= 65536)) "
                 "ORDER BY timestamp DESC, id DESC LIMIT ?")
SQL_MAX_ID = "SELECT max(id) FROM messages WHERE session_id = ?"


def _json(texto):
    try:
        return json.loads(texto)
    except (ValueError, TypeError, RecursionError):
        return None


def _instante(valor, padrao):
    try:
        numero = epoch(valor)
        return numero if math.isfinite(numero) and 0 < numero <= padrao + 300 else padrao
    except (ValueError, TypeError, OverflowError, OSError):
        return padrao


def _cortar(texto, teto):
    return texto if len(texto) <= teto else texto[:teto - 1] + '…'


def _nome(nome):
    return limpar_exibicao(nome[:MAX_ENTRADA]).rsplit('__', 1)[-1].strip().lower() if isinstance(nome, str) else ''


def _e_edicao(nome):
    nome = _nome(nome)
    return nome in _EDICAO or any(parte in nome for parte in (
        'edit', 'write', 'patch', 'replace', 'apply_diff', 'create_file', 'update_file',
        'save_file', 'append_file', 'insert_file'))


def _caminhos(argumentos):
    """Editores jamais mostram corpo, inclusive patch textual/V4A sem path."""
    caminhos = []
    if isinstance(argumentos, dict):
        for campo in ('file_path', 'path', 'file', 'target_file'):
            if isinstance(argumentos.get(campo), str):
                caminhos.append(argumentos[campo])
        for campo in ('patch', 'diff', 'input', 'content', 'text', 'edits'):
            valor = argumentos.get(campo)
            if isinstance(valor, (str, dict, list)):
                extraidos = _caminhos(valor)
                if extraidos != 'sem caminho':
                    caminhos.append(extraidos)
    elif isinstance(argumentos, list):
        for item in argumentos[:MAX_CHAMADAS]:
            extraidos = _caminhos(item)
            if extraidos != 'sem caminho':
                caminhos.append(extraidos)
    elif isinstance(argumentos, str):
        for linha in argumentos.splitlines():
            for prefixo in ('*** Update File: ', '*** Add File: ', '*** Delete File: '):
                if linha.startswith(prefixo):
                    caminhos.append(linha[len(prefixo):].strip())
                    break
            if len(caminhos) >= MAX_CHAMADAS:
                break
    return '; '.join(dict.fromkeys(caminhos)) if caminhos else 'sem caminho'


def _tem_patch(valor):
    if isinstance(valor, str):
        return '*** Begin Patch' in valor
    return isinstance(valor, list) and any(_tem_patch(v) for v in valor)


def _principal_bruto(nome, argumentos):
    if isinstance(argumentos, str):
        decodificado = _json(argumentos)
        if decodificado is None:
            return _caminhos(argumentos) if _e_edicao(nome) or _tem_patch(argumentos) else argumentos
        argumentos = decodificado
    comando = argumentos
    if isinstance(argumentos, dict):
        for campo in ('command', 'cmd'):
            if _tem_patch(argumentos.get(campo)):
                return _caminhos(argumentos[campo])
    elif _tem_patch(comando):
        return _caminhos(comando)
    corpo_com_caminho = isinstance(argumentos, dict) and any(
        k in argumentos for k in ('file_path', 'path', 'file', 'target_file')) and any(
        k in argumentos for k in ('content', 'contents', 'new_text', 'file_content', 'body', 'code'))
    if _e_edicao(nome) or corpo_com_caminho or isinstance(argumentos, dict) and any(
            k in argumentos for k in ('old_string', 'new_string', 'patch', 'diff')):
        return _caminhos(argumentos)
    if isinstance(argumentos, dict):
        ferramenta = _nome(nome)
        campos = (('command', 'cmd') if ferramenta in _SHELL else
                  ('file_path', 'path', 'file') if ferramenta in _EDICAO | _LEITURA else
                  ('pattern', 'query') if ferramenta in _BUSCA else ())
        for campo in campos:
            if isinstance(argumentos.get(campo), str):
                return argumentos[campo]
            if isinstance(argumentos.get(campo), list) and all(isinstance(x, str) for x in argumentos[campo]):
                return ' '.join(argumentos[campo])
    if isinstance(argumentos, list) and all(isinstance(x, str) for x in argumentos):
        return ' '.join(argumentos)
    return json.dumps(argumentos, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _principal(nome, argumentos):
    principal = _principal_bruto(nome, argumentos)
    # Defesa final para reservas JSON e campos desconhecidos/aninhados.
    return _caminhos(argumentos) if '*** Begin Patch' in principal else principal


def tipo_tarefa(nome, texto):
    """Inferência por vocabulário fixo; não executa nem valida o comando."""
    ferramenta = _nome(nome)
    if _e_edicao(nome):
        return 'edição'
    if ferramenta in _LEITURA:
        return 'leitura'
    if ferramenta in _BUSCA:
        return 'busca'
    if ferramenta in _REDE:
        return 'rede'
    if ferramenta in _SHELL:
        regras = (('testes', r'\b(?:pytest|unittest|jest|vitest)\b|\bnode\s+--test\b|\b(?:npm|pnpm|yarn|cargo|go)\s+(?:run\s+)?test\b'),
                  ('build', r'\b(?:make|cmake|msbuild)\b|\b(?:npm|pnpm|yarn|cargo|dotnet)\s+(?:run\s+)?build\b'),
                  ('git', r'\bgit(?:\.exe)?\s'),
                  ('busca', r'\b(?:rg|grep|findstr|select-string)\s'),
                  ('leitura', r'\b(?:cat|head|tail|type|get-content)\s'),
                  ('rede', r'\b(?:curl|wget|invoke-webrequest)\s'))
        for tipo, padrao in regras:
            if re.search(padrao, texto, re.I):
                return tipo
        return 'shell'
    return 'outro'


def _novo(ident):
    return {'ident': ident, 'projeto': None, 'chamadas': {}, 'ultima': None,
            'ultima_concluida': None, 'sequencia': 0}


def _chamada(estado, ident, nome, argumentos, quando):
    if not isinstance(nome, str) or not nome:
        return
    try:
        principal = _principal(nome, argumentos)
        texto = mascarar(principal[:MAX_ENTRADA])
        if len(principal) > MAX_ENTRADA:
            texto += '…'
    except (ValueError, TypeError, RecursionError):
        texto = 'sem leitura'
    estado['sequencia'] += 1
    chamada = {'ferramenta': _cortar(mascarar(nome[:MAX_ENTRADA], sequencias_longas=False), 64),
               '_nome_cru': nome, 'texto': _cortar(texto, MAX_TEXTO),
               'desde_epoch': quando, 'tipo_tarefa': tipo_tarefa(nome, texto),
               'tipo_tarefa_inferido': True, 'em_curso': True, '_ordem': estado['sequencia']}
    # Ausência de id não pode fazer todas as chamadas se sobrescreverem.
    chave = ident if isinstance(ident, str) and ident else 'sem-id:%s' % estado['sequencia']
    estado['chamadas'].pop(chave, None)
    estado['chamadas'][chave] = chamada
    while len(estado['chamadas']) > MAX_CHAMADAS:
        estado['chamadas'].pop(next(iter(estado['chamadas'])))
    estado['ultima'] = chamada


def _resultado(estado, ident):
    if isinstance(ident, str):
        chamada = estado['chamadas'].pop(ident, None)
        if chamada is not None:
            chamada['em_curso'] = False
            concluida = estado['ultima_concluida']
            if concluida is None or chamada['_ordem'] > concluida['_ordem']:
                estado['ultima_concluida'] = chamada
                for chave in list(estado['chamadas']):
                    if estado['chamadas'][chave]['_ordem'] < chamada['_ordem']:
                        del estado['chamadas'][chave]


def _funcoes(estado, chamadas, quando):
    if isinstance(chamadas, str):
        chamadas = _json(chamadas)
    if not isinstance(chamadas, list):
        return
    for chamada in chamadas[:MAX_CHAMADAS]:
        if not isinstance(chamada, dict):
            continue
        funcao = chamada.get('function', chamada)
        if isinstance(funcao, dict):
            _chamada(estado, chamada.get('id') or chamada.get('call_id'), funcao.get('name'),
                     funcao.get('arguments', funcao.get('input', {})), quando)


def _interpretar(estado, linha, agente, mtime):
    quando = _instante(linha.get('timestamp', linha.get('ts')), mtime)
    pasta = linha.get('cwd')
    if agente == 'codex':
        p = linha.get('payload')
        if not isinstance(p, dict):
            return
        if linha.get('type') in ('session_meta', 'turn_context'):
            pasta = p.get('cwd')
            ident = p.get('id') or p.get('session_id')
            if linha['type'] == 'session_meta' and isinstance(ident, str):
                estado['ident'] = ident
        tipo = p.get('type', '')
        if tipo in ('custom_tool_call', 'function_call'):
            _chamada(estado, p.get('call_id'), p.get('name'), p.get('input', p.get('arguments', {})), quando)
        elif tipo in ('custom_tool_call_output', 'function_call_output'):
            _resultado(estado, p.get('call_id'))
        elif isinstance(tipo, str) and tipo.startswith('exec_command_'):
            ident = p.get('call_id') or p.get('id')
            if tipo.endswith(('_begin', '_start', '_started')):
                _chamada(estado, ident, 'exec_command', p.get('command', p.get('cmd', {})), quando)
            elif tipo.endswith(('_end', '_output', '_completed')):
                _resultado(estado, ident)
    elif agente == 'claude':
        mensagem = linha.get('message')
        if isinstance(mensagem, dict) and isinstance(mensagem.get('content'), list):
            for bloco in mensagem['content']:
                if not isinstance(bloco, dict):
                    continue
                if linha.get('type') == 'assistant' and bloco.get('type') == 'tool_use':
                    _chamada(estado, bloco.get('id'), bloco.get('name'), bloco.get('input', {}), quando)
                elif linha.get('type') == 'user' and bloco.get('type') == 'tool_result':
                    _resultado(estado, bloco.get('tool_use_id'))
    elif agente == 'grok':
        tipo = linha.get('type')
        mensagem = linha.get('message') if isinstance(linha.get('message'), dict) else linha
        if tipo == 'assistant':
            _funcoes(estado, mensagem.get('tool_calls'), quando)
        elif tipo == 'tool_result':
            _resultado(estado, linha.get('tool_call_id'))
        elif tipo == 'tool_started':
            _chamada(estado, linha.get('tool_call_id') or linha.get('call_id'),
                     linha.get('tool_name'), '', quando)
        elif tipo == 'tool_completed':
            ident = linha.get('tool_call_id') or linha.get('call_id')
            if not ident:
                # Sem id, pareia FIFO e prefere o nome se o evento o informar.
                nome = linha.get('tool_name')
                ident = next((k for k, c in estado['chamadas'].items()
                              if not nome or c['_nome_cru'] == nome), None)
            _resultado(estado, ident)
    if isinstance(pasta, str) and pasta:
        estado['projeto'] = _cortar(mascarar(ntpath.basename(pasta.rstrip('/\\'))[:MAX_ENTRADA]), 128) or None


def _linhas(estado, bruto, agente, mtime):
    for linha in bruto.splitlines():
        valor = _json(linha)
        if isinstance(valor, dict):
            _interpretar(estado, valor, agente, mtime)


def _ler_zst(caminho, estado, info, agente):
    if zstd is None:
        raise OSError(ERRO)
    estado.setdefault('dec', zstd.ZstdDecompressor())
    estado.setdefault('resto', b'')
    estado.setdefault('linha', b'')
    saida, entrada = 0, 0
    with caminho.open('rb') as arquivo:
        arquivo.seek(estado['offset'])
        while saida < JANELA:
            dec = estado['dec']
            if dec.eof:
                estado['resto'] = dec.unused_data
                estado['dec'] = dec = zstd.ZstdDecompressor()
            if dec.needs_input:
                if estado['resto']:
                    bloco, estado['resto'] = estado['resto'], b''
                else:
                    if entrada >= JANELA:
                        break
                    bloco = arquivo.read(min(65536, JANELA - entrada))
                    entrada += len(bloco)
                    estado['offset'] += len(bloco)
                    if not bloco:
                        break
            else:
                bloco = b''
            descomprimido = dec.decompress(bloco, max_length=JANELA - saida)
            saida += len(descomprimido)
            bruto = estado['linha'] + descomprimido
            fim = bruto.rfind(b'\n') + 1
            if estado.get('descartar'):
                primeira = bruto.find(b'\n')
                if primeira < 0:
                    estado['linha'] = b''
                    continue
                bruto = bruto[primeira + 1:]
                estado['descartar'] = False
                fim = bruto.rfind(b'\n') + 1
            _linhas(estado, bruto[:fim], agente, info.st_mtime)
            estado['linha'] = bruto[fim:]
            if len(estado['linha']) > JANELA:
                estado['linha'], estado['descartar'] = b'', True
    # No fim exato do frame, unused_data ainda precisa ser consumido.
    dec = estado['dec']
    estado['aquecendo'] = (estado['offset'] < info.st_size or bool(dec.unused_data)
                           or not dec.needs_input and not dec.eof)


def _ler_arquivo(caminho, agente, cache, info=None):
    info = caminho.stat() if info is None else info
    chave = str(caminho)
    estado = cache.get(chave)
    assinatura = (info.st_size, info.st_mtime_ns, info.st_ino)
    if estado and estado.get('assinatura') == assinatura and not estado.get('aquecendo'):
        return estado
    if (estado is None or estado['offset'] > info.st_size
            or estado['assinatura'][2] != info.st_ino
            or estado['assinatura'] != assinatura and estado['assinatura'][0] == info.st_size):
        ident = _UUID.findall(caminho.name)
        estado = _novo(ident[-1] if ident else caminho.stem)
        estado['offset'] = 0
        estado['assinatura'] = assinatura
        cache[chave] = estado
    if caminho.suffix == '.zst':
        _ler_zst(caminho, estado, info, agente)
    else:
        inicio = max(estado['offset'], info.st_size - JANELA)
        cortado = inicio > estado['offset']
        if cortado:
            novo = _novo(estado['ident'])
            estado.update(novo)  # não reter pares cujo resultado pode estar no trecho omitido
        with caminho.open('rb') as arquivo:
            arquivo.seek(inicio)
            bruto = arquivo.read(JANELA)
        if cortado or estado.get('descartar'):
            primeira = bruto.find(b'\n')
            if primeira < 0:
                estado.update(offset=inicio + len(bruto), descartar=True, assinatura=assinatura)
                return estado
            inicio += primeira + 1
            bruto = bruto[primeira + 1:]
            estado['descartar'] = False
        fim = bruto.rfind(b'\n') + 1
        _linhas(estado, bruto[:fim], agente, info.st_mtime)
        estado['offset'] = inicio + fim
    estado['assinatura'] = assinatura
    return estado


def _item(estado, agente, agora_s=None, atividade=None):
    if estado.get('aquecendo'):
        return None
    pendentes = estado['chamadas']
    chamada = pendentes[next(reversed(pendentes))] if pendentes else estado['ultima']
    concluida = estado['ultima_concluida']
    if chamada is not None and concluida is not None and concluida['_ordem'] > chamada['_ordem']:
        chamada = concluida
    if chamada is None:
        return None
    item = {k: v for k, v in chamada.items() if not k.startswith('_')}
    if item['em_curso'] and agora_s is not None and atividade is not None and agora_s - atividade > LIMITE_ATIVIDADE_S:
        item.update(em_curso=False, sem_resultado=True)
    return dict(item, agente=agente, sessao=mascarar(estado['ident'][:8]), projeto=estado['projeto'])


def _texto_db(bruto):
    try:
        return bruto.decode('utf-8')
    except UnicodeDecodeError:
        return None  # Só a célula inválida; não aborta fetch/session nem as outras linhas.


def _hermes(caminho, agora_s, cache):
    if not caminho.is_file():
        return []
    real = caminho.resolve()
    base = str(real)
    modo = ('?mode=ro' if os.path.isfile(base + '-wal') and os.path.isfile(base + '-shm')
            else '?mode=ro&immutable=1')
    assinatura = (base, real.stat().st_ino)
    anterior = cache.get('_hermes', {})
    if anterior.get('assinatura') != assinatura:
        anterior = {'assinatura': assinatura, 'sessoes': {}}
        cache['_hermes'] = anterior
    estados = anterior['sessoes']
    vistos = set()
    itens = []
    with closing(sqlite3.connect(real.as_uri() + modo, uri=True, timeout=0.1)) as db:
        db.text_factory = _texto_db
        sessoes = db.execute(SQL_SESSOES, (agora_s - LIMITE_ATIVIDADE_S, agora_s))
        for ident, atividade in sessoes:
            if not isinstance(ident, str) or not ident or not math.isfinite(atividade):
                continue
            vistos.add(ident)
            max_id = db.execute(SQL_MAX_ID, (ident,)).fetchone()[0]
            registro = estados.get(ident)
            if registro is None or registro['max_id'] != max_id:
                estado = _novo(ident)
                mensagens = db.execute(SQL_MENSAGENS, (ident, MAX_MENSAGENS)).fetchall()
                for role, chamadas, resultado, quando in reversed(mensagens):
                    if role == 'assistant':
                        _funcoes(estado, chamadas, _instante(quando, atividade))
                    elif role == 'tool':
                        _resultado(estado, resultado)
                registro = {'max_id': max_id, 'estado': estado}
                estados[ident] = registro
            estado = registro['estado']
            item = _item(estado, 'hermes')
            if item:
                itens.append((atividade, item))
                if len(itens) >= MAX_SESSOES:
                    break  # Limite só depois do filtro de chamada válida.
    for ident in list(estados):
        if ident not in vistos:
            del estados[ident]
    return itens


def _preparar_vigias(raizes, agora_s, vigias):
    for agente, raiz, _ in raizes:
        nivel = {'claude': 'claude_raiz', 'grok': 'grok_raiz'}.get(agente, 'codex')
        vigias.setdefault(raiz, [agente, nivel, ()])
        if agente == 'codex':
            dias = {datetime.fromtimestamp(agora_s).date(),
                    datetime.fromtimestamp(agora_s, timezone.utc).date()}
            dias |= {dia - timedelta(days=1) for dia in dias}
            for dia in dias:
                pasta = raiz / ('%04d' % dia.year) / ('%02d' % dia.month) / ('%02d' % dia.day)
                vigias.setdefault(pasta, [agente, 'codex', ()])


def _examinar_vigias(lista, reconciliar, agora_s):
    """Stat DIRETO; nunca aproveitar o DirEntry.stat em cache da listagem do pai."""
    vigias = lista['vigias']
    fila = list(vigias)
    filhos = {'claude_raiz': 'claude_projeto', 'grok_raiz': 'grok_projeto',
              'grok_projeto': 'grok_sessao'}
    for pasta in fila:
        agente, nivel, anterior = vigias[pasta]
        try:
            info = os.stat(pasta)
            assinatura = (info.st_mtime_ns, info.st_ino)
        except OSError:
            vigias[pasta][2] = None
            continue
        if nivel == 'grok_sessao' and not 0 <= agora_s - info.st_mtime <= JANELA_STAT_S:
            del vigias[pasta]
            continue
        if not stat.S_ISDIR(info.st_mode) or not reconciliar and assinatura == anterior:
            continue
        # Registrar ANTES do scandir permite observar mudanças durante a listagem.
        vigias[pasta][2] = assinatura
        try:
            with os.scandir(pasta) as entradas:
                for entrada in entradas:
                    caminho = Path(entrada.path)
                    if entrada.is_dir(follow_symlinks=False) and nivel in filhos:
                        if filhos[nivel] == 'grok_sessao':
                            try:
                                idade = agora_s - entrada.stat(follow_symlinks=False).st_mtime
                            except OSError:
                                continue
                            if not 0 <= idade <= JANELA_STAT_S:
                                continue
                        if caminho not in vigias:
                            vigias[caminho] = [agente, filhos[nivel], ()]
                            fila.append(caminho)
                    elif entrada.is_file(follow_symlinks=False):
                        nome = entrada.name
                        aceito = (nome in ('chat_history.jsonl', 'events.jsonl') if agente == 'grok'
                                  else nome.endswith('.jsonl') or agente == 'codex' and nome.endswith('.jsonl.zst'))
                        if aceito:
                            try:
                                idade = agora_s - entrada.stat(follow_symlinks=False).st_mtime
                            except OSError:
                                continue
                            if 0 <= idade <= JANELA_STAT_S:
                                lista['caminhos'].add((agente, caminho))
        except OSError:
            if any(str(pasta) == raiz for _, raiz, _ in lista['raizes']):
                lista['falhas'].append(agente)


def _candidatos(raizes, agora_s, cache):
    """Reconcilia a cada 30 s; vigias e arquivos das últimas 24 h recebem stat a cada 2 s."""
    assinatura = tuple((a, str(r), p) for a, r, p in raizes)
    lista = cache.get('_descoberta')
    reconciliar = (lista is None or lista['raizes'] != assinatura
                   or not 0 <= agora_s - lista['em'] < INTERVALO_DESCOBERTA)
    if reconciliar:
        caminhos, falhas = set(), []
        for agente, raiz, padroes in raizes:
            try:
                for padrao in padroes:
                    for caminho in raiz.glob(padrao):
                        try:
                            info = caminho.stat()
                            recente = 0 <= agora_s - info.st_mtime <= JANELA_STAT_S
                            conhecido = cache.get(str(caminho))
                            pendente = conhecido and conhecido['chamadas']
                            if stat.S_ISREG(info.st_mode) and (recente or pendente):
                                caminhos.add((agente, caminho))
                        except OSError:
                            continue
            except OSError:
                falhas.append(agente)
        lista = {'raizes': assinatura, 'em': agora_s, 'caminhos': caminhos, 'falhas': falhas, 'vigias': {}}
        cache['_descoberta'] = lista
    _preparar_vigias(raizes, agora_s, lista['vigias'])
    _examinar_vigias(lista, reconciliar, agora_s)
    candidatos, mantidos = [], set()
    for agente, caminho in lista['caminhos']:
        try:
            info = caminho.stat()
            recente = 0 <= agora_s - info.st_mtime <= LIMITE_ATIVIDADE_S
            conhecido = cache.get(str(caminho))
            pendente = conhecido and conhecido['chamadas']
            if stat.S_ISREG(info.st_mode) and (0 <= agora_s - info.st_mtime <= JANELA_STAT_S or pendente):
                mantidos.add((agente, caminho))
            if stat.S_ISREG(info.st_mode) and (recente or pendente):
                candidatos.append((info.st_mtime, agente, caminho, info))
        except OSError:
            continue
    candidatos.sort(key=lambda c: (-c[0], str(c[2])))
    lista['caminhos'] = mantidos
    return candidatos, list(lista['falhas'])


def ler(ctx):
    """ctx opcional: claude_dir, sessoes_dir, grok_dir, hermes_db, comando_cache, agora.

    A janela de vida é atividade de arquivo/banco nos últimos dez minutos, não
    uma prova de processo vivo. Cursor-agent permanece sem leitura.
    """
    agora_s = agora(ctx)
    cache = ctx.get('comando_cache', _CACHE)
    raizes = (('claude', Path(ctx.get('claude_dir') or Path.home() / '.claude' / 'projects'), ('**/*.jsonl',)),
              ('codex', Path(ctx.get('sessoes_dir') or Path.home() / '.codex' / 'sessions'), ('**/*.jsonl', '**/*.jsonl.zst')),
              ('grok', Path(ctx.get('grok_dir') or Path.home() / '.grok') / 'sessions', ('*/*/chat_history.jsonl', '*/*/events.jsonl')))
    candidatos, falhas = _candidatos(raizes, agora_s, cache)
    # Histórico + eventos Grok contam como uma sessão; versões .jsonl/.zst também.
    grupos = {}
    for mt, agente, caminho, info in candidatos:
        chave = (agente, str(caminho.parent) if agente == 'grok' else str(caminho).removesuffix('.zst'))
        grupos.setdefault(chave, []).append((mt, caminho, info))
    itens, usados = [], set()
    for (agente, _), arquivos in grupos.items():
        estados = []
        for mt, caminho, info in arquivos:
            usados.add(str(caminho))
            try:
                estado = _ler_arquivo(caminho, agente, cache, info)
                if agente == 'grok':
                    estado['ident'] = caminho.parent.name
                item = _item(estado, agente, agora_s, mt)
                if item:
                    estados.append((mt, item, caminho.name == 'chat_history.jsonl'))
            except ERROS_LEITURA:
                cache.pop(str(caminho), None)
                falhas.append(agente)
        if estados:
            # O início mais novo vence; no empate, o histórico preserva argumentos.
            escolha = max(estados, key=lambda x: (x[1]['desde_epoch'], x[2]))
            itens.append((escolha[0], escolha[1]))
            if len(itens) >= MAX_SESSOES:
                break  # Arquivo sem chamada nunca consome uma das 30 vagas.
    for chave in list(cache):
        if not chave.startswith('_') and chave not in usados:
            del cache[chave]
    banco = Path(ctx.get('hermes_db') or Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'hermes' / 'state.db')
    try:
        itens.extend(_hermes(banco, agora_s, cache))
    except (OSError, sqlite3.Error, ValueError):
        falhas.append('hermes')
    itens.sort(key=lambda x: (-x[0], x[1]['agente'], x[1]['sessao']))
    secao = {'estado': 'ok', 'sessoes': [i for _, i in itens[:MAX_SESSOES]],
             'sem_leitura': ['cursor-agent'] + [a for a in AGENTES if a in falhas]}
    return {'comando': secao}
