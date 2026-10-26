"""Montagem pura das sessões vivas no contrato já consumido pela página."""
import ntpath
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
                         _pasta(v.get('pasta')) == _pasta(pasta)), None)
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
            if override.get(chave) is not None:
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
