"""Montagem pura das sessões vivas no contrato já consumido pela página."""
import ntpath
import hashlib
import math
from datetime import datetime
try:
    from . import leitor
except ImportError:
    import leitor


def _pasta(valor):
    return ntpath.normcase(ntpath.normpath(valor)) if valor else None


def montar_agentes(leituras_das_fontes, agora, overrides):
    """overrides é a lista opcional agentes do mapa; leituras são snapshots."""
    leituras = leituras_das_fontes
    resultado = []
    nomes = set()

    def adicionar(ferramenta, nome, pasta, session_id, fonte, **campos):
        override = next((v for v in (overrides or []) if pasta and
                         _pasta(v.get('pasta')) == _pasta(pasta) and
                         (not v.get('ferramenta') or v['ferramenta'] == ferramenta)), None)
        if override is None:
            override = next((v for v in (overrides or []) if v.get('nome') == nome), {})
        nome = override.get('nome') or nome
        base, numero = nome, 2
        while nome in nomes:
            nome = '%s (%d)' % (base, numero)
            numero += 1
        nomes.add(nome)
        r = leitor.sem_leitura(ferramenta, None)
        r.update(leitura='ok', estado='ocioso', nome=nome, pasta=pasta,
                 session_id=session_id, fonte=fonte,
                 ferramenta_nome={'claude': 'Claude Code', 'codex': 'Codex'}.get(ferramenta),
                 conta=None, grupo_cota=None, grupo_semanal=None, rotulo_semanal=None,
                 nota_cota=None, missao=None, prs=[], modelo_lido_da_tela=False,
                 lido_em_epoch=agora)
        r.update(campos)
        for chave in ('modelo', 'esforco', 'conta', 'grupo_cota', 'semanal_do_modelo', 'nota_cota'):
            if override.get(chave) is not None and not (chave == 'modelo' and r.get('modelo')):
                r[chave] = override[chave]
        # Nome, modelo e demais textos podem vir de arquivos externos ao painel.
        for chave in ('nome', 'pasta', 'modelo', 'conta', 'atividade', 'ultima_fala', 'ferramenta_nome'):
            if isinstance(r.get(chave), str):
                r[chave] = leitor.mascarar(r[chave])
        resultado.append(r)

    cota = leituras.get('cota_claude', {})
    for sessao in leituras.get('eventos_hooks', {}).get('sessoes', []):
        transcript = leituras.get('transcripts_claude', {}).get(sessao.get('session_id'), {})
        pasta = sessao.get('cwd') or transcript.get('pasta')
        estado = sessao.get('estado', 'ocioso')
        permissao = estado == 'esperando permissão'
        atividade = ('pede permissão: ' + str(sessao.get('tool_name') or 'ferramenta')) if permissao else transcript.get('fala')
        adicionar('claude', 'Claude · ' + (ntpath.basename((pasta or '').rstrip('/\\')) or 'sem pasta'),
                  pasta, sessao.get('session_id'), 'eventos_hooks',
                  estado='esperando' if permissao else estado,
                  modelo=transcript.get('modelo'), atividade=atividade,
                  ultima_fala=transcript.get('fala'), cotas=dict(cota),
                  sessao_5h_pct=cota.get('sessao_5h'), semanal_pct=cota.get('semanal'))

    for sessao in leituras.get('sessoes_codex', {}).get('sessoes', []):
        pasta = sessao.get('pasta')
        adicionar('codex', 'Codex · ' + (ntpath.basename((pasta or '').rstrip('/\\')) or 'sem pasta'),
                  pasta, sessao.get('session_id'), 'sessoes_codex',
                  modelo=sessao.get('modelo'), estado=sessao.get('estado', 'ocioso'),
                  cotas={k: sessao.get(k) for k in ('sessao_5h', 'semanal', 'reset_5h', 'reset_semanal', 'hora')},
                  sessao_5h_pct=sessao.get('sessao_5h'), semanal_pct=sessao.get('semanal'))

    pastas = {(r['ferramenta'], _pasta(r['pasta'])) for r in resultado if r['pasta']}
    for terminal in leituras.get('maestri_wire', {}).get('terminais', []):
        tipo = terminal.get('tipo')
        pasta = terminal.get('workingDirectory') or terminal.get('pasta')
        ferramenta = {'claude_code': 'claude', 'codex': 'codex'}.get(tipo)
        if ferramenta and pasta and (ferramenta, _pasta(pasta)) in pastas:
            continue
        if terminal.get('descarregado') or terminal.get('rodando') is False:
            estado = 'parado'
        elif terminal.get('atencao'):
            estado = 'esperando'
        elif terminal.get('status') == 'running':
            estado = 'trabalhando'
        elif terminal.get('status') == 'idle':
            estado = 'ocioso'
        else:
            estado = 'indeterminado'
        preview = terminal.get('preview') or []
        ultima = next((linha.strip() for linha in reversed(preview) if linha.strip()), 'sem tela')
        adicionar('maestri', '%s/%s' % (terminal.get('workspace') or 'Workspace', terminal.get('nome') or 'Terminal'),
                  pasta, terminal.get('id'), 'maestri_wire',
                  ferramenta_nome='Maestri · ' + str(tipo or 'desconhecido'),
                  estado=estado, atividade=ultima, ultima_fala=ultima, cotas={})
    return resultado


