from datetime import datetime
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.request
import sentinela
import servidor
from lib import canais


def setUpModule():
    for alvo, nome in ((subprocess, 'run'), (subprocess, 'Popen'), (urllib.request, 'urlopen'),
                       (urllib.request, 'build_opener'), (canais, 'build_opener')):
        guarda = patch.object(alvo, nome, side_effect=AssertionError('envio real proibido'))
        guarda.start()
        unittest.addModuleCleanup(guarda.stop)


class SentinelaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        self.dados = self.pasta / 'dados.js'
        self.d = {'origem': 'vivo', 'gerado_em_epoch': 100000, 'agentes': [],
                  'servicos': [{'nome': 'Ollama', 'estado': 'fora'}], 'fontes': {}}
        self.dados.write_text(servidor.PREFIXO + json.dumps(self.d) + ');', encoding='utf-8')
        guarda = patch.dict(os.environ, {'PAINEL_IAS_DIR': str(self.pasta)})
        guarda.start()
        self.addCleanup(guarda.stop)
        self.logger = logging.getLogger('teste.sentinela')

    def test_ensaio_uma_vez_nem_constroi_canais_reais(self):
        with patch.object(canais, 'Toast', side_effect=AssertionError), \
             patch.object(canais, 'Webhook', side_effect=AssertionError), \
             patch.object(canais, 'Hindsight', side_effect=AssertionError):
            self.assertEqual(sentinela.main(['--ensaio', '--uma-vez', '--dados', str(self.dados)], agora=lambda: 100000), 0)
        log = (self.pasta / 'sentinela.log').read_text(encoding='utf-8')
        self.assertIn('contagem servico:Ollama=1 aberto=False', log)
        self.assertFalse((self.pasta / 'alertas-estado.json').exists())
        self.assertFalse((self.pasta / 'alertas-pendentes.jsonl').exists())
        self.assertTrue((self.pasta / 'alertas-ensaio-estado.json').exists())

    def test_falha_canal_nao_impede_outro(self):
        self.d['agentes'] = [{'nome': 'Agente', 'estado': 'parado'}]
        toast = Mock()
        toast.enviar.side_effect = RuntimeError
        webhook, hindsight, log = Mock(), Mock(), Mock()
        sentinela.passada(self.d, {}, 100000, {'log': log, 'toast': toast,
                           'webhook': webhook, 'hindsight': hindsight}, self.logger)
        webhook.enviar.assert_called_once()
        hindsight.enviar.assert_called_once()

    def test_ensaio_nao_envia_mesmo_com_canais_injetados(self):
        self.d['agentes'] = [{'nome': 'Agente', 'estado': 'parado'}]
        objetos = {n: Mock() for n in ('log', 'toast', 'webhook', 'hindsight')}
        sentinela.passada(self.d, {}, 100000, objetos, self.logger, ensaio=True)
        objetos['log'].enviar.assert_called_once()
        for n in ('toast', 'webhook', 'hindsight'):
            objetos[n].enviar.assert_not_called()
        objetos['webhook'].reenviar.assert_not_called()

    def test_lembrete_nao_e_transicao_hindsight(self):
        self.d['servicos'] = []
        self.d['fontes'] = {'Wire': {'ok': False}}
        objetos = {n: Mock() for n in ('log', 'toast', 'webhook', 'hindsight')}
        estado = {}
        for agora in (100000, 100005, 100010, 101810):
            self.d['gerado_em_epoch'] = agora
            estado = sentinela.passada(self.d, estado, agora, objetos, self.logger)
        self.assertEqual(objetos['toast'].enviar.call_count, 2)
        objetos['hindsight'].enviar.assert_called_once()

    def test_estado_persistido_dedupe_apos_reinicio(self):
        self.d['agentes'] = [{'nome': 'Agente', 'estado': 'parado'}]
        self.dados.write_text(servidor.PREFIXO + json.dumps(self.d) + ');', encoding='utf-8')
        objetos = {n: Mock() for n in ('log', 'toast', 'webhook', 'hindsight')}
        args = ['--uma-vez', '--dados', str(self.dados)]
        self.assertEqual(sentinela.main(args, canais=objetos, agora=lambda: 100000), 0)
        self.assertEqual(sentinela.main(args, canais=objetos, agora=lambda: 100005), 0)
        self.assertEqual(objetos['toast'].enviar.call_count, 2)  # parado + Ollama na segunda avaliação
        estado = json.loads((self.pasta / 'alertas-estado.json').read_text())
        self.assertEqual(estado['chaves']['parado:Agente']['ultimo_aviso'], 100000)

    def test_resumo_diario_uma_vez_sem_texto_de_tela(self):
        hoje = datetime(2026, 1, 1, 23, 55).astimezone().timestamp()
        d = dict(self.d, gerado_em_epoch=hoje, servicos=[], agentes=[{
            'nome': 'Agente', 'estado': 'ocioso', 'atividade': 'privado', 'preview': 'privado',
            'cotas': {'sessao_5h': 90, 'semanal': 40}}])
        objetos = {n: Mock() for n in ('log', 'toast', 'webhook', 'hindsight')}
        estado = sentinela.passada(d, {}, hoje, objetos, self.logger)
        resumos = [c.args[0] for c in objetos['hindsight'].enviar.call_args_list if c.args[0]['tipo'] == 'resumo_diario']
        self.assertEqual(len(resumos), 1)
        self.assertNotIn('privado', str(resumos))
        self.assertEqual(resumos[0]['resumo']['agentes_vistos'], ['Agente'])
        self.assertEqual(resumos[0]['resumo']['cotas_maximas']['Agente']['sessao_5h'], 90)
        sentinela.passada(d, estado, hoje + 5, objetos, self.logger)
        self.assertEqual(sum(c.args[0]['tipo'] == 'resumo_diario' for c in objetos['hindsight'].enviar.call_args_list), 1)

    def test_dados_invalidos_nao_disparam_alerta(self):
        self.dados.write_text('invalido', encoding='utf-8')
        log = Mock()
        self.assertEqual(sentinela.main(['--uma-vez', '--ensaio', '--dados', str(self.dados)], canais={'log': log}), 1)
        log.enviar.assert_not_called()

    def test_optin_servidor_segundo_filho(self):
        for ligado, quantidade in (('0', 1), ('1', 2)):
            with self.subTest(ligado=ligado), patch.dict(os.environ, {'SENTINELA': ligado}), \
                 patch.object(servidor, 'Supervisor') as supervisor, \
                 patch.object(servidor, 'PainelHTTP') as http, patch.object(servidor.signal, 'signal'):
                http.return_value.server_port = 7421
                http.return_value.serve_forever.side_effect = KeyboardInterrupt
                self.assertEqual(servidor.main(['--com-coletor']), 0)
                self.assertEqual(supervisor.call_count, quantidade)
                if ligado == '1':
                    self.assertEqual(supervisor.call_args.kwargs['filho'], 'sentinela.py')

    def test_comando_sentinela_sem_flag_vivo(self):
        s = servidor.Supervisor(filho='sentinela.py', job_factory=lambda: None)
        self.assertEqual(Path(s.comando[2]).name, 'sentinela.py')
        self.assertNotIn('--vivo', s.comando)
