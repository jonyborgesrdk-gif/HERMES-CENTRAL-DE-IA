"""Sentinela independente: avalia dados vivos, deduplica e encaminha alertas."""
import argparse
from datetime import datetime
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
from lib import canais as modulo_canais
from lib import regras
from lib.comum import gravar_atomico
from servidor import PREFIXO, Trava

INTERVALO_RESUMO = 600
SERVICOS_OPCIONAIS = frozenset({'Ollama', 'Hindsight'})


def _unicos(itens, padrao):
    if not isinstance(itens, list):
        return []
    vistos, saida = set(), []
    for item in itens:
        if isinstance(item, dict):
            nome = str(item.get('nome') or padrao)
            if nome in vistos:
                continue
            vistos.add(nome)
        saida.append(item)
    return saida


def carregar_estado(caminho, logger):
    try:
        r = json.loads(caminho.read_text(encoding='utf-8'))
        if isinstance(r, dict) and isinstance(r.get('chaves', {}), dict):
            return r
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    logger.warning('estado de alertas ilegível; iniciando vazio')
    return {}


def resumo_diario(dados, novos, estado, agora):
    hoje = datetime.fromtimestamp(agora).astimezone()
    data = hoje.date().isoformat()
    diario = estado.get('diario', {})
    if diario.get('data') != data:
        diario = {'data': data, 'alertas_por_tipo': {}, 'agentes_vistos': [], 'cotas_maximas': {}, 'enviado': False}
    for a in novos:
        tipo = a['chave'].split(':', 1)[0]
        diario['alertas_por_tipo'][tipo] = diario['alertas_por_tipo'].get(tipo, 0) + 1
    nomes = set(diario['agentes_vistos'])
    for a in dados.get('agentes', []):
        nome = str(a.get('nome') or 'Agente')
        nomes.add(nome)
        maximas = diario['cotas_maximas'].setdefault(nome, {})
        for janela, valor in (a.get('cotas') or {}).items():
            if janela in ('sessao_5h', 'semanal') and regras.numero(valor) and 0 <= valor <= 100:
                maximas[janela] = max(maximas.get(janela, 0), valor)
    diario['agentes_vistos'] = sorted(nomes)
    estado['diario'] = diario
    return diario, hoje.hour == 23 and hoje.minute >= 55 and not diario['enviado']


def construir_canais(pasta, logger, ensaio):
    resultado = {'log': modulo_canais.Log(logger, ensaio)}
    if ensaio:
        return resultado  # nem ler credenciais/configurações, nem garantir banco
    for nome, criar in (('toast', lambda: modulo_canais.Toast(logger=logger)),
                         ('webhook', lambda: modulo_canais.Webhook(pasta, logger=logger)),
                         ('hindsight', lambda: modulo_canais.Hindsight(pasta, logger=logger))):
        try:
            resultado[nome] = criar()
        except Exception:
            logger.warning('canal %s indisponível', nome)
    return resultado


