"""Regras puras da sentinela; tempos em segundos epoch."""
import copy
import hashlib
import math
try:
    from .leitor import mascarar
except ImportError:
    from leitor import mascarar


def numero(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def avaliar(dados, estado_anterior, agora):
    estado = copy.deepcopy(estado_anterior or {})
    antigas = estado.get('chaves', {})
    novas, fechamentos, chaves = [], [], {}

    def regra(chave, condicao, texto, canais, duracao=0, vezes=1, uma_vez=False, observacao=True, notificar=True):
        anterior = antigas.get(chave, {})
        mesmo = condicao and anterior.get('condicao') and anterior.get('observacao') == observacao
        r = dict(anterior, condicao=bool(condicao), observacao=observacao,
                 desde=anterior.get('desde', agora) if mesmo else agora,
                 contagem=anterior.get('contagem', 0) + 1 if mesmo else int(bool(condicao)))
        r.setdefault('aberto', False)
        r.setdefault('ultimo_aviso', None)
        ativo = bool(condicao and r['contagem'] >= vezes and
                     (duracao == 0 or agora - r['desde'] > duracao))
        # Quedas curtas da condição também encerram um alerta já aberto.
        if not condicao or (anterior.get('aberto') and not mesmo):
            if anterior.get('aberto'):
                fechamentos.append({'chave': chave, 'tipo': 'resolvido', 'hora': agora,
                                    'texto': mascarar('Resolvido: ' + chave)[:200], 'canais': []})
            r.update(aberto=False, avisado_ciclo=False)
        ultimo = r.get('ultimo_aviso')
        pode = ultimo is None or agora - ultimo >= 1800
        if ativo and pode and notificar and not (uma_vez and r.get('avisado_ciclo')):
            novas.append({'chave': chave, 'tipo': 'lembrete' if r.get('aberto') else 'aberto', 'hora': agora,
                          'texto': mascarar(texto)[:240], 'canais': list(canais)})
            r.update(aberto=True, avisado_ciclo=True, ultimo_aviso=agora)
        r['ativo'] = ativo
        chaves[chave] = r

    for a in dados.get('agentes', []):
        nome = str(a.get('nome') or 'Agente')
        nome_curto = mascarar(nome)[:80]
        situacao = a.get('estado')
        regra('atencao:' + nome, situacao == 'esperando', nome_curto + ' precisa de você há mais de 1 min.',
              ('toast', 'webhook'), duracao=60)
        atividade = a.get('atividade')
        texto = mascarar(atividade)[:120] if isinstance(atividade, str) else ''
        assinatura = hashlib.sha256(str(atividade).encode('utf-8')).hexdigest()
        regra('travado:' + nome, situacao == 'trabalhando' and bool(texto),
              nome_curto + ': atividade sem mudança há 15 min. ' + texto, ('toast',),
              duracao=900, observacao=assinatura)
        regra('parado:' + nome, situacao == 'parado', nome_curto + ' parou.', ('toast', 'webhook'), uma_vez=True)
        cotas = a.get('cotas') or {}
        for janela, campo in (('5h', 'sessao_5h'), ('semanal', 'semanal')):
            chave = 'cota:%s:%s' % (nome, janela)
            pct = cotas.get(campo)
            anterior = antigas.get(chave, {})
            latched = anterior.get('condicao', False)
            condicao = (pct >= 85 or (latched and pct >= 70)) if numero(pct) and 0 <= pct <= 100 else latched
            regra(chave, condicao, '%s: cota %s usada em %s%%.' % (nome_curto, janela, pct),
                  ('webhook',), uma_vez=True, notificar=numero(pct) and 0 <= pct <= 100)
    for s in dados.get('servicos', []):
        nome = str(s.get('nome') or 'Serviço')
        regra('servico:' + nome, s.get('estado') == 'fora', mascarar(nome)[:80] + ' está fora.',
              ('toast', 'webhook'), vezes=2)
    for nome, fonte in dados.get('fontes', {}).items():
        regra('fonte:' + nome, fonte.get('ok') is False, 'Fonte ' + mascarar(nome)[:80] + ' sem leitura.',
              ('toast',), vezes=3)
    gerado = dados.get('gerado_em_epoch')
    regra('dados-velhos', numero(gerado) and agora - gerado > 60,
          'O painel não recebe dados novos há mais de 1 min.', ('toast',))
    for chave, anterior in antigas.items():
        if chave not in chaves:
            regra(chave, False, '', ())
    estado['chaves'] = chaves
    return novas, fechamentos, estado
