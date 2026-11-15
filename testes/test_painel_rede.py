# -*- coding: utf-8 -*-
"""Comando painel-rede (E22, §2; §7, itens 25 a 32 e 52 a 54).

O comando é carregado por SourceFileLoader e chamado por main(argv, ...) com colaboradores falsos e
PAINEL_IAS_DIR numa pasta temporária dentro de patch.dict (menos o item 32, que roda `painel-rede estado`
como processo filho). Nenhum teste abre sessão ETW, pede elevação, cria mutex ou evento de verdade ou roda
a captura de verdade; os únicos objetos de sistema criados são as junções do item 52, dentro da pasta
temporária e removidas no fim.
"""
import contextlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

RAIZ = Path(__file__).resolve().parent.parent
T0 = 1_793_433_712.0
TETO_DORMIR = 200
MARCA = "MARCA-ESTADO-E22"
ESC = "\x1b[31m"
SINAIS = tuple(getattr(signal, nome) for nome in ("SIGINT", "SIGBREAK") if hasattr(signal, nome))
MSG_DIRETORIO = ("painel-rede: o diretório de dados não existe ou não é um diretório comum; "
                 "nada foi gravado\n")


def carregar():
    loader = SourceFileLoader("painel_rede_comando", str(RAIZ / "painel-rede"))
    modulo = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(modulo)
    return modulo


def falsos():
    g = Mock()
    g.sessao.abrir.return_value = None
    g.sessao.limpar_orfa.return_value = True
    g.sessao.perdidos.return_value = 0
    g.mutex.adquirir.return_value = True
    g.mutex.existe.return_value = False
    g.evento.criar.return_value = True
    g.evento.sinalizado.return_value = False
    g.evento.pedir.return_value = False
    return g


class Relogio:
    def __init__(self):
        self.t = 0.0
        self.dormidas = []
        self.ao_dormir = None
        self.estourou = False

    def relogio(self):
        return self.t

    def agora(self):
        return T0 + self.t

    def cpu(self):
        return 0.0

    def dormir(self, segundos):
        # B1-1: teto. Depois de TETO_DORMIR chamadas o laço está rodando sem parar: o teste falha, não trava.
        if len(self.dormidas) >= TETO_DORMIR:
            self.estourou = True
            raise AssertionError("dormir chamado mais de %d vezes: o laço não parou" % TETO_DORMIR)
        self.dormidas.append(segundos)
        self.t += segundos
        if self.ao_dormir is not None:
            self.ao_dormir(len(self.dormidas))


def resumo(gerado, estado="ativo"):
    return {"schema": 1, "gerado_em": gerado, "estado": estado, "motivo": None, "desde": gerado - 3600.0,
            "janela_s": 60, "cpu_pct": 0.4, "eventos_s": 1236, "perdidos": 7, "retoma_em": None,
            "conexoes_no_teto": False,
            "programas": [{"produto": "claude_cli", "conexoes": 3, "janela": {"internet": [1500, 28000]},
                           "total": {"internet": [1500, 28000]}},
                          {"produto": "grok_cli", "conexoes": 1, "janela": {}, "total": {}}],
            "outros": {"conexoes": 42, "janela": {}, "total": {}}}


def fotografia(pasta):
    return sorted((p.name, p.read_bytes() if p.is_file() else None) for p in Path(pasta).iterdir())


class PainelRedeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cmd = carregar()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.dir = self.raiz / "dados"
        self.dir.mkdir()
        self.arquivo = self.dir / "rede-resumo.json"
        ambiente = patch.dict(os.environ, {"PAINEL_IAS_DIR": str(self.dir)})
        ambiente.start()
        self.addCleanup(ambiente.stop)
        self.g = falsos()
        self.rel = Relogio()
        self.pasta = Mock(caminho=str(self.dir))
        self.prender = Mock(return_value=self.pasta)
        self.elevado = Mock(return_value=True)

    def rodar(self, *argv, **trocas):
        col = dict(elevado=self.elevado, sessao=self.g.sessao, mutex=self.g.mutex, evento=self.g.evento,
                   prender=self.prender, listar=lambda predicado: [], relogio=self.rel.relogio, cpu=self.rel.cpu,
                   dormir=self.rel.dormir, agora=self.rel.agora, plataforma="nt", ambiente={})
        col.update(trocas)
        saida, erro = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erro):
            codigo = self.cmd.main(list(argv), **col)
        # main devolve 1 ("falha inesperada") ao engolir o AssertionError do teto: o sinalizador não se perde.
        self.assertFalse(self.rel.estourou, "teto de %d chamadas ao dormir falso estourado" % TETO_DORMIR)
        return codigo, saida.getvalue(), erro.getvalue()

    # 25
    def test_25_capturar_fora_do_windows(self):
        vivos = len(self.cmd._VIVOS)
        codigo, out, err = self.rodar("capturar", plataforma="posix")
        self.assertEqual((codigo, out, err), (1, "", "painel-rede: só funciona no Windows\n"))
        self.elevado.assert_not_called()
        self.prender.assert_not_called()
        self.assertEqual(self.g.mock_calls, [])
        self.assertEqual(len(self.cmd._VIVOS), vivos)

    # 26
    def test_26_capturar_sem_elevacao_nao_grava_nem_cria_nada(self):
        self.arquivo.write_bytes(b'{"schema": 1, "estado": "ativo"}\n')
        (self.dir / "outro.txt").write_text("x", encoding="utf-8")
        antes = fotografia(self.dir)
        vivos = len(self.cmd._VIVOS)
        for valor in (False, 0):
            with self.subTest(elevado=valor):
                self.elevado.return_value = valor
                codigo, out, err = self.rodar("capturar")
                self.assertEqual((codigo, out), (1, ""))
                self.assertEqual(err, "painel-rede: a captura precisa de um terminal de administrador; "
                                      "nada foi iniciado\n")
                self.assertEqual(fotografia(self.dir), antes)
                self.prender.assert_not_called()
                self.assertEqual(self.g.mock_calls, [])
                self.assertEqual(len(self.cmd._VIVOS), vivos)

    # 27
    def test_27_mutex_ou_evento_ocupados(self):
        self.arquivo.write_bytes(b'{"resumo": "anterior"}\n')
        antes = fotografia(self.dir)
        self.g.mutex.adquirir.return_value = False
        codigo, out, err = self.rodar("capturar")
        self.assertEqual((codigo, out, err), (3, "", "painel-rede: já existe uma captura rodando\n"))
        self.prender.assert_called_once_with(str(self.dir))
        self.assertEqual(fotografia(self.dir), antes)
        self.assertEqual(self.g.sessao.mock_calls, [])
        self.assertEqual(self.g.evento.mock_calls, [])
        self.pasta.fechar.assert_called()

        self.g = falsos()
        self.pasta.fechar.reset_mock()
        self.g.evento.criar.return_value = False
        codigo, out, err = self.rodar("capturar")
        self.assertEqual((codigo, out), (4, ""))
        self.assertEqual(err, "painel-rede: o nome do evento de parada já está em uso; nada foi iniciado\n")
        self.g.mutex.liberar.assert_called_once_with()
        self.assertEqual(self.g.sessao.mock_calls, [])
        self.assertEqual(fotografia(self.dir), antes)
        self.pasta.fechar.assert_called()

    # 28
    def test_28_estado_so_mostra_o_que_a_fonte_validou(self):
        self.assertEqual(self.rodar("estado"), (0, "sem captura\n", ""))
        self.arquivo.write_text(json.dumps(resumo(T0 - 5.0)), encoding="utf-8")
        codigo, out, err = self.rodar("estado")
        self.assertEqual((codigo, err), (0, ""))
        self.assertEqual(out.splitlines(), ["estado: ativo", "idade_s: 5", "cpu_pct: 0.4", "eventos_s: 1236",
                                            "perdidos: 7", "programas: 2", "conexoes: 46"])
        self.arquivo.write_text(json.dumps(resumo(T0 - 31.0)), encoding="utf-8")
        self.assertEqual(self.rodar("estado"), (0, "estado: parado\nsem_sinal_s: 31\n", ""))
        self.arquivo.write_text(json.dumps(resumo(T0 - 5.0, "pausado_carga")), encoding="utf-8")
        codigo, out, err = self.rodar("estado")
        self.assertEqual((codigo, err), (0, ""))
        self.assertEqual(out.splitlines(), ["estado: pausado_carga", "idade_s: 5", "cpu_pct: 0.4",
                                            "eventos_s: 1236", "perdidos: 7", "programas: 2", "conexoes: 46"])
        com_motivo = resumo(T0 - 5.0, "erro")
        com_motivo["motivo"] = "provedor"
        self.arquivo.write_text(json.dumps(com_motivo), encoding="utf-8")
        self.assertEqual(self.rodar("estado"), (0, "estado: erro_captura\nmotivo: provedor\n", ""))

        texto = json.dumps(resumo(T0 - 5.0))
        nan = resumo(T0 - 5.0)
        nan["gerado_em"] = float("nan")
        for nome, dados in (("não JSON", b"isso nao e json {"),
                            ("maior que 16 KiB", (texto + " " * (16385 - len(texto))).encode("utf-8")),
                            ("gerado_em NaN", json.dumps(nan).encode("utf-8"))):
            with self.subTest(nome=nome):
                self.arquivo.write_bytes(dados)
                self.assertEqual(self.rodar("estado"), (1, "", "painel-rede: resumo ilegível\n"))

        hostil = resumo(T0 - 5.0)
        hostil.update(nome=MARCA + ESC, caminho=MARCA + ESC, extra={"x": MARCA + ESC})
        hostil["programas"][0].update(rotulo=MARCA + ESC, nome=MARCA + ESC)
        hostil["programas"].append({"produto": MARCA + ESC, "conexoes": 5})
        self.arquivo.write_text(json.dumps(hostil), encoding="utf-8")
        self.assertIn(b"\\u001b", self.arquivo.read_bytes())
        codigo, out, err = self.rodar("estado")
        self.assertEqual((codigo, err), (0, ""))
        self.assertIn("programas: 2", out.splitlines())
        hostil.update(estado="erro", motivo=MARCA + ESC)
        self.arquivo.write_text(json.dumps(hostil), encoding="utf-8")
        codigo2, out2, err2 = self.rodar("estado")
        self.assertEqual((codigo2, out2, err2), (0, "estado: erro_captura\nmotivo: outro\n", ""))
        for saida in (out + err, out2 + err2):
            self.assertNotIn(MARCA, saida)
            self.assertNotIn("\x1b", saida)

        # F1-2: o motivo fora da lista vira "outro" também dentro do comando, mesmo que a fonte o entregue cru.
        cmd_ru = self.cmd.rede_uso
        with patch.object(cmd_ru, "ler", return_value={cmd_ru.CHAVE: {"estado": "erro_captura",
                                                                     "motivo": MARCA + ESC}}):
            self.assertEqual(self.rodar("estado"), (0, "estado: erro_captura\nmotivo: outro\n", ""))
        # F1-3: horário no futuro nunca vira número negativo.
        self.arquivo.write_text(json.dumps(resumo(T0 + 100.0)), encoding="utf-8")
        codigo, out, err = self.rodar("estado")
        self.assertEqual((codigo, err), (0, ""))
        self.assertEqual(out.splitlines()[:2], ["estado: ativo", "idade_s: 0"])
        with patch.object(cmd_ru, "ler", return_value={cmd_ru.CHAVE: {"estado": "parado",
                                                                     "ultimo_sinal_epoch": T0 + 50.0}}):
            self.assertEqual(self.rodar("estado"), (0, "estado: parado\nsem_sinal_s: 0\n", ""))

    # 29
    def test_29_parar_sem_elevacao_e_sem_captura(self):
        self.assertEqual(self.rodar("parar", plataforma="posix"),
                         (1, "", "painel-rede: só funciona no Windows\n"))
        self.elevado.assert_not_called()
        self.assertEqual(self.g.mock_calls, [])
        self.elevado.return_value = False
        self.assertEqual(self.rodar("parar"),
                         (1, "", "painel-rede: parar precisa de um terminal de administrador\n"))
        self.assertEqual(self.g.mock_calls, [])
        self.elevado.return_value = True
        self.assertEqual(self.rodar("parar"), (0, "painel-rede: nenhuma captura rodando\n", ""))
        self.g.evento.pedir.assert_called_once_with()
        self.g.sessao.limpar_orfa.assert_called_once_with()
        self.assertEqual([c[0] for c in self.g.mock_calls
                          if c[0] in ("mutex.adquirir", "sessao.limpar_orfa", "mutex.liberar")],
                         ["mutex.adquirir", "sessao.limpar_orfa", "mutex.liberar"])
        self.assertEqual(self.rel.dormidas, [])

    # 30
    def test_30_parar_com_captura_rodando(self):
        casos = (("sai com 1 s", True, 1.0), ("sai aos 19 s", True, 19.0), ("não sai em 20 s", True, None),
                 ("nome ocupado por outro", False, None))
        for nome, pediu, sai_em in casos:
            with self.subTest(nome):
                self.g, self.rel = falsos(), Relogio()
                self.g.evento.pedir.return_value = pediu
                self.g.mutex.existe.side_effect = (
                    (lambda: True) if sai_em is None else (lambda rel=self.rel, limite=sai_em: rel.t < limite))
                codigo, out, err = self.rodar("parar")
                self.g.evento.pedir.assert_called_once_with()
                self.assertTrue(self.rel.dormidas)
                self.assertEqual(set(self.rel.dormidas), {0.25})
                if sai_em is not None:
                    self.assertEqual((codigo, out, err), (0, "painel-rede: captura encerrada\n", ""))
                    self.g.sessao.limpar_orfa.assert_called_once_with()
                    self.assertEqual(self.rel.t, sai_em)
                else:
                    self.assertEqual((codigo, out, err),
                                     (1, "", "painel-rede: a captura não respondeu; nada foi encerrado\n"))
                    self.assertEqual(self.g.sessao.mock_calls, [])
                    self.assertGreaterEqual(self.rel.t, 20.0)
                    self.assertLess(self.rel.t, 20.5)
        with self.subTest("mutex não adquirido: a sessão não é tocada"):
            self.g, self.rel = falsos(), Relogio()
            self.g.mutex.adquirir.return_value = False
            codigo, out, err = self.rodar("parar")
            self.assertEqual((codigo, err), (0, ""))
            self.g.mutex.adquirir.assert_called_once_with()
            self.g.sessao.limpar_orfa.assert_not_called()
            self.assertEqual(self.g.sessao.mock_calls, [])
        with self.subTest("limpar_orfa levanta: o mutex é liberado mesmo assim"):
            self.g, self.rel = falsos(), Relogio()
            self.g.sessao.limpar_orfa.side_effect = RuntimeError("falha")
            codigo, out, err = self.rodar("parar")
            self.assertEqual((codigo, out, err), (1, "", "painel-rede: falha inesperada\n"))
            self.g.sessao.limpar_orfa.assert_called_once_with()
            self.g.mutex.liberar.assert_called_once_with()

    # 31
    def test_31_argumentos(self):
        for argv in (["xyz"], ["estado", "sobra"], ["capturar", "-h"], ["parar", "--forca"], ["--desconhecido"],
                     []):
            with self.subTest(argv=argv):
                codigo, out, err = self.rodar(*argv)
                self.assertEqual((codigo, out), (2, ""))
                self.assertTrue(err.startswith("painel-rede: argumentos inválidos ("), err)
                self.assertTrue(err.endswith(". Veja painel-rede --help.\n"), err)
                self.assertNotIn("Traceback", err)
        self.elevado.assert_not_called()
        self.prender.assert_not_called()
        self.assertEqual(self.g.mock_calls, [])
        for opcao in ("--help", "-h", "--ajuda"):
            with self.subTest(opcao=opcao):
                codigo, out, err = self.rodar(opcao)
                self.assertEqual((codigo, err), (0, ""))
                for uso in ("painel-rede capturar", "painel-rede estado", "painel-rede parar"):
                    self.assertIn(uso, out)

    # 32
    def test_32_estado_em_processo_filho_sem_elevacao(self):
        with tempfile.TemporaryDirectory() as pasta:
            ambiente = dict(os.environ, PAINEL_IAS_DIR=pasta)
            ambiente.pop("PYTHONPATH", None)
            p = subprocess.run([sys.executable, "-B", str(RAIZ / "painel-rede"), "estado"], env=ambiente,
                               cwd=pasta, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(os.listdir(pasta), [])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.decode("utf-8").splitlines(), ["sem captura"])
        self.assertEqual(p.stderr, b"")

    # 52
    @unittest.skipUnless(os.name == "nt", "junção só existe no Windows")
    def test_52_diretorio_inseguro_com_prender_de_verdade(self):
        import _winapi
        alvo = self.raiz / "alvo"
        alvo.mkdir()
        juncao = self.raiz / "juncao"
        _winapi.CreateJunction(str(alvo), str(juncao))
        self.addCleanup(os.rmdir, str(juncao))
        self.assertTrue(os.lstat(juncao).st_file_attributes & 0x400)
        inexistente = self.raiz / "nao-existe"
        vivos = len(self.cmd._VIVOS)
        for nome, caminho in (("junção", juncao), ("inexistente", inexistente)):
            with self.subTest(nome), patch.dict(os.environ, {"PAINEL_IAS_DIR": str(caminho)}):
                self.g = falsos()
                codigo, out, err = self.rodar("capturar", prender=None)
                self.assertEqual((codigo, out, err), (1, "", MSG_DIRETORIO))
                self.assertEqual(self.g.mock_calls, [])
                self.assertEqual(os.listdir(alvo), [])
                self.assertEqual(len(self.cmd._VIVOS), vivos)
        self.assertFalse(inexistente.exists())
        os.rename(alvo, self.raiz / "alvo-renomeado")
        os.rename(self.raiz / "alvo-renomeado", alvo)

    # 53
    def test_53_sinais_so_ligam_o_sinalizador_e_a_sessao_fica_viva(self):
        anteriores = {numero: signal.getsignal(numero) for numero in SINAIS}
        durante = []

        def ao_dormir(n):
            durante.append({numero: signal.getsignal(numero) for numero in SINAIS})
            if n == 2:
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)

        self.rel.ao_dormir = ao_dormir
        sessao_falsa = self.g.sessao
        # B1-2: espia rede_captura.capturar (o nome como o painel-rede o enxerga) e compara os 11 argumentos
        # posicionais, por identidade, com os colaboradores injetados. Métodos ligados são guardados antes
        # para que a identidade valha.
        listar = lambda predicado: []
        relogio, cpu, dormir, agora = self.rel.relogio, self.rel.cpu, self.rel.dormir, self.rel.agora
        real = self.cmd.rede_captura.capturar
        antes_do_sinal = []

        def espiar(*args, **kwargs):
            antes_do_sinal.append(args[4]() if len(args) > 4 and callable(args[4]) else "ausente")
            return real(*args, **kwargs)

        with patch.object(self.cmd.rede_captura, "capturar", side_effect=espiar) as espia:
            codigo, out, err = self.rodar("capturar", listar=listar, relogio=relogio, cpu=cpu, dormir=dormir,
                                          agora=agora)
        self.assertEqual(espia.call_count, 1)
        args, kwargs = espia.call_args
        self.assertEqual(kwargs, {})
        self.assertEqual(len(args), 11)
        self.assertIs(args[0], self.pasta)
        self.assertIs(args[1], sessao_falsa)
        self.assertIs(args[2], listar)
        self.assertEqual(args[3], self.cmd.presenca_processos.tabela_resolvida({}))
        self.assertEqual(antes_do_sinal, [False])
        self.assertIs(args[5], relogio)
        self.assertIs(args[6], cpu)
        self.assertIs(args[7], dormir)
        self.assertIs(args[8], agora)
        self.assertIs(args[9], self.g.mutex)
        self.assertIs(args[10], self.g.evento)
        self.assertEqual((codigo, out, err), (0, "painel-rede: captura encerrada\n", ""))
        self.assertEqual(self.rel.dormidas, [2.0, 2.0])
        self.assertEqual(len(durante), 2)
        for vistos in durante:
            for numero, tratador in vistos.items():
                self.assertTrue(callable(tratador))
                self.assertIsNot(tratador, anteriores[numero])
                self.assertNotIn(tratador, (signal.SIG_DFL, signal.SIG_IGN, signal.default_int_handler))
        for numero in SINAIS:
            self.assertEqual(signal.getsignal(numero), anteriores[numero])
        self.assertEqual([c[0] for c in sessao_falsa.mock_calls if c[0] in ("limpar_orfa", "abrir", "fechar")],
                         ["limpar_orfa", "abrir", "fechar", "limpar_orfa"])
        self.assertEqual(json.loads(self.arquivo.read_text(encoding="utf-8"))["estado"], "encerrado")
        self.pasta.fechar.assert_called()
        self.assertTrue(any(x is sessao_falsa for x in self.cmd._VIVOS))

    # 54
    def test_54_falhas_inesperadas_sem_rastro_nem_marca(self):
        self.elevado.side_effect = RuntimeError("MARCA-ERRO-E22")
        for sub in ("capturar", "parar"):
            with self.subTest(sub=sub):
                self.assertEqual(self.rodar(sub), (1, "", "painel-rede: falha inesperada\n"))
        self.elevado.side_effect = None
        marcado = str(self.dir / "MARCA-CAMINHO-E22")
        self.prender.side_effect = OSError(2, "MARCA-ERRO-E22", marcado)
        codigo, out, err = self.rodar("capturar")
        self.assertEqual((codigo, out, err), (1, "", "painel-rede: falha de arquivo ou do sistema\n"))
        self.assertEqual(self.g.mock_calls, [])
        self.prender.side_effect = KeyboardInterrupt()
        codigo2, out2, err2 = self.rodar("capturar")
        self.assertEqual((codigo2, out2, err2), (1, "", "painel-rede: interrompido\n"))
        for texto in (err, err2):
            self.assertNotIn("Traceback", texto)
            self.assertNotIn("MARCA", texto)
        self.assertFalse((self.dir / "MARCA-CAMINHO-E22").exists())


if __name__ == "__main__":
    unittest.main()
