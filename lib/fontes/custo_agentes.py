"""E25: custos locais opt-in; cache guarda só contadores e metadados necessários.

Codex: input inclui cache; entrada comum = input - cached - cache_write.
Raciocínio é subconjunto de output, nunca cobrado novamente. A escrita não teve
amostras positivas na medição local: sua inclusão no input é uma hipótese.
Hermes reutiliza SQL, abertura mode=ro e rateio por mensagens de gastos_hermes.
"""
from contextlib import closing
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3

try:
    from compression import zstd
except ImportError:  # Python anterior a 3.14 continua lendo JSONL comum.
    zstd = None

from . import agora, diretorio, epoch
from . import gastos_hermes as gastos

TOKENS = ('entrada', 'cache_leitura', 'cache_escrita_5m', 'cache_escrita_1h',
          'cache_escrita_30m', 'cache_escrita', 'saida', 'raciocinio', 'total')
SELOS = {'claude': 'assinatura (equivalente de API)',
         'codex': 'assinatura (equivalente de API)',
         'grok': 'informado pelo Grok', 'hermes': 'estimado', 'cursor-agent': 'sem leitura'}
AVISO_TARIFAS = 'arquivo de tarifas do usuário inválido; usando o padrão'
_CACHE = {}
_HERMES_CACHE = {}
ERROS_ARQUIVO = (OSError, ValueError, EOFError, ImportError, RecursionError) + (
    (zstd.ZstdError,) if zstd is not None else ())


def numero(v):
    return type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 10 ** 15


def contador(v):
    if not numero(v):
        raise ValueError('contador inválido')
    return v


def instante(v):
    if isinstance(v, bool):
        raise ValueError('horário inválido')
    ts = epoch(v)
    if not math.isfinite(ts) or not 0 < ts < 32503680000:
        raise ValueError('horário inválido')
    return ts


def tokens(**valores):
    r = dict.fromkeys(TOKENS, 0)
    r.update(valores)
    if 'total' not in valores:
        r['total'] = sum(r[k] for k in TOKENS if k not in ('total', 'raciocinio'))
    return r


def validar_tarifas(dado):
    modelos = dado['modelos']
    if not isinstance(modelos, dict):
        raise ValueError('tarifas inválidas')
    for nome, tarifa in modelos.items():
        if gastos.modelo(nome) == 'outro modelo' or not isinstance(tarifa, dict):
            raise ValueError('modelo inválido')
        campos = ('entrada', 'cache_leitura', 'saida') + (
            ('cache_escrita_5m', 'cache_escrita_1h') if nome.startswith('claude-')
            else ('cache_escrita_30m',))
        permitidos = set(campos) | {'fonte', 'conferido_em'}
        if nome == 'claude-haiku-5-5':
            permitidos.add('acima_100k')
        if set(tarifa) != permitidos:
            raise ValueError('campos de tarifa inválidos')
        if not all(numero(tarifa[k]) for k in campos):
            raise ValueError('tarifa inválida')
        date.fromisoformat(tarifa['conferido_em'])
        if not isinstance(tarifa['fonte'], str) or not tarifa['fonte'].startswith('https://'):
            raise ValueError('fonte inválida')
        if nome == 'claude-haiku-5-5':
            if (set(tarifa['acima_100k']) != set(campos)
                    or not all(numero(tarifa['acima_100k'][k]) for k in campos)):
                raise ValueError('faixa inválida')
    return modelos


def tarifas(ctx):
    padrao = validar_tarifas(json.loads((Path(__file__).parents[1] / 'tarifas_padrao.json').read_text(encoding='utf-8')))
    try:
        usuario = validar_tarifas(json.loads((diretorio(ctx) / 'tarifas.json').read_text(encoding='utf-8')))
    except FileNotFoundError:
        return padrao, []
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return padrao, [AVISO_TARIFAS]
    return dict(padrao, **usuario), []


def evento(ts, nome, uso, usd=None, flags=(), ident=None):
    return {'ts': ts, 'modelo': gastos.modelo(nome), 'tokens': uso,
            'usd': usd, 'flags': list(flags), 'ident': ident}


