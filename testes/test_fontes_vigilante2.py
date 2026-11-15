"""Fonte do Vigilante 2: leitura de ultimo.json, lista fixa, retrato e registro no coletor.

Só arquivos sintéticos em diretório temporário; nenhum teste lê %LOCALAPPDATA%\\vigilante2 real nem a rede.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from lib.fontes import vigilante2 as mod

AGORA = 2_000_000_000
HOSTIL = "<img src=x onerror=alert(1)>"


def iso(epoch, formato="z"):
    quando = datetime.fromtimestamp(epoch, timezone.utc)
    if formato == "z":
        return quando.isoformat().replace("+00:00", "Z")
    if formato == "naive":
        return quando.replace(tzinfo=None).isoformat()
    return quando.isoformat()


def bom(segundos_atras=40):
    return {
        "schema": 1,
        "gerado_em": iso(AGORA - segundos_atras, "mais00"),
        "ultimo_resumo_em": iso(AGORA - 3600),
        "wire_ok": True,
        "painel_ok": False,
        "online": 8,
        "trabalhando": 2,
        "atencao": 1,
        "workspaces": 5,
        "terminais": 21,
        "falhas_seguidas": 0,
        "por_tipo": {"claude_code": 9, "codex": 4, "custom": 5, "generic_shell": 3, "outra": 0},
        "alertas_ativos": [{"tipo": "login", "desde": iso(AGORA - 600)},
                           {"tipo": "cota_baixa", "desde": iso(AGORA - 7200)}],
    }


class VigilanteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.arq = self.dir / "ultimo.json"

    def gravar(self, objeto):
        self.arq.write_text(json.dumps(objeto), encoding="utf-8")

    def gravar_bytes(self, dados):
        self.arq.write_bytes(dados)

    def ler(self):
        return mod.ler({"vigilante_arquivo": str(self.arq), "agora": AGORA})

    def erro(self, esperado=mod.ERRO_FORMATO):
        self.assertEqual(self.ler(), {"erro": esperado})

    def ok(self):
        r = self.ler()
        self.assertEqual(list(r), ["vigilante2"], r)
        return r["vigilante2"]

    # 1
    def test_01_ausente_desligado_e_sem_localappdata(self):
        self.assertEqual(self.ler(), {"vigilante2": {"estado": "desligado"}})
        self.assertIsNone(mod.caminho_padrao({}))
        self.assertIsNone(mod.caminho_padrao({"LOCALAPPDATA": ""}))
        with patch.dict(os.environ):
            os.environ.pop("LOCALAPPDATA", None)
            self.assertEqual(mod.ler({"agora": AGORA}), {"vigilante2": {"estado": "desligado"}})

    # 2
    def test_02_bom_e_recente(self):
        self.gravar(bom(40))
        r = self.ok()
        self.assertEqual(r, {
            "estado": "ok", "gerado_em_epoch": float(AGORA - 40), "wire_ok": True, "painel_ok": False,
            "online": 8, "trabalhando": 2, "atencao": 1, "workspaces": 5, "terminais": 21,
            "falhas_seguidas": 0,
            "por_tipo": {"claude_code": 9, "codex": 4, "custom": 5, "generic_shell": 3, "outra": 0},
            "alertas": [{"tipo": "login", "desde_epoch": float(AGORA - 600)},
                        {"tipo": "cota_baixa", "desde_epoch": float(AGORA - 7200)}]})

    # 3
    def test_03_parado_a_partir_de_181s(self):
        self.gravar(bom(181))
        r = self.ok()
        self.assertEqual(r["estado"], "parado")
        self.assertEqual(r["online"], 8)
        self.assertEqual(r["terminais"], 21)
        self.assertEqual(len(r["alertas"]), 2)
        self.gravar(bom(180))
        self.assertEqual(self.ok()["estado"], "ok")

    # 4
    def test_04_futuro(self):
        self.gravar(bom(-300))
        self.assertEqual(self.ok()["estado"], "ok")
        self.gravar(bom(-301))
        self.erro()

    # 5
    def test_05_json_invalido(self):
        casos = {"aleatorio": bytes(range(128, 256)) + b"\x00\xff", "vazio": b"", "lista": b"[]",
                 "texto": b'"abc"', "numero": b"42"}
        for nome, dados in casos.items():
            with self.subTest(nome):
                self.gravar_bytes(dados)
                self.erro()

    # 6
    def test_06_grande_demais(self):
        self.gravar_bytes(b" " * 16385)
        self.erro(mod.ERRO_GRANDE)
        corpo = json.dumps(bom(40)).encode("utf-8")
        self.assertLess(len(corpo), 16384)
        self.gravar_bytes(corpo + b" " * (16384 - len(corpo)))
        self.assertEqual(self.arq.stat().st_size, 16384)
        self.assertEqual(self.ok()["estado"], "ok")

    # 7
    def test_07_schema_errado(self):
        for nome, valor in (("dois", 2), ("texto", "1"), ("true", True), ("ausente", None)):
            with self.subTest(nome):
                obj = bom()
                if valor is None:
                    del obj["schema"]
                else:
                    obj["schema"] = valor
                self.gravar(obj)
                self.erro()

    # 8
    def test_08_tipos_de_alerta(self):
        obj = bom()
        longo = "x" * 500
        obj["alertas_ativos"] = [
            {"tipo": "tipo_novo", "desde": iso(AGORA - 100)},
            {"tipo": HOSTIL, "desde": iso(AGORA - 200)},
            {"tipo": longo, "desde": iso(AGORA - 50)},
            "texto", 7, None, {"desde": iso(AGORA - 5)}, {"tipo": 3, "desde": iso(AGORA - 5)},
            {"tipo": "wire_fora", "desde": iso(AGORA - 30)},
            {"tipo": "wire_fora", "desde": iso(AGORA - 900)},
            {"tipo": "wire_fora", "desde": "lixo"},
            {"tipo": "login", "desde": "lixo"},
            {"tipo": "fim_etapa", "desde": iso(AGORA + 301)},
            {"tipo": "terminal_caiu"},
        ]
        self.gravar(obj)
        r = self.ok()
        saida = json.dumps(r)
        for texto in (HOSTIL, longo, "tipo_novo", "lixo"):
            self.assertNotIn(texto, saida)
        self.assertEqual(r["alertas"], [
            {"tipo": "fim_etapa", "desde_epoch": None},
            {"tipo": "login", "desde_epoch": None},
            {"tipo": "terminal_caiu", "desde_epoch": None},
            {"tipo": "wire_fora", "desde_epoch": float(AGORA - 900)},
            {"tipo": "outro", "desde_epoch": float(AGORA - 200)},
        ])
        ordem = [a["tipo"] for a in r["alertas"]]
        self.assertEqual(ordem, sorted(ordem, key=mod.TIPOS_ALERTA.index))
        obj["alertas_ativos"] = [{"tipo": "login", "desde": iso(AGORA + 300)}]
        self.gravar(obj)
        self.assertEqual(self.ok()["alertas"], [{"tipo": "login", "desde_epoch": float(AGORA + 300)}])

    # 9
    def test_09_contagens_invalidas(self):
        for chave in mod.CONTAGENS:
            for nome, valor in (("negativa", -1), ("true", True), ("float", 1.5), ("texto", "3"),
                                ("acima", mod.MAX_CONTAGEM + 1), ("ausente", None)):
                with self.subTest(chave=chave, caso=nome):
                    obj = bom()
                    if valor is None:
                        del obj[chave]
                    else:
                        obj[chave] = valor
                    self.gravar(obj)
                    self.erro()

    # 10
    def test_10_booleanos_invalidos(self):
        for chave in ("wire_ok", "painel_ok"):
            for nome, valor in (("um", 1), ("texto", "true"), ("nulo", "NULO"), ("ausente", None)):
                with self.subTest(chave=chave, caso=nome):
                    obj = bom()
                    if nome == "ausente":
                        del obj[chave]
                    elif nome == "nulo":
                        obj[chave] = None
                    else:
                        obj[chave] = valor
                    self.gravar(obj)
                    self.erro()

    # 11
    def test_11_por_tipo(self):
        obj = bom()
        obj["por_tipo"] = {"claude_code": "x", "codex": -3, "novo": 4, "outro_novo": 6, "invalido": "a"}
        self.gravar(obj)
        r = self.ok()
        self.assertEqual(r["por_tipo"], {"claude_code": 0, "codex": 0, "custom": 0, "generic_shell": 0,
                                         "outra": 10})
        self.assertEqual(set(r["por_tipo"]), set(mod.TIPOS_TERMINAL))
        obj["por_tipo"] = {"outra": 3, "novo": 4}
        self.gravar(obj)
        self.assertEqual(self.ok()["por_tipo"]["outra"], 7)
        for nome in ("ausente", "lista", "texto"):
            with self.subTest(nome):
                obj = bom()
                if nome == "ausente":
                    del obj["por_tipo"]
                else:
                    obj["por_tipo"] = [] if nome == "lista" else "abc"
                self.gravar(obj)
                self.erro()

    # 12
    def test_12_gerado_em_e_alertas(self):
        alvo = AGORA - 40
        for nome, texto in (("fuso", iso(alvo - 0, "mais00")), ("z", iso(alvo, "z")),
                            ("sem_fuso", iso(alvo, "naive"))):
            with self.subTest(nome):
                obj = bom()
                obj["gerado_em"] = texto
                self.gravar(obj)
                self.assertEqual(self.ok()["gerado_em_epoch"], float(alvo))
        obj = bom()
        obj["gerado_em"] = datetime.fromtimestamp(alvo, timezone.utc).astimezone(
            timezone(timedelta(hours=-3))).isoformat()
        self.assertTrue(obj["gerado_em"].endswith("-03:00"))
        self.gravar(obj)
        self.assertEqual(self.ok()["gerado_em_epoch"], float(alvo))
        for nome in ("ausente", "numero", "texto", "nulo"):
            with self.subTest(nome):
                obj = bom()
                if nome == "ausente":
                    del obj["gerado_em"]
                else:
                    obj["gerado_em"] = {"numero": AGORA, "texto": "ontem", "nulo": None}[nome]
                self.gravar(obj)
                self.erro()
        for nome in ("ausente", "objeto", "texto"):
            with self.subTest(alertas=nome):
                obj = bom()
                if nome == "ausente":
                    del obj["alertas_ativos"]
                else:
                    obj["alertas_ativos"] = {} if nome == "objeto" else "abc"
                self.gravar(obj)
                self.erro()

    # 13
    def test_13_bom_utf8(self):
        corpo = json.dumps(bom()).encode("utf-8")
        self.gravar_bytes(bytes([0xEF, 0xBB, 0xBF]) + corpo)
        self.assertEqual(self.ok()["estado"], "ok")

    # 14
    def test_14_erro_de_leitura(self):
        r = mod.ler({"vigilante_arquivo": str(self.dir), "agora": AGORA})
        self.assertEqual(r, {"erro": mod.ERRO_LER})

    # 15
    def test_15_so_o_arquivo_certo_e_nenhum_texto_sai(self):
        (self.dir / "estado.json").write_text("segredo-sintetico-estado", encoding="utf-8")
        (self.dir / "vigilante2.log").write_text("segredo-sintetico-log", encoding="utf-8")
        obj = bom()
        obj["extra"] = "segredo-sintetico-extra"
        obj["alertas_ativos"][0]["rotulo"] = "segredo-sintetico-alerta"
        self.gravar(obj)
        r = self.ok()
        saida = json.dumps(r)
        self.assertNotIn("segredo-sintetico", saida)
        self.assertNotIn("ultimo_resumo_em", saida)
        self.assertEqual(set(r), {"estado", "gerado_em_epoch", "wire_ok", "painel_ok", "online", "trabalhando",
                                  "atencao", "workspaces", "terminais", "falhas_seguidas", "por_tipo",
                                  "alertas"})

    # 16
    def test_16_nao_grava(self):
        (self.dir / "estado.json").write_text("x", encoding="utf-8")
        self.gravar(bom())

        def listagem():
            out = []
            for nome in sorted(os.listdir(self.dir)):
                st = os.stat(self.dir / nome)
                out.append((nome, st.st_size, st.st_mtime_ns))
            return out

        antes = listagem()
        self.ler()
        self.assertEqual(listagem(), antes)


class IntegracaoVigilanteTests(unittest.TestCase):
    VARIAVEIS = ("PAINEL_VIGILANTE", "PAINEL_COTA_CODEX_LOCAL", "PAINEL_COTA_GROK_BOT", "PAINEL_COTA_CURSOR",
                 "PAINEL_PRESENCA_LINUX", "PAINEL_PRESENCA", "PAINEL_GASTOS")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_vigilante2', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dado = {'estado': 'ok', 'gerado_em_epoch': 1.0}
        self.fontes = {'vigilante2': Mock(return_value={'vigilante2': self.dado})}
        for nome in ('gravar', 'montar'):
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=None))
            guarda.start()
            self.addCleanup(guarda.stop)
        ambiente = patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        self.limpar()

    def limpar(self):
        for variavel in self.VARIAVEIS:
            os.environ.pop(variavel, None)

    def ligar(self, **valores):
        self.limpar()
        os.environ.update(valores)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes=self.fontes,
                                        ctx={'agora': hora}, cache=self.cache)

    # 17
    def test_17_desligado_nao_registra_nao_chama_nao_publica(self):
        for valor in (None, '0', 'true'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                self.ligar(**({} if valor is None else {'PAINEL_VIGILANTE': valor}))
                self.cache.clear()
                self.assertNotIn('vigilante2', self.coletor.fontes_vivas(True))
                self.assertNotIn('vigilante2', self.coletor.extras_ativos())
                d = self.passada(1000)
                self.assertNotIn('vigilante2', d)
                self.assertNotIn('vigilante2', d['fontes'])
                self.fontes['vigilante2'].assert_not_called()

    # 18
    def test_18_ligado_registra_publica_e_respeita_cadencia(self):
        self.ligar(PAINEL_VIGILANTE='1')
        registradas = self.coletor.fontes_vivas(True)
        self.assertIn('vigilante2', registradas)
        self.assertEqual(registradas['vigilante2'].__module__, 'fontes.vigilante2')
        d = self.passada(1000)
        self.assertEqual(d['vigilante2'], self.dado)
        self.assertIs(d['fontes']['vigilante2']['ok'], True)
        fonte = self.fontes['vigilante2']
        self.assertEqual(fonte.call_count, 1)
        self.passada(1020)
        self.assertEqual(fonte.call_count, 1)
        self.passada(1031)
        self.assertEqual(fonte.call_count, 2)

    # 19
    def test_19_falha_retem_ate_300s_e_depois_vira_erro(self):
        self.ligar(PAINEL_VIGILANTE='1')
        self.passada(1000)
        self.fontes['vigilante2'].return_value = {'erro': mod.ERRO_LER}
        for hora in (1120, 1240):
            d = self.passada(hora)
            self.assertEqual(d['vigilante2'], self.dado)
            self.assertIs(d['fontes']['vigilante2']['desatualizado'], True)
        d = self.passada(1301)
        self.assertEqual(d['vigilante2'], {'estado': 'erro'})
        self.assertIs(d['fontes']['vigilante2']['desatualizado'], False)

    # 20
    def test_20_independencia_e_ordem(self):
        self.ligar(PAINEL_VIGILANTE='1')
        self.assertEqual(self.coletor.extras_ativos(), ('vigilante2',))
        todas = {v: '1' for v in self.VARIAVEIS}
        self.ligar(**todas)
        esperado = ('whatsapp', 'gastos_hermes', 'presenca_processos', 'presenca_linux',
                    'cota_codex_local', 'cota_grok_bot', 'cota_cursor', 'vigilante2')
        extras = self.coletor.extras_ativos()
        self.assertEqual(extras, esperado)
        self.assertEqual(extras[-1], 'vigilante2')
        self.assertIn('vigilante2', self.coletor.fontes_vivas(True))


if __name__ == "__main__":
    unittest.main()