def _numero(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def _epoch(valor):
    if _numero(valor):
        return valor
    if isinstance(valor, str):
        try:
            return datetime.fromisoformat(valor.replace('Z', '+00:00')).timestamp()
        except (ValueError, OSError):
            pass
    return None


def montar_contas(leituras, agora, agentes=(), disponiveis=None, saude=None):
    """Contrato dos cards antigos, sem I/O nem identidade presumida para Codex."""
    disponiveis = set(leituras) if disponiveis is None else set(disponiveis)
    saude = saude or {}
    lista = []

    def card(ferramenta, chave, titulo, cota, membros, origem):
        lido = _epoch(cota.get('hora'))
        erro = saude.get('cota_claude' if ferramenta == 'claude' else 'sessoes_codex', {}).get('erro')
        erro = erro or cota.get('erro') or cota.get('erro_auth')
        barras = []
        for tipo, campo, reset, minutos, rotulo in (
                ('5h', 'sessao_5h', 'reset_5h', 300, '5h'),
                ('semana', 'semanal', 'reset_semanal', 10080, 'Semana')):
            pct = cota.get(campo)
            pct = pct if _numero(pct) and 0 <= pct <= 100 else None
            r = cota.get(reset)
            barras.append({'grupo': chave, 'tipo': tipo, 'rotulo': rotulo, 'usado_pct': pct,
                'renova_epoch': _epoch(r), 'renova_texto': leitor.mascarar(r) if isinstance(r, str) else None,
                'janela_min': minutos, 'origem': origem if pct is not None else None,
                'evidencia': origem if pct is not None else None,
                'lido_em_epoch': lido, 'fonte': 'Claude Code' if ferramenta == 'claude' else 'Codex',
                'membros': list(membros)})
        lista.append({'id': ferramenta + ':' + hashlib.sha256(chave.encode()).hexdigest()[:16],
            'ferramenta': ferramenta, 'ferramenta_nome': 'Claude Code' if ferramenta == 'claude' else 'Codex',
            'titulo': leitor.mascarar(titulo), 'email': cota.get('email'), 'plano': cota.get('plano'),
            'agentes': list(membros), 'terminais': [], 'modelo': cota.get('modelo'), 'esforco': None,
            'consultando': False, 'erro': bool(erro), 'erro_detalhe': leitor.mascarar(str(erro)) if erro else None,
            'situacao': 'ok' if any(b['usado_pct'] is not None for b in barras) else 'sem dado',
            'lido_em_epoch': lido, 'proxima_consulta_epoch': None, 'keychain_limitado': False,
            'intervalo_s': 300, 'barras': barras})

    if 'cota_claude' in disponiveis:
        cota = leituras.get('cota_claude', {})
        plano = cota.get('plano')
        card('claude', 'claude', 'Claude' + (' · ' + plano if isinstance(plano, str) and plano else ''),
             cota, [a['nome'] for a in agentes if a['ferramenta'] == 'claude'], 'cli')
    if 'sessoes_codex' in disponiveis:
        grupos = {}
        for sessao in leituras.get('sessoes_codex', {}).get('sessoes', []):
            conta = (sessao.get('account_id') or sessao.get('email') or sessao.get('conta') or
                     sessao.get('limit_id') or (sessao.get('rate_limits') or {}).get('limit_id'))
            chave = str(conta) if conta else 'desconhecida'
            grupos.setdefault(chave, []).append(sessao)
        if not grupos:
            grupos['desconhecida'] = []
        for chave, sessoes in grupos.items():
            com_cota = [s for s in sessoes if any(_numero(s.get(k)) and 0 <= s[k] <= 100
                                                for k in ('sessao_5h', 'semanal'))]
            ultima = max(com_cota or sessoes, key=lambda s: _epoch(s.get('hora')) or 0, default={})
            ids = {s.get('session_id') for s in sessoes}
            membros = [a['nome'] for a in agentes if a['ferramenta'] == 'codex' and a.get('session_id') in ids]
            card('codex', 'codex:' + chave, 'Codex' if chave == 'desconhecida' else 'Codex · ' + chave,
                 ultima, membros, 'sessao')
    erros = [saude.get(n, {}).get('erro') for n in ('cota_claude', 'sessoes_codex') if n in disponiveis]
    return {'consultadas': bool(disponiveis & {'cota_claude', 'sessoes_codex'}),
            'erro': '; '.join(leitor.mascarar(str(e)) for e in erros if e) or None,
            'intervalos_s': {'claude': 300, 'codex': 300}, 'lista': lista}