def claude(linhas):
    vistos, ignorados = {}, 0
    for d in linhas:
        try:
            m = d.get('message')
            if not isinstance(m, dict) or 'usage' not in m or m.get('model') == '<synthetic>':
                continue
            u, ident = m['usage'], m.get('id')
            if not isinstance(u, dict) or not isinstance(ident, str) or not ident:
                raise ValueError('uso inválido')
            campos = ('input_tokens', 'cache_creation_input_tokens',
                      'cache_read_input_tokens', 'output_tokens')
            iteracoes = u.get('iterations')
            if (all(u.get(k, 0) == 0 for k in campos)
                    and isinstance(iteracoes, list) and iteracoes
                    and all(isinstance(it, dict) for it in iteracoes)):
                # O cache aninhado do topo também é substituído, não somado outra vez.
                u = {k: sum(contador(it.get(k, 0)) for it in iteracoes) for k in campos}
                u['cache_creation'] = {
                    k: sum(contador((it.get('cache_creation') or {}).get(k, 0)) for it in iteracoes)
                    for k in ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens')}
            c = u.get('cache_creation') or {}
            a = contador(c.get('ephemeral_5m_input_tokens', 0))
            b = contador(c.get('ephemeral_1h_input_tokens', 0))
            escrita = contador(u.get('cache_creation_input_tokens', a + b))
            flags = []
            if escrita != a + b:
                flags.append('cache_sem_duracao')
            t = tokens(entrada=contador(u.get('input_tokens', 0)),
                       saida=contador(u.get('output_tokens', 0)),
                       cache_leitura=contador(u.get('cache_read_input_tokens', 0)),
                       cache_escrita_5m=a, cache_escrita_1h=b,
                       cache_escrita=max(0, escrita - a - b))
            vistos[ident] = evento(instante(d.get('timestamp')), m.get('model'), t, flags=flags, ident=ident)
        except (ValueError, TypeError, AttributeError, OverflowError):
            ignorados += 1
    return list(vistos.values()), ignorados


def codex(linhas):
    eventos, ignorados, nome = [], 0, None
    for d in linhas:
        try:
            p = d.get('payload')
            if not isinstance(p, dict):
                continue
            if d.get('type') == 'turn_context':
                nome = p.get('model')
            if d.get('type') != 'event_msg' or p.get('type') != 'token_count':
                continue
            info = p.get('info')
            if info is None:  # evento de cota sem contagem de tokens
                continue
            u = info.get('last_token_usage')
            if not isinstance(u, dict):
                raise ValueError('uso inválido')
            i, c, w, o, r = (contador(u.get(k, 0)) for k in (
                'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens'))
            total = contador(u.get('total_tokens', i + o))
            if i == c == w == o == 0:
                # Após compactação, total pode ser só o tamanho do novo contexto.
                total = r = 0
            flags = []
            if total != i + o or c + w > i:
                flags.append('tokens_inconsistentes')
            if i > 272000:
                flags.append('contexto_longo_nao_ajustado')
            t = tokens(entrada=max(0, i - c - w), cache_leitura=c,
                       cache_escrita_30m=w, saida=o, raciocinio=r, total=total)
            eventos.append(evento(instante(d.get('timestamp')), nome, t,
                                  usd=0 if i == c == w == o == 0 else None, flags=flags))
        except (ValueError, TypeError, AttributeError, OverflowError):
            ignorados += 1
    return eventos, ignorados


def grok(d):
    eventos, ignorados = [], 0
    if not isinstance(d, dict) or not isinstance(d.get('turns'), list):
        return [], 1
    for u in d['turns']:
        try:
            t = tokens(**{dest: contador(u.get(origem, 0)) for dest, origem in (
                ('entrada', 'inputTokens'), ('saida', 'outputTokens'),
                ('cache_leitura', 'cachedReadTokens'), ('cache_escrita', 'cacheCreationTokens'),
                ('raciocinio', 'reasoningTokens'), ('total', 'totalTokens'))})
            eventos.append(evento(instante(u.get('endedAt')), u.get('primaryModelId'),
                                  t, contador(u['costUsdTicks']) / 1e10))
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            ignorados += 1
    return eventos, ignorados


def ler_arquivo(caminho, agente):
    ignorados, linhas = 0, []
    try:
        if agente == 'grok':
            return grok(json.loads(caminho.read_text(encoding='utf-8')))
        if caminho.name.endswith('.jsonl.zst'):
            if zstd is None:
                raise ImportError('zstd indisponível')
            stream = zstd.open(caminho, 'rt', encoding='utf-8', errors='replace')
        else:
            stream = caminho.open(encoding='utf-8', errors='replace')
        with stream:
            # O parser recebe um gerador: prompts e mensagens não ficam no cache.
            def registros():
                nonlocal ignorados
                for linha in stream:
                    try:
                        d = json.loads(linha)
                        if not isinstance(d, dict):
                            raise ValueError('registro inválido')
                        yield d
                    except (ValueError, RecursionError):
                        ignorados += 1
            linhas, falhas = (claude if agente == 'claude' else codex)(registros())
            ignorados += falhas
    except ERROS_ARQUIVO:
        # Nunca expor caminho, conteúdo nem mensagem da exceção.
        ignorados += 1
    return linhas, ignorados


def arquivos(ctx, hora):
    grupos, ignorados, ativos = [], 0, set()
    for agente, raiz, padroes in (
        ('claude', Path(ctx.get('claude_dir') or Path.home() / '.claude/projects'), ('*.jsonl',)),
        ('codex', Path(ctx.get('codex_dir') or Path.home() / '.codex/sessions'), ('*.jsonl', '*.jsonl.zst')),
        ('grok', Path(ctx.get('grok_dir') or Path.home() / '.grok') / 'sessions', ('usage.json',))):
        for padrao in padroes:
            for caminho in sorted(raiz.rglob(padrao)):
                try:
                    st = caminho.stat()
                    if st.st_mtime < hora - 8 * 86400:
                        continue
                    chave = (caminho, st.st_size, st.st_mtime_ns)
                    ativos.add(chave)
                    if chave not in _CACHE:
                        _CACHE[chave] = ler_arquivo(caminho, agente)
                    eventos, falhas = _CACHE[chave]
                    ignorados += falhas
                    grupos.append((agente, caminho, eventos))
                except OSError:
                    ignorados += 1
    for chave in list(_CACHE):
        if chave not in ativos:
            del _CACHE[chave]
    # Um mesmo message.id pode reaparecer em mais de um arquivo.
    ultimos = {}
    for agente, caminho, eventos in grupos:
        if agente == 'claude':
            for e in eventos:
                anterior = ultimos.get(e['ident'])
                if anterior is None or e['ts'] >= anterior[1]['ts']:
                    ultimos[e['ident']] = (caminho, e)
    return [(a, p, [e for e in es if a != 'claude' or ultimos[e['ident']] == (p, e)])
            for a, p, es in grupos], ignorados


def hermes(ctx, hora, inicio):
    """Mesmo snapshot, SQL e rateio da fonte existente; só contadores do banco."""
    caminho = Path(ctx.get('gastos_path') or
                   Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'hermes/state.db')
    grupos = []
    try:
        # WAL também participa da assinatura: SQLite vivo pode mudar sem tocar o db.
        assinatura = []
        for p in (caminho, Path(str(caminho) + '-wal')):
            try:
                st = p.stat()
                assinatura.append((p, st.st_size, st.st_mtime_ns))
            except FileNotFoundError:
                assinatura.append((p, None, None))
        chave = (tuple(assinatura), inicio)
        if chave in _HERMES_CACHE:
            return _HERMES_CACHE[chave], 'ok'
        with closing(sqlite3.connect(caminho.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.execute('BEGIN')
            usos, mensagens = {}, {}
            for u in db.execute(gastos.MODELOS, (hora,)):
                usos.setdefault(u[0], []).append(u)
            params = {'dias': inicio, 'agora': hora, 'horas': inicio, 'recente': inicio}
            for sid, dia, h, recente, n in db.execute(gastos.MENSAGENS, params):
                mensagens.setdefault(sid, []).append((dia, h, recente, n))
            for linha in db.execute(gastos.CONSULTA, (hora,)):
                ts = instante(linha[4])
                ms = mensagens.get(linha[0]) or [(datetime.fromtimestamp(ts).date().isoformat(),
                                                math.floor(ts / 3600), True, 1)]
                denominador = sum(m[3] for m in ms)
                eventos = []
                for dia, h, _, n in ms:
                    if dia is None:
                        continue
                    for nome, d in gastos.partes_sessao(linha, usos.get(linha[0], [])):
                        fator = n / denominador
                        t = tokens(**{dest: gastos.positivo(d[origem]) * fator for dest, origem in (
                            ('entrada', 'input_tokens'), ('saida', 'output_tokens'),
                            ('cache_leitura', 'cache_read_tokens'), ('cache_escrita', 'cache_write_tokens'),
                            ('raciocinio', 'reasoning_tokens'))})
                        e = evento(max(ts, h * 3600, datetime.fromisoformat(dia).timestamp()), nome, t, d['cost_usd'] * fator,
                                   ('distribuicao_aproximada',))
                        e['inicio'] = ts
                        eventos.append(e)
                grupos.append(('hermes', linha[0], eventos))
        _HERMES_CACHE.clear()
        _HERMES_CACHE[chave] = grupos
    except (sqlite3.Error, OSError, ValueError, OverflowError, TypeError):
        return [], 'sem leitura'
    return grupos, 'ok'


def precificar(e, tabela, hoje):
    e = dict(e, flags=list(e['flags']))
    if e['usd'] == 0:
        return e
    tarifa = tabela.get(e['modelo'])
    if tarifa is None:
        e['flags'].append('sem_tarifa')
        return e
    if (hoje - date.fromisoformat(tarifa['conferido_em'])).days > 30:
        e['flags'].append('tarifa_velha')
    t = e['tokens']
    if e['modelo'] == 'claude-haiku-5-5' and t['total'] - t['saida'] > 100000:
        tarifa = tarifa['acima_100k']
    if any(f in e['flags'] for f in ('tokens_inconsistentes', 'cache_sem_duracao')):
        return e
    e['usd'] = sum(t[k] * tarifa[k] for k in tarifa if k in TOKENS) / 1e6
    return e


def agregado():
    return {'tokens': tokens(), 'usd': 0, 'brl': None, 'flags': [],
            'parcial': False, 'eventos_sem_custo': 0, '_eventos_com_custo': 0}


def somar(dest, e):
    for k in TOKENS:
        dest['tokens'][k] += e['tokens'][k]
    if e['usd'] is not None:
        dest['usd'] += e['usd']
    dest['eventos_sem_custo'] += e.get('eventos_sem_custo', int(e['usd'] is None))
    dest['_eventos_com_custo'] += e.get('_eventos_com_custo', int(e['usd'] is not None))
    dest['parcial'] = dest['eventos_sem_custo'] > 0
    dest['flags'] = sorted(set(dest['flags'] + e['flags']))


def converter(d, cambio):
    conhecidos = d.pop('_eventos_com_custo')
    if d['eventos_sem_custo'] and not conhecidos:
        d['usd'] = None
    # Rateio Hermes mantém precisão internamente; só a saída vira inteira.
    d['tokens'] = {k: round(v) for k, v in d['tokens'].items()}
    if d['usd'] is not None:
        d['usd'] = round(d['usd'], 8)
        if cambio is not None:
            d['brl'] = round(d['usd'] * cambio['usd_brl'], 8)


def ler(ctx):
    if os.environ.get('PAINEL_CUSTO') != '1':
        return {}
    hora = agora(ctx)
    hoje = datetime.fromtimestamp(hora).date()
    inicio = datetime.combine(hoje - timedelta(days=6), datetime.min.time()).timestamp()
    tabela, avisos = tarifas(ctx)
    grupos, ignorados = arquivos(ctx, hora)
    hs, estado_hermes = hermes(ctx, hora, inicio)
    grupos += hs
    try:
        cambio = gastos.cotacao(ctx, hora)
    except Exception:
        cambio = None
    dias = { (hoje - timedelta(days=6-i)).isoformat():
             {a: agregado() for a in SELOS if a != 'cursor-agent'} for i in range(7) }
    sessoes, modelos = [], {}
    for agente, _, eventos in grupos:
        sessao = agregado()
        usados, inicio_s, fim_s = [], None, None
        for e in eventos:
            ts = e['ts']
            if ts <= hora:
                inicio_s = min(inicio_s if inicio_s is not None else ts, e.get('inicio', ts))
                fim_s = max(fim_s or ts, ts)
            if not inicio <= ts <= hora:
                continue
            if agente in ('claude', 'codex'):
                e = precificar(e, tabela, hoje)
                modelos[e['modelo']] = {'modelo': e['modelo'], 'tarifa_velha': 'tarifa_velha' in e['flags'],
                                       'sem_tarifa': 'sem_tarifa' in e['flags']}
            dia = datetime.fromtimestamp(ts).date().isoformat()
            somar(dias[dia][agente], e)
            if dia == hoje.isoformat():
                somar(sessao, e)
                usados.append(e['modelo'])
        if usados:
            converter(sessao, cambio)
            sessoes.append(dict(sessao, agente=agente, modelo=usados[-1] if len(set(usados)) == 1 else 'outro modelo',
                                inicio_epoch=inicio_s, fim_epoch=fim_s))
    agentes = []
    total_hoje, total_7d = agregado(), agregado()
    for a, selo in SELOS.items():
        h, semana = agregado(), agregado()
        estado = ('sem leitura' if a == 'cursor-agent' else estado_hermes if a == 'hermes' else 'ok')
        if estado == 'sem leitura':
            h['usd'] = semana['usd'] = None
        else:
            for dia, itens in dias.items():
                somar(semana, itens[a])
                somar(total_7d, itens[a])
                if dia == hoje.isoformat():
                    somar(h, itens[a])
                    somar(total_hoje, itens[a])
        for d in (h, semana):
            converter(d, cambio)
        agentes.append({'agente': a, 'selo': selo, 'estado': estado, 'hoje': h, 'sete_dias': semana})
    serie = []
    for dia, itens in dias.items():
        total = agregado()
        for a, d in itens.items():
            if a != 'hermes' or estado_hermes == 'ok':
                somar(total, d)
            else:
                d['usd'] = None
            converter(d, cambio)
        converter(total, cambio)
        serie.append({'dia': dia, 'total': total,
                      'agentes': [dict(d, agente=a) for a, d in itens.items()]})
    for d in (total_hoje, total_7d):
        converter(d, cambio)
    if any(m['tarifa_velha'] for m in modelos.values()):
        avisos.append('tarifa conferida há mais de 30 dias')
    if any(m['sem_tarifa'] for m in modelos.values()):
        avisos.append('modelo sem tarifa; custo sem leitura')
    if estado_hermes != 'ok':
        avisos.append('Hermes sem leitura; totais não incluem Hermes')
    if any('tokens_inconsistentes' in d['flags'] for d in (total_hoje, total_7d)):
        avisos.append('contadores de tokens inconsistentes; custo sem leitura')
    if any('cache_sem_duracao' in d['flags'] for d in (total_hoje, total_7d)):
        avisos.append('cache Claude sem duração; custo sem leitura')
    sessoes.sort(key=lambda s: (s['usd'] is None, -(s['usd'] or 0), -s['fim_epoch']))
    return {'custo_agentes': {'estado': 'ok', 'hoje': total_hoje, 'sete_dias': total_7d,
            'agentes': agentes, 'dias': serie, 'sessoes': sessoes[:20],
            'sessoes_hoje': len(sessoes), 'cotacao': cambio, 'tarifas': list(modelos.values()),
            'avisos': avisos, 'registros_ignorados': ignorados}}
