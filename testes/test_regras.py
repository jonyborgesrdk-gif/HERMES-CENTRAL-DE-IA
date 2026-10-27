import copy
import unittest
from lib.regras import avaliar


class RegrasTests(unittest.TestCase):
    def setUp(self):
        self.estado = {}
        self.a = {'nome': 'Agente', 'estado': 'ocioso', 'atividade': 'teste',
                  'cotas': {'sessao_5h': 10, 'semanal': 20}}
        self.d = {'origem': 'vivo', 'agentes': [self.a], 'servicos': [], 'fontes': {}}

    def passo(self, agora):
        self.d['gerado_em_epoch'] = agora
        a, f, self.estado = avaliar(self.d, self.estado, agora)
        return a, f

    def test_espera_mais_de_sessenta(self):
        self.a['estado'] = 'esperando'
        self.assertEqual(self.passo(0)[0], [])
        self.assertEqual(self.passo(60)[0], [])
        a = self.passo(61)[0]
        self.assertEqual(a[0]['chave'], 'atencao:Agente')
        self.assertEqual(a[0]['canais'], ['toast', 'webhook'])

    def test_atividade_estavel_quase_e_mais_quinze_minutos(self):
        self.a['estado'] = 'trabalhando'
        self.passo(0)
        self.assertEqual(self.passo(900)[0], [])
        self.assertEqual(self.passo(901)[0][0]['chave'], 'travado:Agente')

    def test_mudanca_atividade_reinicia_e_fecha(self):
        self.a['estado'] = 'trabalhando'
        self.passo(0)
        self.passo(901)
        self.a['atividade'] = 'progresso novo'
        a, f = self.passo(902)
        self.assertEqual(a, [])
        self.assertEqual(f[0]['tipo'], 'resolvido')
        self.assertEqual(self.estado['chaves']['travado:Agente']['desde'], 902)

    def test_atividade_ausente_nao_inventa_travamento(self):
        self.a.update(estado='trabalhando', atividade=None)
        self.passo(0)
        self.assertEqual(self.passo(1000)[0], [])

    def test_parado_uma_vez_por_ocorrencia(self):
        self.a['estado'] = 'parado'
        self.assertEqual(self.passo(0)[0][0]['chave'], 'parado:Agente')
        self.assertEqual(self.passo(4000)[0], [])

    def test_cota_histerese_e_reabertura(self):
        self.a['cotas']['sessao_5h'] = 85
        self.assertEqual(self.passo(0)[0][0]['chave'], 'cota:Agente:5h')
        self.a['cotas']['sessao_5h'] = 75
        self.assertEqual(self.passo(1900), ([], []))
        self.a['cotas']['sessao_5h'] = 70
        self.assertEqual(self.passo(2000), ([], []))
        self.a['cotas']['sessao_5h'] = 69
        self.assertEqual(self.passo(2001)[1][0]['tipo'], 'resolvido')
        self.a['cotas']['sessao_5h'] = 90
        self.assertEqual(self.passo(2002)[0][0]['chave'], 'cota:Agente:5h')

    def test_cota_uma_vez_por_janela(self):
        self.a['cotas']['semanal'] = 90
        self.assertEqual(self.passo(0)[0][0]['canais'], ['webhook'])
        self.assertEqual(self.passo(4000)[0], [])

    def test_cota_ausente_nao_prova_renovacao(self):
        self.a['cotas']['semanal'] = 90
        self.passo(0)
        self.a['cotas']['semanal'] = None
        self.assertEqual(self.passo(4000), ([], []))

    def test_servico_duas_avaliacoes_e_recuperacao(self):
        self.d['servicos'] = [{'nome': 'Exemplo', 'estado': 'fora'}]
        self.assertEqual(self.passo(0)[0], [])
        self.assertEqual(self.passo(5)[0][0]['chave'], 'servico:Exemplo')
        self.d['servicos'][0]['estado'] = 'ok'
        self.assertEqual(self.passo(10)[1][0]['tipo'], 'resolvido')
        self.assertEqual(self.passo(15)[1], [])

    def test_servico_contagem_tem_que_ser_consecutiva(self):
        self.d['servicos'] = [{'nome': 'Exemplo', 'estado': 'fora'}]
        self.passo(0)
        self.d['servicos'][0]['estado'] = 'ok'
        self.passo(5)
        self.d['servicos'][0]['estado'] = 'fora'
        self.assertEqual(self.passo(10)[0], [])

    def test_fonte_tres_avaliacoes(self):
        self.d['fontes'] = {'Wire': {'ok': False, 'erro': 'sintetico'}}
        self.passo(0)
        self.assertEqual(self.passo(5)[0], [])
        self.assertEqual(self.passo(10)[0][0]['canais'], ['toast'])

    def test_dedupe_trinta_minutos(self):
        self.d['fontes'] = {'Wire': {'ok': False}}
        self.passo(0)
        self.passo(5)
        self.passo(10)
        self.assertEqual(self.passo(1809)[0], [])
        alerta = self.passo(1810)[0][0]
        self.assertEqual(alerta['chave'], 'fonte:Wire')
        self.assertEqual(alerta['tipo'], 'lembrete')

    def test_resolvido_uma_vez_e_dedupe_entre_ocorrencias(self):
        self.a['estado'] = 'parado'
        self.passo(0)
        self.a['estado'] = 'ocioso'
        self.assertEqual(len(self.passo(5)[1]), 1)
        self.assertEqual(self.passo(10)[1], [])
        self.a['estado'] = 'parado'
        self.assertEqual(self.passo(15)[0], [])
        self.assertEqual(len(self.passo(1800)[0]), 1)

    def test_agente_desaparecido_fecha(self):
        self.a['estado'] = 'parado'
        self.passo(0)
        self.d['agentes'] = []
        self.assertEqual(self.passo(5)[1][0]['chave'], 'parado:Agente')

    def test_dados_velhos_limite(self):
        self.d['gerado_em_epoch'] = 0
        a, _, s = avaliar(self.d, {}, 60)
        self.assertEqual(a, [])
        a, _, _ = avaliar(self.d, s, 61)
        self.assertEqual(a[0]['chave'], 'dados-velhos')

    def test_pureza_e_sem_texto_de_tela_no_estado(self):
        self.a['estado'] = 'trabalhando'
        self.a['atividade'] = 'sk-' + 'a' * 40 + ' texto' * 100
        antes = copy.deepcopy(self.d)
        _, _, estado = avaliar(self.d, {}, 0)
        alertas, _, _ = avaliar(self.d, estado, 901)
        self.assertEqual(self.d, antes)
        self.assertNotIn('sk-' + 'a' * 40, alertas[0]['texto'])
        self.assertNotIn(self.a['atividade'], str(estado))
        self.assertLessEqual(len(alertas[0]['texto']), 240)
