"""Montagem pura das sessões vivas no contrato já consumido pela página."""
import ntpath
import hashlib
import math
import re
import time
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:     # Python sem zoneinfo
    ZoneInfo = None
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

    def adicionar(ferramenta, nome, pasta, session_id, fonte, publicar_pasta=True, **campos):
        override = next((v for v in (overrides or []) if pasta and
                         _pasta(v.get('pasta')) == _pasta(pasta) and
                         (not v.get('ferramenta') or v['ferramenta'] == ferramenta)), None)
        if override is None:
            override = next((v for v in (overrides or []) if v.get('nome') == nome and
                             (not v.get('ferramenta') or v['ferramenta'] == ferramenta)), {})
        nome = override.get('nome') or nome
        base, numero = nome, 2
        while nome in nomes:
            nome = '%s (%d)' % (base, numero)
            numero += 1
        nomes.add(nome)
        r = leitor.sem_leitura(ferramenta, None)
        r.update(leitura='ok', estado='ocioso', nome=nome, pasta=pasta if publicar_pasta else None,
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
        transcript = leituras.get('transcripts_claude', {}).get(sessao.get('session_id')) or {}
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
        adicionar('maestri', '%s/%s' % (terminal.get('workspace') or 'Workspace', terminal.get('nome') or 'Terminal'),
                  pasta, terminal.get('id'), 'maestri_wire', publicar_pasta=False,
                  ferramenta_nome='Maestri · ' + str(tipo or 'desconhecido'),
                  estado=estado, atividade=None, ultima_fala=None, cotas={})
    return resultado


def _numero(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


# Fusos conhecidos sem depender da base IANA (no Windows o zoneinfo pode vir sem tzdata).
# O Brasil não tem horário de verão desde 2019, então o deslocamento fixo é exato daí em diante.
_FUSOS_FIXOS = {'America/Sao_Paulo': timezone(timedelta(hours=-3), 'America/Sao_Paulo'),
                'UTC': timezone.utc}
_MESES = {m: i for i, m in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
_RESET = re.compile(
    r'^\s*(?:(?P<mes>[A-Za-z]{3,9})\.?\s+(?P<dia>\d{1,2})(?:\s*,|\s+at)?\s+)?'
    r'(?P<hora>\d{1,2})(?::(?P<min>\d{2}))?\s*(?P<ampm>[ap]m)'
    r'(?:\s*\((?P<fuso>[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+){0,2})\))?\s*$', re.I)


_MARGEM_RESET_S = 3600      # tolerância sobre a janela da cota ao validar o reset lido


def _fuso(nome):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(nome)
        except Exception:       # fuso desconhecido ou base IANA ausente: tenta a tabela fixa
            pass
    return _FUSOS_FIXOS.get(nome)


def epoch_do_reset(texto, agora=None, lido=None, janela_min=None):
    """'Oct 31, 2:10am (America/Sao_Paulo)' -> epoch; None se o texto ou o fuso não forem confiáveis.

    Aceita com/sem minutos, am/pm e, sem data, a próxima ocorrência da hora. O ano é inferido a partir da
    hora da LEITURA (`lido`; sem ela, `agora`): primeira ocorrência >= leitura - 1 dia. Com `janela_min`, um
    reset além de leitura + janela (+ margem) é incoerente (ex.: leitura velha de 'Oct 27' lida meses depois)
    e devolve None em vez de inventar. Reset já passado em relação a `agora` é mantido (a página trata 'vencida').
    Sem fuso no texto ou com fuso desconhecido não se chuta: None.
    """
    m = _RESET.match(texto) if isinstance(texto, str) else None
    if not m or not m['fuso']:
        return None
    fuso = _fuso(m['fuso'])
    hora, minuto = int(m['hora']), int(m['min'] or 0)
    if fuso is None or not 1 <= hora <= 12 or minuto > 59:
        return None
    hora = hora % 12 + (12 if m['ampm'].lower() == 'pm' else 0)
    agora = time.time() if not _numero(agora) else agora
    ref = lido if _numero(lido) else agora
    try:
        base = datetime.fromtimestamp(ref - 86400, fuso)
        if m['mes']:
            mes = _MESES.get(m['mes'][:3].lower())
            if mes is None:
                return None
            dia, candidatos = int(m['dia']), []
            for ano in (base.year, base.year + 1):
                try:
                    candidatos.append(datetime(ano, mes, dia, hora, minuto, tzinfo=fuso))
                except ValueError:      # 29/02 em ano comum
                    pass
            alvo = next((c for c in candidatos if c.timestamp() >= ref - 86400), None)
        else:
            alvo = datetime(base.year, base.month, base.day, hora, minuto, tzinfo=fuso) + timedelta(days=1)
            while alvo.timestamp() < ref:
                alvo += timedelta(days=1)
        if alvo is None:
            return None
        epoch = alvo.timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    # Normalização do truncamento de segundos observado no texto da CLI: 02:09:59 aparece como '2:09am' e
    # logo depois como '2:10am' (19:59:59 -> '7:59pm'/'8pm'). Só os minutos 09 e 59 sobem um minuto, para o
    # epoch não oscilar; 19, 29, 39 e 49 não têm esse comportamento e ficam como lidos.
    if minuto in (9, 59):
        epoch += 60
    if _numero(janela_min) and epoch > ref + janela_min * 60 + _MARGEM_RESET_S:
        return None
    return epoch


def _epoch(valor, agora=None, lido=None, janela_min=None):
    if _numero(valor):
        return valor
    if isinstance(valor, str):
        try:
            return datetime.fromisoformat(valor.replace('Z', '+00:00')).timestamp()
        except (ValueError, OSError):
            return epoch_do_reset(valor, agora, lido, janela_min)
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
                'renova_epoch': _epoch(r, agora, lido, minutos), 'renova_texto': leitor.mascarar(r) if isinstance(r, str) else None,
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
