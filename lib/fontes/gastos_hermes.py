"""Contadores das sessões Hermes; leitura SQLite sem conteúdo das conversas.

Cotação US$->R$: a fonte publica só `usd_brl`; a conversão para R$ é do painel.
A rede é consultada no máximo a cada 6 h após sucesso e a cada 15 min após falha
(timeout de 3 s), para que uma API fora do ar não bloqueie cada passada de 60 s.
"""
from contextlib import closing
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from urllib.request import Request, urlopen

from . import agora, diretorio

COTACAO_URL = 'https://economia.awesomeapi.com.br/json/last/USD-BRL'
COTACAO_ARQUIVO = 'cotacao-usd-brl.json'
COTACAO_INTERVALO = 6 * 3600
COTACAO_REPETICAO = 15 * 60
COTACAO_VELHA = 48 * 3600

CAMPOS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
          'reasoning_tokens', 'message_count', 'cost_usd')
# Somente rótulos constantes saem da fonte; identificadores desconhecidos não vazam.
CANAIS = {'local': 'Terminal', 'cli': 'Terminal', 'subagent': 'Subagentes',
          'tool': 'Ferramentas', 'oneshot': 'One-shot', 'desktop': 'App desktop'}
# Identificadores internos só servem ao join; nunca são publicados.
CONSULTA = '''SELECT id, source, chat_type, billing_mode, started_at, model,
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    reasoning_tokens, message_count, COALESCE(actual_cost_usd, estimated_cost_usd, 0)
    FROM sessions WHERE started_at <= ?'''
MODELOS = '''SELECT u.session_id, u.model, u.billing_mode, u.task,
    SUM(u.input_tokens), SUM(u.output_tokens), SUM(u.cache_read_tokens),
    SUM(u.cache_write_tokens), SUM(u.reasoning_tokens)
    FROM session_model_usage u JOIN sessions s ON s.id = u.session_id
    WHERE s.started_at <= ? GROUP BY u.session_id, u.model, u.billing_mode, u.task'''
# Só estas três colunas de messages são lidas. Um único grupo antigo por sessão
# conserva o denominador vitalício sem trazer mensagens linha a linha ao Python.
MENSAGENS = '''SELECT session_id,
    CASE WHEN timestamp >= :dias THEN strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime') END AS dia,
    CASE WHEN timestamp >= :horas THEN CAST(timestamp / 3600 AS INTEGER) END AS hora,
    timestamp >= :recente AS recente, COUNT(timestamp)
    FROM messages WHERE role = 'assistant' AND timestamp <= :agora
    GROUP BY session_id, dia, hora, recente'''



