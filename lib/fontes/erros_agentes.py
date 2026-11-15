"""Erros de ferramenta dos agentes (E20). Só leitura dos transcripts; nada de texto sai daqui."""
from . import agora, diretorio

try:
    from .. import erros_registro as registro
except ImportError:
    import erros_registro as registro

CHAVE = "erros_agentes"


def ler(ctx):
    dir_dados = diretorio(ctx)
    agora_s = agora(ctx)
    estado, erro = registro.varrer(dir_dados, agora_s, ctx.get('claude_projects_dir'), ctx.get('codex_sessions_dir'))
    if erro:
        return {"erro": erro}
    return {CHAVE: registro.resumo_publico(estado, registro.carregar_registro(dir_dados, True), agora_s)}
