# -*- coding: utf-8 -*-
"""Fonte rede_uso (E22, §3 e §4; §7, itens 33 a 45 e 55): leitura e validação do resumo da captura e
registro no coletor sob PAINEL_REDE=1.

Só arquivos sintéticos em diretório temporário; nenhum teste abre sessão ETW, lê tráfego real nem toca o
diretório de dados de verdade.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib.fontes import presenca_processos
from lib.fontes import rede_uso as mod

AGORA = 1_793_433_712.0
MARCA = "MARCA-REDE-E22"
GRANDE = "1" + "0" * 400
PROIBIDAS = ("pid", "endereco", "porta", "caminho", "nome")
ZERADO = {"local": [0, 0], "privada": [0, 0], "tailnet": [0, 0], "internet": [0, 0]}


def cats(**pares):
    saida = {c: [0, 0] for c in mod.CATEGORIAS}
    saida.update(pares)
    return saida


def bom(estado="ativo", atras=5.0):
    return {"schema": 1, "gerado_em": AGORA - atras, "estado": estado, "motivo": None,
            "desde": AGORA - 3600.0, "janela_s": 60, "cpu_pct": 0.4, "eventos_s": 1236, "perdidos": 3,
            "retoma_em": AGORA + 50.0 if estado == "pausado_carga" else None, "conexoes_no_teto": False,
            "programas": [
                {"produto": "claude_cli", "conexoes": 3,
                 "janela": cats(internet=[1500000, 28000000]), "total": cats(internet=[90000000, 2000000000])},
                {"produto": "grok_cli", "conexoes": 1,
                 "janela": cats(local=[2000, 500], tailnet=[300000, 0]),
                 "total": cats(local=[2000, 500], tailnet=[300000, 0])}],
            "outros": {"conexoes": 42,
                       "janela": cats(local=[1000000, 1000000], internet=[200000, 4000000]),
                       "total": cats(local=[1000000, 1000000], privada=[5000, 0], internet=[200000, 4000000])}}


def esperado(estado="ativo", atras=5.0):
    return {"estado": estado, "gerado_em_epoch": AGORA - atras, "desde_epoch": AGORA - 3600.0, "janela_s": 60,
            "cpu_pct": 0.4, "eventos_s": 1236, "perdidos": 3,
            "retoma_em_epoch": AGORA + 50.0 if estado == "pausado_carga" else None, "conexoes_no_teto": False,
            "programas": [
                {"produto": "claude_cli", "rotulo": "Claude Code CLI", "familia": "claude", "tipo": "cli",
                 "conexoes": 3,
                 "janela": cats(internet=[1500000, 28000000]), "total": cats(internet=[90000000, 2000000000])},
                {"produto": "grok_cli", "rotulo": "Grok CLI", "familia": "grok", "tipo": "cli", "conexoes": 1,
                 "janela": cats(local=[2000, 500], tailnet=[300000, 0]),
                 "total": cats(local=[2000, 500], tailnet=[300000, 0])}],
            "outros": {"conexoes": 42,
                       "janela": cats(local=[1000000, 1000000], internet=[200000, 4000000]),
                       "total": cats(local=[1000000, 1000000], privada=[5000, 0], internet=[200000, 4000000])}}


def hostil():
    """Resumo válido com UM programa da tabela e chaves e textos hostis em todos os níveis."""
    obj = bom()
    del obj["programas"][1]
    sujeira = {nome: MARCA for nome in PROIBIDAS}
    obj.update(sujeira, extra=MARCA, motivo=MARCA)
    obj["programas"][0].update(sujeira, rotulo=MARCA, familia=MARCA, tipo=MARCA)
    obj["programas"][0]["janela"].update({MARCA: [1, 2], "pid": [4242, 1]})
    obj["programas"][0]["total"]["endereco"] = MARCA
    obj["programas"].append({"produto": MARCA, "conexoes": 9, "janela": cats(), "total": cats(), "nome": MARCA})
    obj["outros"].update(sujeira)
    obj["outros"]["janela"][MARCA] = [5, 6]
    obj["outros"]["total"]["porta"] = [54321, 0]
    return obj


def chaves(valor):
    if isinstance(valor, dict):
        for chave, item in valor.items():
            yield chave
            yield from chaves(item)
    elif isinstance(valor, list):
        for item in valor:
            yield from chaves(item)


class RedeUsoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.arquivo = self.dir / "rede-resumo.json"

    def gravar(self, obj):
        self.arquivo.write_text(json.dumps(obj), encoding="utf-8")

    def gravar_bytes(self, dados):
        self.arquivo.write_bytes(dados)

    def ler(self, agora=AGORA):
        r = mod.ler({"rede_resumo": str(self.arquivo), "agora": agora})
        json.dumps(r, allow_nan=False)
        return r

    def ok(self, agora=AGORA):
        r = self.ler(agora)
        self.assertEqual(set(r), {"rede_uso"}, r)
        return r["rede_uso"]

    def com_campo(self, campo, bruto, base=None):
        """Grava ``base`` (padrão: resumo ativo) com ``campo`` escrito no arquivo como o texto ``bruto``."""
        obj = bom() if base is None else base
        obj[campo] = "__BRUTO__"
        self.gravar_bytes(json.dumps(obj).replace('"__BRUTO__"', bruto).encode("utf-8"))

    # 33
    def test_33_arquivo_ausente_desligado(self):
        self.assertEqual(self.ler(), {"rede_uso": {"estado": "desligado"}})
        self.assertEqual(mod.ler({"dir": str(self.dir), "agora": AGORA}), {"rede_uso": {"estado": "desligado"}})
        self.assertEqual(os.listdir(self.dir), [])

    # 34
    def test_34_ativo_recente_com_rotulo_familia_e_tipo_da_tabela(self):
        obj = bom()
        obj["programas"][0].update(rotulo="Rótulo plantado", familia="plantada", tipo="plantado")
        self.gravar(obj)
        self.assertEqual(self.ok(), esperado())
        tabela = {linha["produto"]: linha for linha in presenca_processos.PRODUTOS}
        for programa in self.ok()["programas"]:
            linha = tabela[programa["produto"]]
            self.assertEqual((programa["rotulo"], programa["familia"], programa["tipo"]),
                             (linha["rotulo"], linha["familia"], linha["tipo"]))
        self.assertNotIn("plantad", json.dumps(self.ler(), ensure_ascii=False))
        self.assertEqual(mod.ler({"dir": str(self.dir), "agora": AGORA}), {"rede_uso": esperado()})

    # 35
    def test_35_parado_depois_de_30s_e_futuro(self):
        for estado in ("ativo", "pausado_carga"):
            with self.subTest(estado=estado):
                self.gravar(bom(estado, atras=31.0))
                self.assertEqual(self.ok(), {"estado": "parado", "ultimo_sinal_epoch": AGORA - 31.0})
                self.gravar(bom(estado, atras=30.0))
                self.assertEqual(self.ok(), esperado(estado, atras=30.0))
        self.gravar(bom(atras=-301.0))
        self.assertEqual(self.ler(), {"erro": mod.ERRO_FORMATO})
        self.gravar(bom(atras=-300.0))
        self.assertEqual(self.ok()["estado"], "ativo")

    # 36
    def test_36_pausado_encerrado_e_erro(self):
        self.gravar(bom("pausado_carga"))
        r = self.ok()
        self.assertEqual(r, esperado("pausado_carga"))
        self.assertEqual(r["retoma_em_epoch"], AGORA + 50.0)
        self.assertEqual(len(r["programas"]), 2)
        obj = bom()
        obj["retoma_em"] = AGORA + 50.0
        self.gravar(obj)
        self.assertIsNone(self.ok()["retoma_em_epoch"])
        self.gravar(bom("encerrado"))
        self.assertEqual(self.ok(), {"estado": "encerrado", "gerado_em_epoch": AGORA - 5.0})
        for motivo in ("sessao", "provedor", "consumidor"):
            with self.subTest(motivo=motivo):
                obj = bom("erro")
                obj["motivo"] = motivo
                self.gravar(obj)
                self.assertEqual(self.ok(), {"estado": "erro_captura", "motivo": motivo})
        for motivo in ("sem_elevacao", "plataforma", 7, None, ["sessao"], "ausente"):
            with self.subTest(motivo=motivo):
                obj = bom("erro")
                if motivo == "ausente":
                    del obj["motivo"]
                else:
                    obj["motivo"] = motivo
                self.gravar(obj)
                self.assertEqual(self.ok(), {"estado": "erro_captura", "motivo": "outro"})

    # 37
    def test_37_validacao_de_programas_pares_e_conexoes(self):
        obj = bom()
        primeiro = obj["programas"][0]
        obj["programas"] = ["texto", 5, None, {"produto": "nao_existe", "conexoes": 1},
                            {"produto": 7, "conexoes": 1}, {"conexoes": 1}, {"produto": ["claude_cli"]},
                            primeiro, dict(primeiro, conexoes=99), {"produto": "grok_cli", "janela": [1, 2]}]
        obj["programas"][7]["janela"].update(local=[-1, 5], privada=[1.5, 2], tailnet=[True, 1],
                                             internet=[1, 2, 3], marte=[1, 2])
        obj["programas"][7]["total"].update(local=[10 ** 15 + 1, 0], privada=[10 ** 15, 0], tailnet="1,2",
                                            internet={"a": 1})
        self.gravar(obj)
        r = self.ok()
        self.assertEqual([p["produto"] for p in r["programas"]], ["claude_cli", "grok_cli"])
        claude, grok = r["programas"]
        self.assertEqual(claude["conexoes"], 3)
        self.assertEqual(claude["janela"], ZERADO)
        self.assertEqual(claude["total"], cats(privada=[10 ** 15, 0]))
        self.assertEqual(grok["conexoes"], 0)
        self.assertEqual((grok["janela"], grok["total"]), (ZERADO, ZERADO))
        self.assertNotIn("marte", json.dumps(r))
        for conexoes in (-1, 1.0, True, "3", 100001, None):
            with self.subTest(conexoes=conexoes):
                obj = bom()
                obj["programas"][0]["conexoes"] = conexoes
                obj["outros"]["conexoes"] = conexoes
                self.gravar(obj)
                r = self.ok()
                self.assertEqual(r["programas"][0]["conexoes"], 0)
                self.assertEqual(r["outros"]["conexoes"], 0)
        produtos = [linha["produto"] for linha in presenca_processos.PRODUTOS]
        obj = bom()
        obj["programas"] = [{"produto": produtos[i % len(produtos)], "conexoes": i, "janela": cats(), "total": cats()}
                            for i in range(45)]
        self.gravar(obj)
        saida = [p["produto"] for p in self.ok()["programas"]]
        self.assertEqual(saida, produtos[:45])
        self.assertEqual(len(saida), len(set(saida)))
        for nome in ("outros ausente", "outros texto"):
            with self.subTest(nome=nome):
                obj = bom()
                if nome == "outros ausente":
                    del obj["outros"]
                else:
                    obj["outros"] = MARCA
                self.gravar(obj)
                self.assertEqual(self.ok()["outros"], {"conexoes": 0, "janela": ZERADO, "total": ZERADO})

    # 38
    def test_38_limites_e_formato(self):
        texto = json.dumps(bom())
        self.gravar_bytes((texto + " " * (16385 - len(texto))).encode("utf-8"))
        self.assertEqual(os.path.getsize(self.arquivo), 16385)
        self.assertEqual(self.ler(), {"erro": mod.ERRO_GRANDE})
        # B1-3: o stat do módulo da fonte informa 10 bytes; só a checagem depois da leitura pode barrar.
        stat_falso = Mock(return_value=Mock(st_size=10))
        with patch.object(mod, "os", Mock(stat=stat_falso)):
            self.assertEqual(self.ler(), {"erro": mod.ERRO_GRANDE})
        stat_falso.assert_called()
        self.gravar_bytes((texto + " " * (16384 - len(texto))).encode("utf-8"))
        self.assertEqual(self.ok()["estado"], "ativo")
        casos = {"não JSON": b"isso nao e json {", "lista": b"[1, 2]", "texto": b'"ativo"',
                 "aninhamento": b'{"x":' + b"[" * 8000 + b"]" * 8000 + b"}"}
        for nome, dados in casos.items():
            with self.subTest(nome=nome):
                self.assertLessEqual(len(dados), mod.LIMITE_BYTES)
                self.gravar_bytes(dados)
                self.assertEqual(self.ler(), {"erro": mod.ERRO_FORMATO})
        for campo, bruto in (("schema", "2"), ("schema", "true"), ("schema", "1.0"), ("schema", '"1"'),
                             ("estado", '"desconhecido"'), ("estado", "5"), ("estado", "null")):
            with self.subTest(campo=campo, bruto=bruto):
                self.com_campo(campo, bruto)
                self.assertEqual(self.ler(), {"erro": mod.ERRO_FORMATO})
        for bruto in ("true", '"texto"', "0", "-5", "NaN", "Infinity", "-Infinity", "1e999", GRANDE, "null"):
            for estado in ("ativo", "encerrado", "erro"):
                with self.subTest(gerado_em=bruto[:12], estado=estado):
                    self.com_campo("gerado_em", bruto, bom(estado))
                    self.assertEqual(self.ler(), {"erro": mod.ERRO_FORMATO})
        # F1-1: exceção de interpretar vira a mensagem fixa, sem texto da exceção.
        self.gravar(bom())
        with patch.object(mod, "interpretar", side_effect=RuntimeError("C:/SEGREDO-PASTA 10.9.8.7 pid 4242")):
            r = self.ler()
        self.assertEqual(r, {"erro": mod.ERRO_FORMATO})
        self.assertNotIn("SEGREDO", repr(r))
        self.arquivo.unlink()
        self.arquivo.mkdir()
        self.assertEqual(self.ler(), {"erro": mod.ERRO_LER})

    # 39
    def test_39_chaves_e_textos_hostis_nao_saem(self):
        self.gravar(hostil())
        r = self.ler()
        self.assertEqual(len(r["rede_uso"]["programas"]), 1)
        for texto in (json.dumps(r), json.dumps(r, ensure_ascii=False)):
            self.assertNotIn(MARCA, texto)
        self.assertFalse(set(chaves(r)) & set(PROIBIDAS))
        self.assertEqual(set(r["rede_uso"]), set(esperado()))
        self.assertEqual(set(r["rede_uso"]["programas"][0]),
                         {"produto", "rotulo", "familia", "tipo", "conexoes", "janela", "total"})
        self.assertEqual(set(r["rede_uso"]["outros"]), {"conexoes", "janela", "total"})
        obj = hostil()
        obj["estado"] = "erro"
        self.gravar(obj)
        r = self.ler()
        self.assertEqual(r, {"rede_uso": {"estado": "erro_captura", "motivo": "outro"}})

    # 40
    def test_40_so_leitura_e_so_o_resumo(self):
        self.gravar(bom())
        (self.dir / "outro.json").write_text('{"x": 1}', encoding="utf-8")

        def fotografia():
            saida = []
            for nome in sorted(os.listdir(self.dir)):
                caminho = self.dir / nome
                st = os.stat(caminho)
                saida.append((nome, st.st_size, st.st_mtime_ns, caminho.read_bytes()))
            return saida

        antes = fotografia()
        aberto = open
        with patch("builtins.open", side_effect=aberto) as espiao:
            r = mod.ler({"rede_resumo": str(self.arquivo), "agora": AGORA})
        self.assertEqual(r, {"rede_uso": esperado()})
        self.assertEqual(espiao.call_count, 1)
        argumentos = espiao.call_args.args
        self.assertEqual(Path(argumentos[0]), self.arquivo)
        self.assertEqual(argumentos[1:], ("rb",))
        self.assertEqual(espiao.call_args.kwargs, {})
        self.assertEqual(fotografia(), antes)

    # 55
    def test_55_nao_finitos_e_tipos_trocados(self):
        ruins = ("NaN", "Infinity", "-Infinity", "1e999", "true", '"texto"', GRANDE)
        for campo in ("desde", "retoma_em", "cpu_pct"):
            for bruto in ruins:
                with self.subTest(campo=campo, bruto=bruto[:12]):
                    self.com_campo(campo, bruto, bom("pausado_carga"))
                    r = self.ok()
                    chave = {"desde": "desde_epoch", "retoma_em": "retoma_em_epoch", "cpu_pct": "cpu_pct"}[campo]
                    self.assertIsNone(r[chave])
                    self.assertEqual(len(r["programas"]), 2)
                    json.dumps(r, allow_nan=False)
        for bruto in ("1.5", "true", "NaN", "-1", '"1236"'):
            with self.subTest(eventos_s=bruto):
                self.com_campo("eventos_s", bruto, bom("pausado_carga"))
                self.assertIsNone(self.ok()["eventos_s"])
        for bruto in ("NaN", "Infinity", "1.0", "true", GRANDE):
            with self.subTest(perdidos=bruto[:12]):
                self.com_campo("perdidos", bruto, bom("pausado_carga"))
                self.assertEqual(self.ok()["perdidos"], 0)
        for bruto in ("1", '"true"', "null", "NaN"):
            with self.subTest(conexoes_no_teto=bruto):
                self.com_campo("conexoes_no_teto", bruto, bom("pausado_carga"))
                self.assertIs(self.ok()["conexoes_no_teto"], False)
        self.com_campo("conexoes_no_teto", "true", bom("pausado_carga"))
        self.assertIs(self.ok()["conexoes_no_teto"], True)
        for bruto in ('{"produto": "claude_cli"}', '"claude_cli"', "5", "NaN"):
            with self.subTest(programas=bruto):
                self.com_campo("programas", bruto, bom("pausado_carga"))
                self.assertEqual(self.ok()["programas"], [])
        for bruto in ("NaN", "Infinity", "1e999", GRANDE, "-0.0"):
            with self.subTest(par=bruto[:12]):
                obj = bom("pausado_carga")
                obj["programas"][0]["janela"]["internet"] = ["__BRUTO__", 5]
                obj["outros"]["total"]["local"] = [5, "__BRUTO__"]
                self.gravar_bytes(json.dumps(obj).replace('"__BRUTO__"', bruto).encode("utf-8"))
                r = self.ok()
                self.assertEqual(r["programas"][0]["janela"]["internet"], [0, 0])
                self.assertEqual(r["outros"]["total"]["local"], [0, 0])
                json.dumps(r, allow_nan=False)


class IntegracaoRedeTests(unittest.TestCase):
    VARIAVEIS = ("PAINEL_REDE", "PAINEL_AGENTES_EXTRA", "PAINEL_ERROS", "PAINEL_VIGILANTE",
                 "PAINEL_COTA_CODEX_LOCAL", "PAINEL_COTA_GROK_BOT", "PAINEL_COTA_CURSOR", "PAINEL_PRESENCA_LINUX",
                 "PAINEL_PRESENCA", "PAINEL_GASTOS")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_rede_uso', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.gravar_real = self.coletor.gravar
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dado = {"estado": "encerrado", "gerado_em_epoch": 990.0}
        self.resposta = {"rede_uso": self.dado}
        self.chamadas = []
        self.fontes = {"rede_uso": Mock(side_effect=self.falsa)}
        for nome in ('gravar', 'montar'):
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=None))
            guarda.start()
            self.addCleanup(guarda.stop)
        ambiente = patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        self.limpar()

    def falsa(self, ctx):
        self.chamadas.append(ctx["agora"])
        return self.resposta

    def limpar(self):
        for variavel in self.VARIAVEIS:
            os.environ.pop(variavel, None)

    def ligar(self, **valores):
        self.limpar()
        os.environ.update(valores)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes=self.fontes, ctx={'agora': hora}, cache=self.cache)

    # 41
    def test_41_sem_valor_exato_1_nao_registra_nao_chama_nao_publica(self):
        base = self.coletor.passada_viva(self.args, fontes={}, ctx={'agora': 1000}, cache={})
        for valor in (None, '0', 'true', 'sim'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                self.ligar(**({} if valor is None else {'PAINEL_REDE': valor}))
                self.cache.clear()
                self.assertNotIn('rede_uso', self.coletor.extras_ativos())
                self.assertNotIn('rede_uso', self.coletor.fontes_vivas(True))
                d = self.passada(1000)
                self.assertNotIn('rede_uso', d)
                self.assertNotIn('rede_uso', d['fontes'])
                self.fontes['rede_uso'].assert_not_called()
                self.assertEqual(d, base)

    # 42
    def test_42_com_1_registra_e_publica_com_ok(self):
        self.ligar(PAINEL_REDE='1')
        self.assertEqual(self.coletor.extras_ativos(), ('rede_uso',))
        registradas = self.coletor.fontes_vivas(True)
        self.assertIn('rede_uso', registradas)
        self.assertEqual(registradas['rede_uso'].__module__, 'fontes.rede_uso')
        self.assertEqual(registradas['rede_uso'].__name__, 'ler')
        d = self.passada(1000)
        self.assertEqual(d['rede_uso'], self.dado)
        self.assertIs(d['fontes']['rede_uso']['ok'], True)
        self.assertEqual(self.chamadas, [1000])
        todas = {v: '1' for v in self.VARIAVEIS}
        self.ligar(**todas)
        self.assertEqual(self.coletor.extras_ativos()[-1], 'rede_uso')
        self.assertIn('rede_uso', self.coletor.fontes_vivas(True))

    # 43
    def test_43_cadencia_10s(self):
        self.ligar(PAINEL_REDE='1')
        for hora in (1000, 1005, 1011):
            self.passada(hora)
        self.assertIn(1000, self.chamadas)
        self.assertIn(1011, self.chamadas)
        self.assertNotIn(1005, self.chamadas)
        self.assertEqual(self.chamadas, [1000, 1011])

    # 44
    def test_44_retencao_ate_300s_e_depois_erro(self):
        self.ligar(PAINEL_REDE='1')
        d = self.passada(1000)
        self.assertEqual(d['rede_uso'], self.dado)
        self.resposta = {"erro": mod.ERRO_LER}
        for hora in (1011, 1022):
            with self.subTest(hora=hora):
                d = self.passada(hora)
                self.assertEqual(self.chamadas[-1], hora)
                self.assertEqual(d['rede_uso'], self.dado)
                self.assertIs(d['fontes']['rede_uso']['ok'], False)
                self.assertIs(d['fontes']['rede_uso']['desatualizado'], True)
        d = self.passada(1301)
        self.assertEqual(self.chamadas[-1], 1301)
        self.assertEqual(d['rede_uso'], {"estado": "erro"})
        self.assertIs(d['fontes']['rede_uso']['desatualizado'], False)

    # 45
    def test_45_passada_real_com_a_fonte_verdadeira_nao_publica_texto_do_arquivo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_saida = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_saida.cleanup)
        raiz, saida = Path(tmp.name), Path(tmp_saida.name)
        arquivo = raiz / "rede-resumo.json"
        arquivo.write_text(json.dumps(hostil()), encoding="utf-8")
        self.assertIn(MARCA.encode("utf-8"), arquivo.read_bytes())
        self.args.saida = str(saida)
        gravar = self.coletor.gravar
        gravar.side_effect = self.gravar_real
        with patch.dict(os.environ, {"PAINEL_REDE": "1", "PAINEL_IAS_DIR": str(raiz / "dados"),
                                     "LOCALAPPDATA": str(raiz / "localappdata")}):
            d = self.coletor.passada_viva(self.args, fontes={"rede_uso": mod.ler},
                                          ctx={"rede_resumo": str(arquivo), "agora": AGORA}, cache=self.cache)
        self.assertEqual(gravar.call_count, 1)
        entregue = gravar.call_args.args[0]
        self.assertIs(entregue, d)
        self.assertIs(entregue["fontes"]["rede_uso"]["ok"], True)
        self.assertEqual(entregue["rede_uso"]["estado"], "ativo")
        self.assertEqual(len(entregue["rede_uso"]["programas"]), 1)
        self.assertFalse(set(chaves(entregue["rede_uso"])) & set(PROIBIDAS))
        textos = {"objeto": json.dumps(entregue),
                  "objeto sem escape": json.dumps(entregue, ensure_ascii=False),
                  "dados.json": (saida / "dados.json").read_text(encoding="utf-8"),
                  "dados.js": (saida / "dados.js").read_text(encoding="utf-8")}
        for nome, texto in textos.items():
            with self.subTest(arquivo=nome):
                self.assertIn('"rede_uso"', texto)
                self.assertNotIn(MARCA, texto)


if __name__ == "__main__":
    unittest.main()
