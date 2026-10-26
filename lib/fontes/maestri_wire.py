"""Leitura local do Wire; configuração ausente desliga a fonte."""
from urllib.parse import quote
from . import diretorio, http_get, mascarar


def interpretar(item, workspace):
    t = item.get('terminal', item)
    preview = t.get('preview') or []
    if isinstance(preview, str):
        preview = preview.splitlines()
    return {'id': t.get('id'), 'workspace': workspace, 'nome': t.get('name'),
            'tipo': t.get('agentType'), 'status': t.get('status'),
            'atencao': bool(t.get('needsAttention')), 'rodando': bool(t.get('isRunning')),
            'descarregado': bool(t.get('isUnloaded')),
            'preview': [mascarar(linha) for linha in preview[-10:]],
            'tela': 'ok' if preview else 'sem tela'}


def ler(ctx):
    arquivo = diretorio(ctx) / 'wire.conf'
    try:
        texto = arquivo.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {'ligado': False}
    except OSError:
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
        for w in workspaces:
            feed = http_get(ctx, base + '/' + quote(str(w['id']), safe='') + '/feed', 3, headers)
            itens = feed.get('items', []) if isinstance(feed, dict) else feed
            terminais.extend(interpretar(i, w.get('name', w['id'])) for i in itens
                             if i.get('type') == 'terminal' or 'agentType' in i)
        return {'ligado': True, 'terminais': terminais}
    except Exception:
        # Nunca incluir exceção potencialmente contendo headers/token.
        return {'ligado': True, 'erro': 'Wire indisponível'}