def passada(dados, estado, agora, canais, logger, ensaio=False):
    dados = dict(dados, agentes=_unicos(dados.get('agentes'), 'Agente'),
                 servicos=_unicos(dados.get('servicos'), 'Serviço'))
    novos, fechados, estado = regras.avaliar(dados, estado, agora)
    ok = set(n for n in estado.get('servicos_ok', []) if isinstance(n, str))
    for s in dados['servicos']:
        if isinstance(s, dict) and s.get('estado') != 'fora':
            ok.add(str(s.get('nome') or 'Serviço'))
    estado['servicos_ok'] = sorted(ok)
    novos = [a for a in novos if not (
        a['tipo'] == 'lembrete' and a['chave'].startswith('servico:') and
        a['chave'][8:] in SERVICOS_OPCIONAIS and a['chave'][8:] not in ok)]
    for alerta in novos + fechados:
        nomes = ['log'] if ensaio else ['log', *alerta['canais']]
        if not ensaio and alerta['tipo'] in ('aberto', 'resolvido'):
            nomes.append('hindsight')
        for nome in nomes:
            canal = canais.get(nome)
            if canal is None:
                continue
            try:
                canal.enviar(alerta)
            except Exception:
                logger.warning('canal %s falhou', nome)
    ultimo = estado.get('resumo_log')
    if not isinstance(ultimo, (int, float)) or isinstance(ultimo, bool) or ultimo > agora:
        estado['resumo_log'] = agora
        # Instantâneo único na primeira avaliação (sem estado de resumo); depois só resumo periódico.
        for chave, item in sorted(estado['chaves'].items())[:20]:
            if item['condicao']:
                logger.info('%s contagem %s=%d aberto=%s', 'ENSAIO' if ensaio else 'avaliacao',
                            modulo_canais.mascarar(chave), item['contagem'], item['aberto'])
    elif agora - ultimo >= INTERVALO_RESUMO:
        ativas = sorted((c, i) for c, i in estado['chaves'].items() if i['condicao'])
        abertas = sum(1 for _, i in ativas if i['aberto'])
        lista = ', '.join('%s=%d' % (modulo_canais.mascarar(c), i['contagem']) for c, i in ativas[:20])
        logger.info('%s resumo periódico: %d condições ativas, %d abertas [%s]',
                    'ENSAIO' if ensaio else 'avaliacao', len(ativas), abertas, lista)
        estado['resumo_log'] = agora
    diario, enviar = resumo_diario(dados, novos, estado, agora)
    if enviar:
        alerta = {'chave': 'resumo-diario:' + diario['data'], 'tipo': 'resumo_diario',
                  'hora': agora, 'resumo': diario, 'texto': 'Resumo diário do painel', 'canais': []}
        try:
            if ensaio:
                canais['log'].enviar(alerta)
            elif 'hindsight' in canais:
                canais['hindsight'].enviar(alerta)
        except Exception:
            logger.warning('resumo Hindsight falhou')
        diario['enviado'] = True  # uma tentativa por dia, sem tempestade de retentativas
    if not ensaio and 'webhook' in canais:
        try:
            canais['webhook'].reenviar()
        except Exception:
            logger.warning('reenvio de webhook falhou')
    return estado


def main(argv=None, canais=None, agora=None, dormir=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dados', type=Path, default=Path(__file__).resolve().parent / 'publico' / 'dados.js')
    parser.add_argument('--ensaio', action='store_true')
    parser.add_argument('--uma-vez', action='store_true')
    args = parser.parse_args(argv)
    agora, dormir = agora or time.time, dormir or time.sleep
    pasta = Path(os.environ.get('PAINEL_IAS_DIR') or
                 Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'painel-ias')
    logger = logging.getLogger('painel.sentinela')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = trava = None
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(pasta / 'sentinela.log', when='midnight',
                                           backupCount=7, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        try:
            trava = Trava(pasta / 'sentinela.trava')
        except RuntimeError:
            logger.warning('outra sentinela já está em execução; saindo')
            return 0
        estado_path = pasta / ('alertas-ensaio-estado.json' if args.ensaio else 'alertas-estado.json')
        estado = carregar_estado(estado_path, logger)
        canais = construir_canais(pasta, logger, args.ensaio) if canais is None else canais
        logger.info('subida ensaio=%s uma_vez=%s', args.ensaio, args.uma_vez)
        while True:
            inicio = time.monotonic()
            try:
                # Extração estrita; --dados também aceita outro nome de arquivo.
                texto = args.dados.read_text(encoding='utf-8').strip()
                if not texto.startswith(PREFIXO) or not texto.endswith(');'):
                    raise ValueError('wrapper inválido')
                dados = json.loads(texto[len(PREFIXO):-2])
                if not isinstance(dados, dict) or dados.get('origem') != 'vivo':
                    raise ValueError('dados não vivos')
                estado = passada(dados, estado, agora(), canais, logger, args.ensaio)
                gravar_atomico(estado_path, json.dumps(estado, ensure_ascii=True) + '\n')
            except (OSError, ValueError, TypeError, KeyError):
                logger.warning('dados/estado sem leitura; nenhuma transição inferida')
                if args.uma_vez:
                    return 1
            if args.uma_vez:
                return 0
            dormir(max(0.1, 5 - (time.monotonic() - inicio)))
    except KeyboardInterrupt:
        return 0
    except OSError:
        return 1
    finally:
        if trava:
            trava.close()
        if handler:
            logger.removeHandler(handler)
            handler.close()


if __name__ == '__main__':
    sys.exit(main())