def faixa(valor):
    """Taxa USD/BRL plausível (float finito entre 1 e 20) ou None."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, str):
        valor = valor.strip().replace(',', '.')
    try:
        taxa = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    return taxa if math.isfinite(taxa) and 1 <= taxa <= 20 else None


def buscar_cotacao(hora):
    if not COTACAO_URL.startswith('https://'):
        raise ValueError('cotação exige https')
    pedido = Request(COTACAO_URL, headers={'Accept': 'application/json'})
    with urlopen(pedido, timeout=3) as r:
        final = r.geturl()
        if isinstance(final, str) and not final.startswith('https://'):
            raise ValueError('redirecionamento fora de https')
        item = json.loads(r.read(65536))['USDBRL']
    taxa = faixa(item['bid'])
    if taxa is None:
        raise ValueError('cotação fora da faixa')
    try:
        data = float(item.get('timestamp'))
    except (TypeError, ValueError, OverflowError):
        data = 0
    if not math.isfinite(data) or data <= 0 or data > hora + 86400:
        data = hora
    return taxa, data


def epoch_valido(valor):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor) if math.isfinite(valor) and valor > 0 else None


def ler_cache_cotacao(caminho):
    """Sempre devolve dict com 'tentativa_ok' bool; valores inválidos são ignorados."""
    try:
        bruto = json.loads(Path(caminho).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        bruto = None
    if not isinstance(bruto, dict):
        bruto = {}
    cache = {}
    taxa = faixa(bruto.get('usd_brl'))
    data, buscado = epoch_valido(bruto.get('data_epoch')), epoch_valido(bruto.get('buscado_em'))
    if taxa is not None and data is not None and buscado is not None:
        cache.update(usd_brl=taxa, data_epoch=data, buscado_em=buscado)
    tentativa = epoch_valido(bruto.get('tentativa_em'))
    if tentativa is not None:
        cache['tentativa_em'] = tentativa
    cache['tentativa_ok'] = bruto.get('tentativa_ok') is True
    return cache


def gravar_cache_cotacao(caminho, cache):
    caminho = Path(caminho)
    tmp = caminho.with_name('%s.%d.tmp' % (caminho.name, os.getpid()))
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(cache, allow_nan=False), encoding='utf-8')
        os.replace(tmp, caminho)
    except (OSError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass


def cotacao(ctx, hora):
    manual = faixa(os.environ.get('PAINEL_USD_BRL'))
    if manual is not None:
        return {'usd_brl': manual, 'fonte': 'manual', 'data_epoch': hora, 'desatualizada': False}
    caminho = diretorio(ctx) / COTACAO_ARQUIVO
    cache = ler_cache_cotacao(caminho)
    tentativa = cache.get('tentativa_em')
    espera = COTACAO_INTERVALO if cache['tentativa_ok'] else COTACAO_REPETICAO
    if tentativa is None or not 0 <= hora - tentativa < espera:
        try:
            taxa, data = buscar_cotacao(hora)
            cache = {'usd_brl': taxa, 'data_epoch': data, 'buscado_em': hora,
                     'tentativa_em': hora, 'tentativa_ok': True}
        except Exception:
            cache = dict(cache, tentativa_em=hora, tentativa_ok=False)
        gravar_cache_cotacao(caminho, cache)
    if 'usd_brl' not in cache:
        return None
    return {'usd_brl': cache['usd_brl'], 'fonte': 'awesomeapi' if cache['tentativa_ok'] else 'cache',
            'data_epoch': cache['data_epoch'],
            'desatualizada': hora - cache['buscado_em'] > COTACAO_VELHA}


def canal(source, chat_type):
    if source == 'whatsapp':
        if chat_type == 'dm':
            return 'WhatsApp · conversa direta'
        if chat_type in ('group', 'channel', 'thread'):
            return 'WhatsApp · grupo'
        return 'WhatsApp'
    return CANAIS.get(source, 'Outra')


def modelo(nome):
    return nome if isinstance(nome, str) and re.fullmatch(r'[a-z0-9][a-z0-9.:_-]{0,63}', nome) else 'outro modelo'


def positivo(valor):
    return valor if isinstance(valor, (int, float)) and math.isfinite(valor) and valor > 0 else 0


def vazio():
    return dict(dict.fromkeys(CAMPOS, 0), inclui_assinatura=False)


def somar(destino, dado, fator=1):
    for campo in CAMPOS:
        destino[campo] += dado[campo] * fator
    destino['inclui_assinatura'] |= dado['inclui_assinatura']
    if dado.get('sub_rotulo'):
        destino['sub_rotulo'] = 'revisão em segundo plano'


def partes_sessao(linha, usos):
    """Tokens do ledger por modelo; custo vem exclusivamente de sessions."""
    valores = dict(zip(CAMPOS, map(positivo, linha[6:])))
    assinatura = linha[3] == 'subscription_included'
    if not usos:
        return [(modelo(linha[5]), dict(valores, inclui_assinatura=assinatura))]
    pesos = [sum(positivo(v) for v in uso[4:8]) for uso in usos]
    peso_total = sum(pesos)
    partes = []
    for uso, peso in zip(usos, pesos):
        # Sem tokens em todos os modelos, dividir igualmente mantém o custo.
        fracao = peso / peso_total if peso_total else 1 / len(usos)
        dado = vazio()
        dado.update(zip(CAMPOS[:5], map(positivo, uso[4:])))
        dado['cost_usd'] = valores['cost_usd'] * fracao
        dado['message_count'] = valores['message_count'] * fracao
        dado['inclui_assinatura'] = assinatura or uso[2] == 'subscription_included'
        if uso[3] == 'background_review':
            dado['sub_rotulo'] = 'revisão em segundo plano'
        partes.append((modelo(uso[1]), dado))
    return partes


def nova_janela(desde):
    return {'desde': desde, 'origens': {}, 'modelos': {}, 'total': vazio()}


def adicionar(janela, source, nome, dado, fator=1):
    origem = janela['origens'].setdefault(source, dict(vazio(), modelos={}))
    somar(origem, dado, fator)
    somar(origem['modelos'].setdefault(nome, vazio()), dado, fator)
    somar(janela['modelos'].setdefault(nome, vazio()), dado, fator)
    somar(janela['total'], dado, fator)


def finalizar(janela):
    def modelos_lista(mapa):
        return [dict(mapa[nome], model=nome) for nome in sorted(mapa)]
    for origem in janela['origens'].values():
        origem['modelos'] = modelos_lista(origem['modelos'])
    janela['origens'] = [dict(janela['origens'][s], source=s) for s in sorted(janela['origens'])]
    janela['modelos'] = modelos_lista(janela['modelos'])
    janela['total'] = {campo: janela['total'][campo] for campo in CAMPOS}


def arredondar_custos(janela):
    """Custo sai com 6 casas; tokens e timestamps ficam intactos."""
    def custo(dado):
        dado['cost_usd'] = round(dado['cost_usd'], 6)
    custo(janela['total'])
    for origem in janela['origens']:
        custo(origem)
        for dado in origem['modelos']:
            custo(dado)
    for dado in janela['modelos']:
        custo(dado)
    for b in janela['serie']:
        pares = [b['total']] + list(b['por_canal'].values()) + list(b['por_modelo'].values())
        for par in pares:
            par[0] = round(par[0], 6)


def balde(inicio, fim, diario):
    return {'inicio': inicio, 'fim': fim,
            'rotulo': datetime.fromtimestamp(inicio).strftime('%Y-%m-%d' if diario else '%d/%m %H:%M'),
            'total': [0, 0], 'por_canal': {}, 'por_modelo': {}}


def preparar_janelas(hora):
    hoje = datetime.fromtimestamp(hora).replace(hour=0, minute=0, second=0, microsecond=0)
    inicios = {'24h': hora - 86400, 'hoje': hoje.timestamp(),
               '7d': (hoje - timedelta(days=6)).timestamp(),
               '30d': (hoje - timedelta(days=29)).timestamp()}
    janelas, indices = {}, {}
    for chave, inicio in inicios.items():
        diario = chave in ('7d', '30d')
        janela = janelas[chave] = nova_janela(inicio)
        janela.update(ate=hora, granularidade='dia' if diario else 'hora', serie=[])
        indices[chave] = {}
        atual = inicio
        while atual <= hora:
            if diario:
                seguinte = (datetime.fromtimestamp(atual) + timedelta(days=1)).timestamp()
                indice = datetime.fromtimestamp(atual).strftime('%Y-%m-%d')
            else:
                seguinte = (math.floor(atual / 3600) + 1) * 3600
                indice = math.floor(atual / 3600)
            b = balde(atual, min(seguinte, hora), diario)
            janela['serie'].append(b)
            indices[chave][indice] = b
            atual = seguinte
    return janelas, indices


def somar_balde(b, source, nome, dado, fator):
    valores = (dado['cost_usd'] * fator, sum(dado[c] for c in CAMPOS[:4]) * fator)
    canal_total = b['por_canal'].setdefault(source, [0, 0])
    modelo_total = b['por_modelo'].setdefault(nome, [0, 0])
    for i, valor in enumerate(valores):
        b['total'][i] += valor
        canal_total[i] += valor
        modelo_total[i] += valor


def distribuir(janelas, indices, linha, partes, mensagens, hora):
    source = canal(linha[1], linha[2])
    if not mensagens:
        ts = linha[4]
        mensagens = [(datetime.fromtimestamp(ts).strftime('%Y-%m-%d'),
                      math.floor(ts / 3600), ts >= hora - 86400, 1)]
    denominador = sum(m[3] for m in mensagens)
    for dia, hora_balde, recente, quantidade in mensagens:
        fator = quantidade / denominador
        for chave, janela in janelas.items():
            if chave == '24h' and not recente:
                continue
            if chave == 'hoje' and dia != datetime.fromtimestamp(janela['desde']).strftime('%Y-%m-%d'):
                continue
            indice = dia if chave in ('7d', '30d') else hora_balde
            b = indices[chave].get(indice)
            if b is not None:
                for nome, dado in partes:
                    adicionar(janela, source, nome, dado, fator)
                    somar_balde(b, source, nome, dado, fator)


def ler(ctx):
    caminho = Path(ctx.get('gastos_path') or
                   Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'hermes' / 'state.db')
    hora = agora(ctx)
    janelas, indices = preparar_janelas(hora)
    resultado = {'custo_tipo': 'estimado pelo Hermes', 'custo_modelo_rateado': True,
                 'distribuicao_aproximada': True, 'serie_campos': ['cost_usd', 'tokens']}
    try:
        with closing(sqlite3.connect(caminho.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            # Modelos, sessões e mensagens no mesmo snapshot do banco vivo.
            db.execute('BEGIN')
            usos, mensagens = {}, {}
            for uso in db.execute(MODELOS, (hora,)):
                usos.setdefault(uso[0], []).append(uso)
            parametros = {'dias': janelas['30d']['desde'], 'agora': hora,
                          'horas': min(janelas['hoje']['desde'], hora - 86400), 'recente': hora - 86400}
            for sid, dia, h, recente, n in db.execute(MENSAGENS, parametros):
                mensagens.setdefault(sid, []).append((dia, h, recente, n))
            for linha in db.execute(CONSULTA, (hora,)):
                distribuir(janelas, indices, linha, partes_sessao(linha, usos.get(linha[0], [])),
                           mensagens.get(linha[0], []), hora)
            for janela in janelas.values():
                finalizar(janela)
                arredondar_custos(janela)
            resultado.update(janelas)
    except (sqlite3.Error, OSError, ValueError, OverflowError):
        # Não publicar caminho, SQL, conteúdo nem erro cru do banco.
        return {'erro': 'banco Hermes indisponível, travado ou incompatível'}
    try:
        resultado['cotacao'] = cotacao(ctx, hora)
    except Exception:
        resultado['cotacao'] = None
    saida = {'gastos_hermes': resultado}
    # Limite conservador também para serializadores com ensure_ascii=True.
    # Não truncar modelos nem publicar totais parciais: retenção é do coletor.
    if len(json.dumps(saida, allow_nan=False).encode('utf-8')) >= 64 * 1024:
        return {'erro': 'agregação de gastos excede o limite de 64 KB'}
    return saida
