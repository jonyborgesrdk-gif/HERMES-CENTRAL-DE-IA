"""Leitura local do Wire; configuração ausente desliga a fonte."""
from urllib.parse import quote
from . import agora, diretorio, http_get

_cache_settings = {}


def _pasta(ctx, terminal_id, headers):
    if not terminal_id:
        return None, None
    chave = (str(diretorio(ctx)), str(terminal_id))
    hora = agora(ctx)
    anterior = _cache_settings.get(chave)
    if anterior and 0 <= hora - anterior[0] < 60:
        return anterior[1:]
    try:
        settings = http_get(ctx, 'https://localhost:7434/api/terminals/' +
                            quote(str(terminal_id), safe='') + '/settings', 3, headers)
        pasta = settings.get('workingDirectory')
        pasta = pasta if isinstance(pasta, str) else None
        erro = None
    except Exception:
        pasta, erro = None, 'settings do terminal indisponível'
    _cache_settings[chave] = (hora, pasta, erro)
    return pasta, erro


def interpretar(item, workspace):
    t = item.get('terminal', item)
    return {'id': t.get('id'), 'workspace': workspace, 'nome': t.get('name'),
            'tipo': t.get('agentType'), 'status': t.get('status'),
            'atencao': bool(t.get('needsAttention')), 'rodando': bool(t.get('isRunning')),
            'descarregado': bool(t.get('isUnloaded')),
            'tela': 'ok' if t.get('preview') else 'sem tela'}


def ler(ctx):
    arquivo = diretorio(ctx) / 'wire.conf'
    try:
        texto = arquivo.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return {'ligado': False}
    except (OSError, ValueError):
        return {'ligado': True, 'erro': 'configuração sem leitura'}
    token = next((linha.split('=', 1)[1].strip() for linha in texto.splitlines()
                  if linha.partition('=')[0].strip() == 'WIRE_TOKEN'), '')
    if not token:
        return {'ligado': False}
    try:
        headers = {'Authorization': 'Bearer ' + token}
        base = 'https://localhost:7434/api/workspaces'
        dados = http_get(ctx, base, 3, headers)
        workspaces = dados.get('workspaces', []) if isinstance(dados, dict) else dados
        terminais = []
        erros = []
        for w in workspaces:
            feed = http_get(ctx, base + '/' + quote(str(w['id']), safe='') + '/feed', 3, headers)
            itens = feed.get('items', []) if isinstance(feed, dict) else feed
            for item in itens:
                # Formato novo do feed: kind == 'terminal', campos em item['terminal'];
                # os antigos (type == 'terminal' ou agentType no item) seguem aceitos.
                if (item.get('kind') != 'terminal' and item.get('type') != 'terminal'
                        and 'agentType' not in item):
                    continue
                terminal = interpretar(item, w.get('name', w['id']))
                terminal['pasta'], erro = _pasta(ctx, terminal['id'], headers)
                if erro:
                    erros.append(erro)
                terminais.append(terminal)
        resultado = {'ligado': True, 'terminais': terminais}
        if erros:
            resultado['erro'] = '; '.join(sorted(set(erros)))
        return resultado
    except Exception:
        # Nunca incluir exceção potencialmente contendo headers/token.
        return {'ligado': True, 'erro': 'Wire indisponível'}
