"""Erros de ferramenta dos agentes (E20; Grok e Hermes na E30). Só leitura; nada de texto sai daqui."""
import os
from pathlib import Path

from . import agora, diretorio

try:
    from .. import erros_registro as registro
except ImportError:
    import erros_registro as registro

CHAVE = "erros_agentes"


def ler(ctx):
    dir_dados = diretorio(ctx)
    agora_s = agora(ctx)
    # Os mesmos caminhos de fontes/sessoes_grok e fontes/sessoes_hermes.
    raiz_grok = (Path(ctx["grok_dir"]) if "grok_dir" in ctx else Path.home() / ".grok") / "sessions"
    banco_hermes = Path(ctx.get("hermes_db") or
                        Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "hermes" / "state.db")
    estado, erro = registro.varrer(dir_dados, agora_s, ctx.get('claude_projects_dir'), ctx.get('codex_sessions_dir'),
                                   raiz_grok, banco_hermes)
    if erro:
        return {"erro": erro}
    return {CHAVE: registro.resumo_publico(estado, registro.carregar_registro(dir_dados, True), agora_s)}
