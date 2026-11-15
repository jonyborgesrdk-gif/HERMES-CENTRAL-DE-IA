"""Erros de ferramenta dos agentes (E20): captura, estado local, resumo público e registro.

Só biblioteca padrão; nenhum outro módulo do repositório; sem processo filho e sem rede.
Os transcripts só são abertos para leitura. Nenhum texto de erro, de comando ou de pasta é
gravado: erros-ocorrencias.json guarda só valores de lista fixa, números e o ponteiro
(arquivo, posição, chave) para o comando painel-erro achar o texto na máquina.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time

try:
    from compression import zstd
except ImportError:
    zstd = None

VERSAO = 1
ARQ_OCORRENCIAS = "erros-ocorrencias.json"
ARQ_REGISTRO = "erros-registro.json"
ARQ_TRAVA = "erros-registro.trava"
DIR_EVIDENCIAS = "erros-evidencias"
AGENTES = ("claude", "codex")
FERRAMENTAS = ("bash", "powershell", "read", "edit", "write", "grep", "glob", "agent", "web", "mcp",
               "comando", "arquivo", "nenhuma", "outra")
CLASSES = ("permissao_negada", "interrompido", "codigo_saida", "edicao", "arquivo", "tempo_esgotado",
           "validacao", "api", "outra")
SO_CONTAM = ("permissao_negada", "interrompido", "api")
ESTADOS = ("aberto", "corrigido", "verificado", "sem_tratamento", "contado")
TIPOS_EVIDENCIA = ("teste", "comando")
PRAZO_ABERTO_S = 7 * 86400
RETENCAO_S = 30 * 86400
JANELA_ARQUIVO_S = 7 * 86400
TOLERANCIA_FUTURO_S = 300
MAX_OCORRENCIAS = 5000
MAX_ITENS_PUBLICOS = 30
MAX_ABERTOS_PUBLICOS = 20
ORCAMENTO_BYTES = 8 * 1024 * 1024
MAX_LINHA = 16 * 1024 * 1024
JANELA_TOOL_USE = 256 * 1024
MAX_ZST = 64 * 1024 * 1024
MAX_EVIDENCIA = 256 * 1024
MAX_CODIGO = 4294967295
MIN_CODIGO = -2147483648
ESPERA_TRAVA_S = 5.0
TRAVA_VELHA_S = 30.0
ERRO_ESTADO = "erros dos agentes: estado local ilegível"
ERRO_LISTAR = "erros dos agentes: pastas de transcript sem leitura"


class ErroRegistro(ValueError):
    """Entrada ou transição inválida (o comando sai com 2)."""


class ErroEstado(OSError):
    """Estado ilegível, trava ocupada ou gravação impossível (o comando sai com 1)."""


_MSG_ILEGIVEL = "estado local ilegível"
_MSG_GRAVAR = "não foi possível gravar o estado"
_MSG_OCUPADO = "registro ocupado; tente de novo"
_RE_ID = re.compile(r"^[0-9a-f]{10}$")
_RE_EXIT = re.compile(r"\s*Exit code (\d{1,5})\b")
_RE_PERMISSAO = re.compile(r"(?i)\s*(permission to use |the user doesn't want to proceed|claude requested permissions)")
_RE_INTERROMPIDO = re.compile(r"\s*\[Request interrupted")
_RE_EDICAO = re.compile(r"(?i)(string to replace not found|found \d+ matches of the string)")
_RE_ARQUIVO = re.compile(r"(?i)(file has not been read yet|file does not exist|file content |.{0,20}no such file)")
_RE_TEMPO = re.compile(r"(?i)(command timed out|.{0,30}timed out after)")
_FILTRO_CLAUDE = re.compile(rb'"is_error"\s*:\s*true|"isApiErrorMessage"\s*:\s*true|\[Request interrupted')
_MARCADOR_ERRO = "<tool_use_error>"
_FERRAMENTAS_CLAUDE = {"Bash": "bash", "PowerShell": "powershell", "Read": "read", "Edit": "edit",
                       "NotebookEdit": "edit", "Write": "write", "Grep": "grep", "Glob": "glob",
                       "Agent": "agent", "Task": "agent", "WebFetch": "web", "WebSearch": "web"}
_REGISTRADOS = ("corrigido", "verificado")
_BLOCO_DESCARTE = 1024 * 1024
_TENTATIVAS_REPLACE = 5
_PAUSA_S = 0.05
_AUSENTE = object()
_CHAVES_OC = ("id", "agente", "ferramenta", "classe", "codigo", "hora", "arquivo", "offset", "chave")
_CHAVES_REGISTRO = ("estado", "corrigido_em", "nota", "verificado_em", "evidencia")
_RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------- valores

def _inteiro(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _numero(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(x)
    except OverflowError:
        return False


def _codigo_valido(x):
    return _inteiro(x) and MIN_CODIGO <= x <= MAX_CODIGO


def _texto(x):
    return isinstance(x, str) and x != ""


def _id_valido(x):
    return isinstance(x, str) and _RE_ID.fullmatch(x) is not None


def _epoch(valor):
    """Número finito vira float; texto ISO com Z ou deslocamento vira epoch; o resto vira None."""
    if _numero(valor):
        return float(valor)
    if not isinstance(valor, str):
        return None
    try:
        quando = datetime.fromisoformat(valor)
    except ValueError:
        return None
    if quando.tzinfo is None:
        return None
    try:
        return quando.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def id_de(agente, chave):
    return hashlib.sha256((agente + "\0" + chave).encode("utf-8", "surrogatepass")).hexdigest()[:10]


# ---------------------------------------------------------------- captura (§3)

def _conteudo_texto(item):
    conteudo = item.get("content")
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        return "\n".join(i["text"] for i in conteudo if isinstance(i, dict) and isinstance(i.get("text"), str))
    return ""


def classificar_claude(linha, item):
    decisao = linha.get("permissionDecision")
    if "toolDenialKind" in linha or (isinstance(decisao, dict) and decisao.get("decision") == "reject"):
        return ("permissao_negada", None)
    texto = _conteudo_texto(item)[:400]
    achado = _RE_EXIT.match(texto)
    if achado:
        return ("codigo_saida", int(achado.group(1)))
    if _RE_PERMISSAO.match(texto):
        return ("permissao_negada", None)
    if _RE_INTERROMPIDO.match(texto):
        return ("interrompido", None)
    corpo = texto.lstrip()
    embrulhado = False
    if corpo.startswith(_MARCADOR_ERRO):
        corpo = corpo[len(_MARCADOR_ERRO):].lstrip()
        embrulhado = True
    if _RE_EDICAO.match(corpo):
        return ("edicao", None)
    if _RE_ARQUIVO.match(corpo):
        return ("arquivo", None)
    if _RE_TEMPO.match(corpo):
        return ("tempo_esgotado", None)
    if embrulhado:
        return ("validacao", None)
    return ("outra", None)


def ferramenta_claude(nome):
    if not isinstance(nome, str):
        return "outra"
    if nome in _FERRAMENTAS_CLAUDE:
        return _FERRAMENTAS_CLAUDE[nome]
    if nome.startswith("mcp__"):
        return "mcp"
    return "outra"


def ocorrencias_claude(linha):
    """Ocorrências de uma linha do Claude Code: [{"chave", "classe", "codigo", "hora", "tool_use_id"}]."""
    if not isinstance(linha, dict):
        return []
    tipo = linha.get("type")
    uuid = linha.get("uuid")
    hora = _epoch(linha.get("timestamp"))
    if tipo == "assistant":
        if linha.get("isApiErrorMessage") is True and _texto(uuid):
            status = linha.get("apiErrorStatus")
            codigo = status if _inteiro(status) and 100 <= status <= 599 else None
            return [{"chave": "api:" + uuid, "classe": "api", "codigo": codigo, "hora": hora, "tool_use_id": None}]
        return []
    if tipo != "user":
        return []
    mensagem = linha.get("message")
    conteudo = mensagem.get("content") if isinstance(mensagem, dict) else None
    if not isinstance(conteudo, list):
        return []
    saida = []
    interrupcao = None
    for item in conteudo:
        if not isinstance(item, dict):
            continue
        tipo_item = item.get("type")
        if tipo_item == "tool_result":
            tool_use_id = item.get("tool_use_id")
            if item.get("is_error") is True and _texto(tool_use_id):
                classe, codigo = classificar_claude(linha, item)
                saida.append({"chave": tool_use_id, "classe": classe, "codigo": codigo, "hora": hora,
                              "tool_use_id": tool_use_id})
        elif tipo_item == "text" and interrupcao is None:
            texto = item.get("text")
            if isinstance(texto, str) and _RE_INTERROMPIDO.match(texto) and _texto(uuid):
                interrupcao = {"chave": "int:" + uuid, "classe": "interrompido", "codigo": None, "hora": hora,
                               "tool_use_id": None}
    if not saida and interrupcao is not None:
        saida.append(interrupcao)
    return saida


def ocorrencias_codex(linha):
    """Ocorrências de uma linha do Codex: [{"chave", "ferramenta", "classe", "codigo", "hora"}]."""
    if not isinstance(linha, dict) or linha.get("type") != "event_msg":
        return []
    carga = linha.get("payload")
    if not isinstance(carga, dict):
        return []
    tipo = carga.get("type")
    if tipo == "item_completed":
        item = carga.get("item")
        if not isinstance(item, dict) or item.get("status") != "failed":
            return []
        thread_id = carga.get("thread_id")
        item_id = item.get("id")
        if not (_texto(thread_id) and _texto(item_id)):
            return []
        tipo_item = item.get("type")
        if tipo_item == "CommandExecution":
            saida = item.get("exit_code")
            ferramenta, classe, codigo = "comando", "codigo_saida", (saida if _codigo_valido(saida) else None)
        elif tipo_item == "McpToolCall":
            ferramenta, classe, codigo = "mcp", "outra", None
        elif tipo_item == "FileChange":
            ferramenta, classe, codigo = "arquivo", "outra", None
        else:
            ferramenta, classe, codigo = "outra", "outra", None
        fim_ms = carga.get("completed_at_ms")
        hora = fim_ms / 1000 if _numero(fim_ms) and fim_ms > 0 else _epoch(linha.get("timestamp"))
        return [{"chave": thread_id + ":" + item_id, "ferramenta": ferramenta, "classe": classe,
                 "codigo": codigo, "hora": hora}]
    if tipo == "turn_aborted":
        turn_id = carga.get("turn_id")
        if not _texto(turn_id):
            return []
        classe = "interrompido" if carga.get("reason") == "interrupted" else "outra"
        return [{"chave": "abort:" + turn_id, "ferramenta": "nenhuma", "classe": classe, "codigo": None,
                 "hora": _epoch(linha.get("timestamp"))}]
    return []


def _passa_filtro(agente, bruto):
    if agente == "claude":
        return _FILTRO_CLAUDE.search(bruto) is not None
    return b'"failed"' in bruto or b"turn_aborted" in bruto


def _decodificar(bruto):
    try:
        return json.loads(bruto)
    except (ValueError, RecursionError):
        return None


def _extrair(agente, obj):
    return ocorrencias_claude(obj) if agente == "claude" else ocorrencias_codex(obj)


def _tool_use_em(janela, tool_use_id):
    marca = tool_use_id.encode("utf-8", "surrogatepass")
    for bruto in reversed(janela.split(b"\n")):
        if marca not in bruto:
            continue
        obj = _decodificar(bruto)
        mensagem = obj.get("message") if isinstance(obj, dict) else None
        conteudo = mensagem.get("content") if isinstance(mensagem, dict) else None
        if not isinstance(conteudo, list):
            continue
        for item in conteudo:
            if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("id") == tool_use_id:
                return item
    return None


def _tool_use(caminho, offset_da_linha, tool_use_id):
    """O item tool_use com esse id nos JANELA_TOOL_USE bytes antes da linha, ou None."""
    if not _texto(tool_use_id) or not _inteiro(offset_da_linha) or offset_da_linha <= 0:
        return None
    inicio = max(0, offset_da_linha - JANELA_TOOL_USE)
    try:
        with open(caminho, "rb") as f:
            f.seek(inicio)
            janela = f.read(offset_da_linha - inicio)
    except (OSError, ValueError):
        return None
    return _tool_use_em(janela, tool_use_id)


# ---------------------------------------------------------------- estado em disco (§2)

def _serializar(obj):
    return (json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def _gravar(caminho, dados_bytes):
    """Gravação atômica na mesma pasta; OSError que sobra vira ErroEstado, sem deixar temporário."""
    caminho = Path(caminho)
    temporario = None
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        fd, temporario = tempfile.mkstemp(prefix="." + caminho.name + ".", suffix=".tmp", dir=caminho.parent)
        with os.fdopen(fd, "wb") as f:
            f.write(dados_bytes)
            f.flush()
            os.fsync(f.fileno())
        tentativa = 0
        while True:
            try:
                os.replace(temporario, caminho)
                temporario = None
                return
            except PermissionError:
                if tentativa >= _TENTATIVAS_REPLACE:
                    raise
                tentativa += 1
                time.sleep(_PAUSA_S)
    except OSError:
        if temporario is not None:
            try:
                os.unlink(temporario)
            except OSError:
                pass
        raise ErroEstado(_MSG_GRAVAR) from None


def _ler_json(caminho):
    try:
        with open(caminho, "rb") as f:
            bruto = f.read()
    except FileNotFoundError:
        return _AUSENTE
    except OSError:
        raise ErroEstado(_MSG_ILEGIVEL) from None
    try:
        return json.loads(bruto)
    except (ValueError, RecursionError):
        raise ErroEstado(_MSG_ILEGIVEL) from None


def _versao_ok(obj):
    return isinstance(obj, dict) and _inteiro(obj.get("versao")) and obj["versao"] == VERSAO


def _oc_publica_valida(ident, oc):
    return (isinstance(oc, dict) and _id_valido(ident) and oc.get("id") == ident
            and oc.get("agente") in AGENTES and oc.get("ferramenta") in FERRAMENTAS
            and oc.get("classe") in CLASSES
            and "codigo" in oc and (oc["codigo"] is None or _codigo_valido(oc["codigo"]))
            and _numero(oc.get("hora")))


def _oc_valida(ident, oc):
    """Formato exigido na leitura (§2): as nove chaves presentes ("codigo" pode valer null) e de tipo válido."""
    return (isinstance(oc, dict) and all(k in oc for k in _CHAVES_OC) and _oc_publica_valida(ident, oc)
            and isinstance(oc["arquivo"], str) and _inteiro(oc["offset"]) and oc["offset"] >= 0
            and isinstance(oc["chave"], str))


def carregar_ocorrencias(dir_dados):
    """O estado da captura; None se o arquivo não existe; ErroEstado se ilegível."""
    obj = _ler_json(Path(dir_dados) / ARQ_OCORRENCIAS)
    if obj is _AUSENTE:
        return None
    if not (_versao_ok(obj) and isinstance(obj.get("arquivos"), dict) and isinstance(obj.get("ocorrencias"), dict)
            and _numero(obj.get("criado_em"))):
        raise ErroEstado(_MSG_ILEGIVEL)
    arquivos = {}
    for caminho, entrada in obj["arquivos"].items():
        if (isinstance(entrada, dict) and _inteiro(entrada.get("offset")) and entrada["offset"] >= 0
                and _inteiro(entrada.get("tam")) and entrada["tam"] >= 0 and _numero(entrada.get("mtime"))
                and ("fim" not in entrada or isinstance(entrada["fim"], bool))):
            arquivos[caminho] = {"offset": entrada["offset"], "tam": entrada["tam"], "mtime": float(entrada["mtime"])}
            if "fim" in entrada:
                arquivos[caminho]["fim"] = entrada["fim"]
    ocorrencias = {}
    for ident, oc in obj["ocorrencias"].items():
        if _oc_valida(ident, oc):
            ocorrencias[ident] = {"id": ident, "agente": oc["agente"], "ferramenta": oc["ferramenta"],
                                  "classe": oc["classe"], "codigo": oc["codigo"], "hora": float(oc["hora"]),
                                  "arquivo": oc["arquivo"], "offset": oc["offset"], "chave": oc["chave"]}
    return {"versao": VERSAO, "criado_em": float(obj["criado_em"]), "arquivos": arquivos, "ocorrencias": ocorrencias}


def _registro_valido(registro):
    return isinstance(registro, dict) and registro.get("estado") in _REGISTRADOS


def _registro_completo(ident, registro):
    """Formato completo do §2, exigido na leitura do arquivo; a leitura não abre o arquivo de evidência."""
    if not isinstance(registro, dict) or not all(k in registro for k in _CHAVES_REGISTRO):
        return False
    if not _numero(registro["corrigido_em"]) or not (registro["nota"] is None or isinstance(registro["nota"], str)):
        return False
    if registro["estado"] == "corrigido":
        return registro["verificado_em"] is None and registro["evidencia"] is None
    if registro["estado"] != "verificado" or not _numero(registro["verificado_em"]):
        return False
    evidencia = registro["evidencia"]
    return (isinstance(evidencia, dict) and evidencia.get("tipo") in TIPOS_EVIDENCIA
            and isinstance(evidencia.get("comando"), str) and _codigo_valido(evidencia.get("codigo"))
            and evidencia.get("arquivo") == DIR_EVIDENCIAS + "/" + ident + ".txt"
            and isinstance(evidencia.get("sha256"), str) and _RE_SHA256.fullmatch(evidencia["sha256"]) is not None
            and _inteiro(evidencia.get("bytes")) and 1 <= evidencia["bytes"] <= MAX_EVIDENCIA)


def carregar_registro(dir_dados, tolerante):
    """O dict registros ({} se o arquivo não existe); ilegível = {} se tolerante, senão ErroEstado."""
    try:
        obj = _ler_json(Path(dir_dados) / ARQ_REGISTRO)
        if obj is _AUSENTE:
            return {}
        if not (_versao_ok(obj) and isinstance(obj.get("registros"), dict)):
            raise ErroEstado(_MSG_ILEGIVEL)
    except ErroEstado:
        if tolerante:
            return {}
        raise
    return {ident: r for ident, r in obj["registros"].items() if _id_valido(ident) and _registro_completo(ident, r)}


def _gravar_ocorrencias(dir_dados, estado):
    try:
        _gravar(Path(dir_dados) / ARQ_OCORRENCIAS, _serializar(estado))
    except ErroEstado:
        pass    # a próxima passada refaz o trabalho; o id impede duplicar


# ---------------------------------------------------------------- varredura (§4)

def _buscar(raiz, buscas):
    raiz = Path(os.path.abspath(raiz))
    try:
        with os.scandir(raiz):
            pass
    except FileNotFoundError:
        return []
    achados = []
    for recursivo, padrao in buscas:
        achados.extend(raiz.rglob(padrao) if recursivo else raiz.glob(padrao))
    return achados


def _listar(raiz_claude, raiz_codex, agora_s):
    buscas_codex = [(True, "*.jsonl")]
    if zstd is not None:
        buscas_codex.append((True, "*.jsonl.zst"))
    candidatos = [("claude", p) for p in _buscar(raiz_claude, [(False, "*/*.jsonl"), (False, "*/*/subagents/*.jsonl")])]
    candidatos += [("codex", p) for p in _buscar(raiz_codex, buscas_codex)]
    listados = []
    vistos = set()
    for agente, p in candidatos:
        caminho = str(p)
        if caminho in vistos:
            continue
        vistos.add(caminho)
        try:
            info = os.stat(caminho)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or agora_s - info.st_mtime > JANELA_ARQUIVO_S:
            continue
        listados.append((caminho, agente, info.st_size, info.st_mtime))
    listados.sort(key=lambda a: (-a[3], a[0]))
    return listados


def _erros_leitura():
    erros = (OSError, EOFError)
    erro_zstd = getattr(zstd, "ZstdError", None) if zstd is not None else None
    if isinstance(erro_zstd, type) and issubclass(erro_zstd, BaseException):
        erros += (erro_zstd,)
    return erros


def _descartar_resto(f, limite=None):
    """Lê o resto da linha em blocos de 1 MiB; devolve (bytes lidos, achou a quebra)."""
    lidos = 0
    while limite is None or lidos < limite:
        bloco = f.readline(_BLOCO_DESCARTE)
        if not bloco:
            return lidos, False
        lidos += len(bloco)
        if bloco.endswith(b"\n"):
            return lidos, True
    return lidos, False


def _ler_jsonl(caminho, agente, offset, restante):
    """Linhas completas a partir de offset; devolve (offset novo, restante, [(início da linha, ocorrência)])."""
    achadas = []
    with open(caminho, "rb") as f:
        f.seek(offset)
        while restante > 0:
            ini = f.tell()
            bruto = f.readline(MAX_LINHA + 1)
            if not bruto:
                break
            if not bruto.endswith(b"\n"):
                if len(bruto) <= MAX_LINHA:
                    break                       # linha incompleta: não consome
                _lidos, achou = _descartar_resto(f)
                if not achou:
                    break                       # gigante e incompleta: offset fica em ini
                offset = f.tell()
                restante -= offset - ini
                continue                        # gigante: descartada sem decodificar
            offset = f.tell()
            restante -= len(bruto)
            if _passa_filtro(agente, bruto):
                for oc in _extrair(agente, _decodificar(bruto)):
                    achadas.append((ini, oc))
    return offset, restante, achadas


def _ler_zst(caminho, agente, offset):
    """Retoma do offset no fluxo descomprimido, com a regra de linha do .jsonl.

    Para no fim do fluxo (fim verdadeiro) ou quando os bytes percorridos NESTA passada chegam a
    MAX_ZST (fim falso: a passada seguinte retoma daqui). Devolve (offset novo, fim, [(início, ocorrência)]).
    """
    achadas = []
    with zstd.open(caminho, "rb") as f:
        pulados = 0
        while pulados < offset:                 # o formato não permite ir direto à posição
            bloco = f.read(min(_BLOCO_DESCARTE, offset - pulados))
            if not bloco:
                return offset, True, achadas    # o fluxo acabou antes: não lê mais nada
            pulados += len(bloco)
        pos = offset
        percorridos = 0
        while True:
            if percorridos >= MAX_ZST:
                return pos, False, achadas      # teto da passada
            ini = pos
            bruto = f.readline(MAX_LINHA + 1)
            if not bruto:
                return pos, True, achadas
            if not bruto.endswith(b"\n"):
                if len(bruto) <= MAX_LINHA:
                    return pos, True, achadas   # trecho final sem \n: não consome
                lidos, achou = _descartar_resto(f)
                if not achou:
                    return pos, True, achadas   # gigante sem \n até o fim: não consome
                pos += len(bruto) + lidos
                percorridos += len(bruto) + lidos
                continue                        # gigante completa: consumida sem decodificar
            pos += len(bruto)
            percorridos += len(bruto)
            if _passa_filtro(agente, bruto):
                for oc in _extrair(agente, _decodificar(bruto)):
                    achadas.append((ini, oc))


def _acolher(ocorrencias, agente, caminho, origem, ini, oc, agora_s, criado_em):
    hora = oc.get("hora")
    if hora is None or hora > agora_s + TOLERANCIA_FUTURO_S:
        hora = agora_s
    if hora < criado_em:
        return
    chave = oc["chave"]
    ident = id_de(agente, chave)
    if ident in ocorrencias:
        return
    if agente == "claude":
        tool_use_id = oc.get("tool_use_id")
        if tool_use_id:
            item = _tool_use(caminho, ini, tool_use_id)
            ferramenta = ferramenta_claude(item.get("name")) if item is not None else "outra"
        else:
            ferramenta = "nenhuma"
    else:
        ferramenta = oc["ferramenta"]
    ocorrencias[ident] = {"id": ident, "agente": agente, "ferramenta": ferramenta, "classe": oc["classe"],
                          "codigo": oc["codigo"], "hora": float(hora), "arquivo": origem, "offset": ini,
                          "chave": chave}


def varrer(dir_dados, agora_s, raiz_claude=None, raiz_codex=None):
    """Uma passada da captura; devolve (estado, erro). Só lê os transcripts; só grava em dir_dados."""
    dir_dados = Path(dir_dados)
    agora_s = float(agora_s)
    if raiz_claude is None:
        raiz_claude = Path.home() / ".claude" / "projects"
    if raiz_codex is None:
        raiz_codex = Path.home() / ".codex" / "sessions"
    try:
        estado = carregar_ocorrencias(dir_dados)
    except ErroEstado:
        return None, ERRO_ESTADO
    try:
        listados = _listar(raiz_claude, raiz_codex, agora_s)
    except OSError:
        return None, ERRO_LISTAR
    if estado is None:                          # primeira passada: só marca a posição (D6)
        marcados = {}
        for c, _a, tam, mt in listados:
            marcados[c] = {"offset": tam, "tam": tam, "mtime": mt}
            if c.endswith(".zst"):
                marcados[c]["fim"] = True       # já processado: não é aberto enquanto não mudar
        estado = {"versao": VERSAO, "criado_em": agora_s, "arquivos": marcados, "ocorrencias": {}}
        _gravar_ocorrencias(dir_dados, estado)
        return estado, None
    anteriores = estado["arquivos"]
    arquivos = {c: anteriores[c] for c, _a, _t, _m in listados if c in anteriores}
    ocorrencias = dict(estado["ocorrencias"])
    criado_em = estado["criado_em"]
    erros_leitura = _erros_leitura()
    restante = ORCAMENTO_BYTES
    for caminho, agente, tam, mtime in listados:
        if restante <= 0:
            break
        entrada = anteriores.get(caminho)
        if caminho.endswith(".zst"):
            mesmo = entrada is not None and entrada["tam"] == tam and entrada["mtime"] == mtime
            if mesmo and entrada.get("fim", True) is not False:
                continue                        # fim verdadeiro ou ausente: pula sem abrir
            try:
                lidos, fim, achadas = _ler_zst(caminho, agente, entrada["offset"] if mesmo else 0)
            except erros_leitura:
                continue                        # ignora nesta passada, sem mexer na entrada
            restante -= tam
            arquivos[caminho] = {"offset": lidos, "tam": tam, "mtime": mtime, "fim": fim}
            origem = caminho[:-len(".zst")]
        else:
            offset = entrada["offset"] if entrada is not None else 0
            if tam < offset:
                offset = 0
            if tam == offset:
                arquivos[caminho] = {"offset": offset, "tam": tam, "mtime": mtime}
                continue
            try:
                offset, restante, achadas = _ler_jsonl(caminho, agente, offset, restante)
            except erros_leitura:
                continue                        # ignora nesta passada, sem mexer na entrada
            arquivos[caminho] = {"offset": offset, "tam": tam, "mtime": mtime}
            origem = caminho
        for ini, oc in achadas:
            _acolher(ocorrencias, agente, caminho, origem, ini, oc, agora_s, criado_em)
    for ident in [i for i, oc in ocorrencias.items() if agora_s - oc["hora"] > RETENCAO_S]:
        del ocorrencias[ident]
    if len(ocorrencias) > MAX_OCORRENCIAS:
        manter = sorted(ocorrencias.values(), key=lambda o: (o["hora"], o["id"]), reverse=True)[:MAX_OCORRENCIAS]
        ocorrencias = {o["id"]: o for o in manter}
    estado = {"versao": VERSAO, "criado_em": criado_em, "arquivos": arquivos, "ocorrencias": ocorrencias}
    _gravar_ocorrencias(dir_dados, estado)
    return estado, None


# ---------------------------------------------------------------- estado derivado e resumo (§5)

def _registro_de(registros, ident):
    registro = registros.get(ident) if isinstance(registros, dict) else None
    return registro if _registro_valido(registro) else None


def _instante(registro):
    valores = [registro.get(k) for k in ("corrigido_em", "verificado_em")] if isinstance(registro, dict) else []
    return max((float(v) for v in valores if _numero(v)), default=0.0)


def estado_de(oc, registros, agora_s):
    if oc["classe"] in SO_CONTAM:
        return "contado"
    registro = _registro_de(registros, oc["id"])
    if registro is not None:
        return registro["estado"]
    if agora_s - oc["hora"] > PRAZO_ABERTO_S:
        return "sem_tratamento"
    return "aberto"


def _evidencia_publica(registro, estado):
    if estado != "verificado" or not isinstance(registro, dict):
        return None
    evidencia = registro.get("evidencia")
    if isinstance(evidencia, dict) and evidencia.get("tipo") in TIPOS_EVIDENCIA and _codigo_valido(evidencia.get("codigo")):
        return {"tipo": evidencia["tipo"], "codigo": evidencia["codigo"]}
    return None


def _validas(estado, registros, agora_s):
    ocorrencias = estado.get("ocorrencias") if isinstance(estado, dict) else None
    if not isinstance(ocorrencias, dict):
        return
    for ident, oc in ocorrencias.items():
        if not _oc_publica_valida(ident, oc):
            continue
        situacao = estado_de(oc, registros, agora_s)
        registro = _registro_de(registros, ident) if situacao in _REGISTRADOS else None
        yield oc, situacao, registro


def _item_publico(oc, situacao, registro):
    return {"id": oc["id"], "agente": oc["agente"], "ferramenta": oc["ferramenta"], "classe": oc["classe"],
            "codigo": oc["codigo"], "hora": float(oc["hora"]), "estado": situacao,
            "evidencia": _evidencia_publica(registro, situacao)}


def resumo_publico(estado, registros, agora_s):
    """Só listas fixas, números e o id curto: nada de arquivo, offset, chave, nota ou comando."""
    contagens = dict.fromkeys(ESTADOS, 0)
    por_classe = dict.fromkeys(CLASSES, 0)
    por_agente = dict.fromkeys(AGENTES, 0)
    ultimas = 0
    abertos = []
    registrados = []
    for oc, situacao, registro in _validas(estado, registros, agora_s):
        contagens[situacao] += 1
        por_classe[oc["classe"]] += 1
        por_agente[oc["agente"]] += 1
        if agora_s - oc["hora"] <= 86400:
            ultimas += 1
        if situacao == "aberto":
            abertos.append(_item_publico(oc, situacao, registro))
        elif situacao in _REGISTRADOS:
            registrados.append((_instante(registro), _item_publico(oc, situacao, registro)))
    abertos.sort(key=lambda i: (i["hora"], i["id"]), reverse=True)
    itens = abertos[:MAX_ABERTOS_PUBLICOS]
    registrados.sort(key=lambda par: (par[0], par[1]["id"]), reverse=True)
    itens += [item for _instante_reg, item in registrados][:max(0, MAX_ITENS_PUBLICOS - len(itens))]
    desde = estado.get("criado_em") if isinstance(estado, dict) else None
    return {"estado": "ok", "desde": float(desde) if _numero(desde) else None,
            "contagens": contagens, "por_classe": por_classe, "por_agente": por_agente,
            "ultimas_24h": ultimas, "itens": itens[:MAX_ITENS_PUBLICOS]}


def listar(dir_dados, agora_s):
    """Ocorrências do mais recente para o mais antigo, com os campos públicos e os do registro."""
    estado = carregar_ocorrencias(dir_dados)
    registros = carregar_registro(dir_dados, False)
    if estado is None:
        return []
    saida = []
    for oc, situacao, registro in _validas(estado, registros, agora_s):
        item = _item_publico(oc, situacao, registro)
        r = registro or {}
        item["corrigido_em"] = float(r["corrigido_em"]) if _numero(r.get("corrigido_em")) else None
        item["verificado_em"] = float(r["verificado_em"]) if _numero(r.get("verificado_em")) else None
        item["nota"] = r["nota"] if isinstance(r.get("nota"), str) else None
        saida.append(item)
    saida.sort(key=lambda i: (i["hora"], i["id"]), reverse=True)
    return saida


# ---------------------------------------------------------------- texto na máquina (§6)

def _abrir_transcript(caminho):
    try:
        return open(caminho, "rb"), False
    except FileNotFoundError:
        if zstd is None:
            raise
        return zstd.open(caminho + ".zst", "rb"), True


def _linha_com_chave(agente, bruto, chave):
    if not bruto or (not bruto.endswith(b"\n") and len(bruto) > MAX_LINHA):
        return None
    if not _passa_filtro(agente, bruto):
        return None
    obj = _decodificar(bruto)
    return obj if any(o["chave"] == chave for o in _extrair(agente, obj)) else None


def _achar_linha(caminho, offset, agente, chave):
    """(início, linha decodificada) da linha cuja ocorrência tem essa chave, ou None."""
    f, comprimido = _abrir_transcript(caminho)
    with f:
        f.seek(offset)
        linha = _linha_com_chave(agente, f.readline(MAX_LINHA + 1), chave)
        if linha is not None:
            return offset, linha
        f.seek(0)
        pos = 0
        while not comprimido or pos < MAX_ZST:
            ini = pos
            bruto = f.readline(MAX_LINHA + 1)
            if not bruto:
                break
            pos += len(bruto)
            if not bruto.endswith(b"\n") and len(bruto) > MAX_LINHA:
                lidos, achou = _descartar_resto(f, MAX_ZST - pos if comprimido else None)
                pos += lidos
                if not achou:
                    break
                continue
            linha = _linha_com_chave(agente, bruto, chave)
            if linha is not None:
                return ini, linha
    return None


def _so_texto(valor):
    return valor if isinstance(valor, str) else ""


def _texto_claude(linha, chave, caminho, ini):
    pasta = _so_texto(linha.get("cwd"))
    sessao = _so_texto(linha.get("sessionId"))
    if linha.get("type") == "assistant":
        return {"erro": _so_texto(linha.get("error")), "entrada": "", "pasta": pasta, "sessao": sessao}
    conteudo = linha["message"]["content"]
    for item in conteudo:
        if (isinstance(item, dict) and item.get("type") == "tool_result" and item.get("is_error") is True
                and item.get("tool_use_id") == chave):
            uso = _tool_use(caminho, ini, chave)
            entrada = json.dumps(uso["input"], ensure_ascii=False) if isinstance(uso, dict) and "input" in uso else ""
            return {"erro": _conteudo_texto(item), "entrada": entrada, "pasta": pasta, "sessao": sessao}
    for item in conteudo:
        if (isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
                and _RE_INTERROMPIDO.match(item["text"])):
            return {"erro": item["text"], "entrada": "", "pasta": pasta, "sessao": sessao}
    return None


def _texto_codex(linha):
    carga = linha["payload"]
    if carga.get("type") == "turn_aborted":
        return {"erro": _so_texto(carga.get("reason")), "entrada": "", "pasta": "",
                "sessao": _so_texto(carga.get("thread_id"))}
    item = carga["item"]
    if isinstance(item.get("aggregated_output"), str):
        erro = item["aggregated_output"]
    elif "result" in item:
        erro = json.dumps(item.get("result"), ensure_ascii=False)
    else:
        erro = ""
    comando = item.get("command")
    if isinstance(comando, str):
        entrada = comando
    elif isinstance(comando, list) and all(isinstance(p, str) for p in comando):
        entrada = " ".join(comando)
    else:
        entrada = ""
    return {"erro": erro, "entrada": entrada, "pasta": _so_texto(item.get("cwd")), "sessao": carga["thread_id"]}


def ler_texto(oc):
    """{"erro", "entrada", "pasta", "sessao"} da linha cuja chave confere com a ocorrência, ou None."""
    if not isinstance(oc, dict):
        return None
    agente, caminho, offset, chave = oc.get("agente"), oc.get("arquivo"), oc.get("offset"), oc.get("chave")
    if agente not in AGENTES or not _texto(caminho) or not _inteiro(offset) or offset < 0 or not _texto(chave):
        return None
    try:
        achado = _achar_linha(caminho, offset, agente, chave)
    except _erros_leitura() + (ValueError, OverflowError):
        return None
    if achado is None:
        return None
    ini, linha = achado
    if agente == "claude":
        return _texto_claude(linha, chave, caminho, ini)
    return _texto_codex(linha)


# ---------------------------------------------------------------- registro (§6)

@contextmanager
def _trava(dir_dados):
    dir_dados = Path(dir_dados)
    caminho = dir_dados / ARQ_TRAVA
    try:
        dir_dados.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ErroEstado(_MSG_GRAVAR) from None
    limite = time.monotonic() + ESPERA_TRAVA_S
    negado = False
    while True:
        try:
            fd = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            negado = False
        except PermissionError:
            # No Windows, criar a trava enquanto o dono anterior acaba de apagá-la (nome ainda em
            # exclusão pendente) dá acesso negado em vez de "já existe": é trava ocupada, não erro.
            negado = True
        except OSError:
            raise ErroEstado(_MSG_GRAVAR) from None
        try:
            velha = time.time() - os.stat(caminho).st_mtime > TRAVA_VELHA_S
        except OSError:
            velha = False
        if velha:
            try:
                os.unlink(caminho)
                continue
            except FileNotFoundError:
                continue
            except OSError:
                pass
        if time.monotonic() >= limite:
            raise ErroEstado(_MSG_GRAVAR if negado else _MSG_OCUPADO)
        time.sleep(_PAUSA_S)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(caminho)
        except OSError:
            pass


def _preparar(dir_dados, ident):
    if not _id_valido(ident):
        raise ErroRegistro("id inválido")
    estado = carregar_ocorrencias(dir_dados)
    ocorrencias = estado["ocorrencias"] if estado is not None else {}
    oc = ocorrencias.get(ident)
    if oc is None:
        raise ErroRegistro("id desconhecido")
    return ocorrencias, oc


def _barrar_contado(situacao, oc):
    if situacao == "contado":
        raise ErroRegistro("esta ocorrência só é contada (classe %s); não entra no fluxo de correção" % oc["classe"])


def _ler_saida(caminho_saida):
    dados = b""
    regular = False
    if isinstance(caminho_saida, (str, os.PathLike)):
        try:
            regular = stat.S_ISREG(os.stat(caminho_saida).st_mode)
            if regular:
                with open(caminho_saida, "rb") as f:
                    dados = f.read(MAX_EVIDENCIA + 1)
        except (OSError, TypeError, ValueError):
            regular = False
    if not regular:
        raise ErroRegistro("--saida: arquivo ausente")
    if not dados:
        raise ErroRegistro("--saida: arquivo vazio")
    if len(dados) > MAX_EVIDENCIA:
        raise ErroRegistro("--saida: arquivo maior que %d bytes" % MAX_EVIDENCIA)
    return dados


def _caminho_evidencia(dir_dados, ident):
    return Path(dir_dados) / DIR_EVIDENCIAS / (ident + ".txt")


def _remover_vencidos(registros, ocorrencias, agora_s):
    vencidos = [i for i, r in registros.items() if i not in ocorrencias and agora_s - _instante(r) > RETENCAO_S]
    for ident in vencidos:
        del registros[ident]
    return vencidos


def _apagar_evidencias(dir_dados, idents):
    for ident in idents:
        try:
            os.unlink(_caminho_evidencia(dir_dados, ident))
        except OSError:
            pass


def _gravar_registro(dir_dados, registros):
    _gravar(Path(dir_dados) / ARQ_REGISTRO, _serializar({"versao": VERSAO, "registros": registros}))


def registrar_corrigido(dir_dados, id, nota, agora_s):
    dir_dados = Path(dir_dados)
    agora_s = float(agora_s)
    ocorrencias, oc = _preparar(dir_dados, id)
    with _trava(dir_dados):
        registros = carregar_registro(dir_dados, False)
        situacao = estado_de(oc, registros, agora_s)
        _barrar_contado(situacao, oc)
        if situacao == "corrigido":
            raise ErroRegistro("já está corrigido")
        if situacao == "verificado":
            raise ErroRegistro("já está verificado")
        novo = {"estado": "corrigido", "corrigido_em": agora_s, "nota": nota, "verificado_em": None, "evidencia": None}
        registros[id] = novo
        vencidos = _remover_vencidos(registros, ocorrencias, agora_s)
        _gravar_registro(dir_dados, registros)
        _apagar_evidencias(dir_dados, vencidos)
    return novo


def registrar_verificado(dir_dados, id, tipo, comando, codigo, caminho_saida, agora_s):
    dir_dados = Path(dir_dados)
    agora_s = float(agora_s)
    ocorrencias, oc = _preparar(dir_dados, id)
    if tipo not in TIPOS_EVIDENCIA:
        raise ErroRegistro("--tipo: use teste ou comando")
    if not _codigo_valido(codigo):
        raise ErroRegistro("--codigo: informe um inteiro")
    dados = _ler_saida(caminho_saida)
    with _trava(dir_dados):
        registros = carregar_registro(dir_dados, False)
        situacao = estado_de(oc, registros, agora_s)
        _barrar_contado(situacao, oc)
        if situacao == "verificado":
            raise ErroRegistro("já está verificado")
        anterior = registros.get(id) if situacao == "corrigido" else None
        anterior = anterior if isinstance(anterior, dict) else {}
        corrigido_em = float(anterior["corrigido_em"]) if _numero(anterior.get("corrigido_em")) else agora_s
        nota = anterior["nota"] if isinstance(anterior.get("nota"), str) else None
        novo = {"estado": "verificado", "corrigido_em": corrigido_em, "nota": nota, "verificado_em": agora_s,
                "evidencia": {"tipo": tipo, "comando": comando, "codigo": codigo,
                              "arquivo": DIR_EVIDENCIAS + "/" + id + ".txt",
                              "sha256": hashlib.sha256(dados).hexdigest(), "bytes": len(dados)}}
        registros[id] = novo
        vencidos = _remover_vencidos(registros, ocorrencias, agora_s)
        destino = _caminho_evidencia(dir_dados, id)
        _gravar(destino, dados)
        try:
            _gravar_registro(dir_dados, registros)
        except ErroEstado:
            try:
                os.unlink(destino)
            except OSError:
                pass
            raise
        _apagar_evidencias(dir_dados, vencidos)
    return novo


def diretorio_dados(ambiente=None):
    ambiente = os.environ if ambiente is None else ambiente
    pasta = ambiente.get("PAINEL_IAS_DIR")
    if pasta:
        return Path(pasta)
    return Path(ambiente.get("LOCALAPPDATA") or Path.home()) / "painel-ias"
