"""Regressões da auditoria; sem rede, credenciais ou notificações reais."""
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.request

import sentinela
from servidor import PREFIXO, Trava


def setUpModule():
    for alvo, nome in ((subprocess, 'run'), (subprocess, 'Popen'),
                       (urllib.request, 'urlopen'), (urllib.request, 'build_opener'),
                       (sentinela.modulo_canais, 'build_opener')):
        guarda = patch.object(alvo, nome, side_effect=AssertionError('envio real proibido'))
        guarda.start()
        unittest.addModuleCleanup(guarda.stop)


class AuditoriaSentinela(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        self.arquivo = self.pasta / 'dados.js'
        self.dados = {'origem': 'vivo', 'gerado_em_epoch': 100000,
                      'agentes': [], 'servicos': [], 'fontes': {}}
        self.log = Mock()
        self.canais = {nome: Mock() for nome in ('log', 'toast', 'webhook', 'hindsight')}
        guarda = patch.dict(os.environ, {'PAINEL_IAS_DIR': str(self.pasta)})
        guarda.start()
        self.addCleanup(guarda.stop)

    def publicar(self):
        self.arquivo.write_text(PREFIXO + json.dumps(self.dados) + ');', encoding='utf-8')

    def rodar(self, extras=(), **kwargs):
        self.publicar()
        return sentinela.main(['--ensaio', '--uma-vez', '--dados', str(self.arquivo), *extras],
                              canais=self.canais, agora=lambda: 100000, **kwargs)

    def test_log_so_transicoes_e_resumo_periodico(self):
        self.dados['servicos'] = [{'nome': 'Ollama', 'estado': 'fora'}]
        estado = {}
        for agora in range(100000, 100906, 5):
            self.dados['gerado_em_epoch'] = agora
            estado = sentinela.passada(self.dados, estado, agora, self.canais, self.log, ensaio=True)
        mensagens = [c.args[0] % c.args[1:] for c in self.log.info.call_args_list]
        self.assertLessEqual(len(mensagens), 4, mensagens[:6])
        self.assertEqual(sum('resumo periódico' in m for m in mensagens), 1)
        self.assertEqual(estado['chaves']['servico:Ollama']['contagem'], 182)
        self.assertEqual(self.canais['log'].enviar.call_count, 1)
        self.canais['toast'].enviar.assert_not_called()

    def test_chave_repetida_nao_duplica_abertura_servico(self):
        self.dados['servicos'] = [{'nome': 'Ollama', 'estado': 'fora'}] * 3
        estado = sentinela.passada(self.dados, {}, 100000, self.canais, self.log)
        estado = sentinela.passada(self.dados, estado, 100005, self.canais, self.log)
        self.assertEqual(estado['chaves']['servico:Ollama']['contagem'], 2)
        self.canais['toast'].enviar.assert_called_once()
        self.canais['webhook'].enviar.assert_called_once()

    def test_chave_repetida_nao_duplica_cota_claude(self):
        self.dados['agentes'] = [{'nome': 'Claude', 'cotas': {'sessao_5h': 90}}] * 2
        sentinela.passada(self.dados, {}, 100000, self.canais, self.log)
        self.canais['webhook'].enviar.assert_called_once()

    def test_retencao_temporal_cobre_48h_sem_limite_por_volume(self):
        handlers = []
        original = sentinela.passada

        def observar(*args, **kwargs):
            handlers.extend(args[4].handlers)
            return original(*args, **kwargs)

        with patch.object(sentinela, 'passada', side_effect=observar):
            self.assertEqual(self.rodar(), 0)
        self.assertEqual(len(handlers), 1)
        handler = handlers[0]
        self.assertIsInstance(handler, TimedRotatingFileHandler)
        self.assertEqual(handler.when, 'MIDNIGHT')
        self.assertGreaterEqual(handler.backupCount * handler.interval, 3 * 86400)

    def test_trava_entre_ensaio_e_real_antes_de_construir_canais(self):
        self.publicar()
        trava = Trava(self.pasta / 'sentinela.trava')
        try:
            with patch.object(sentinela, 'construir_canais') as construir:
                for ensaio in ([], ['--ensaio']):
                    self.assertEqual(sentinela.main(['--uma-vez', '--dados', str(self.arquivo), *ensaio]), 0)
                construir.assert_not_called()
            self.assertFalse((self.pasta / 'alertas-estado.json').exists())
            self.assertFalse((self.pasta / 'alertas-ensaio-estado.json').exists())
            self.assertIn('outra sentinela', (self.pasta / 'sentinela.log').read_text(encoding='utf-8'))
        finally:
            trava.close()
        self.assertEqual(self.rodar(), 0)
        self.assertTrue((self.pasta / 'alertas-ensaio-estado.json').exists())


if __name__ == '__main__':
    unittest.main()
