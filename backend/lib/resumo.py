"""Resumo de produção em memória — a "planilha de resumo" entre o
BigQuery e as telas.

Uma única consulta agrupa a base consolidada por dia × banco × convênio ×
produto (só os últimos RESUMO_MESES meses) e o resultado fica guardado na
memória do processo. O Dashboard calcula gráfico, árvore, projeção e lista
de meses a partir dele — trocar de filtro não vai mais ao BigQuery.

Quando ele é recarregado:
- na subida do processo (warmup);
- logo depois de qualquer gravação (importação, exclusão de log,
  cruzamento de dados…), via invalidar_tudo() → em segundo plano;
- quando passa de RESUMO_TTL_MIN minutos (devolve o atual e recarrega em
  segundo plano), para pegar o que for gravado direto no BigQuery.

Depois de uma gravação o resumo antigo NÃO é mais servido: quem pedir
antes da recarga terminar espera por ela (mesma regra do cache: nunca
mostrar dado desatualizado depois de uma mudança).

Se a recarga falhar ou o período pedido for anterior à janela do resumo,
quem chama cai de volta nas consultas diretas ao BigQuery
(lib/dashboard.py) — o resumo só acelera, nunca é o único caminho.
"""
import logging
import os
import threading
import time
from datetime import date

import pandas as pd
from google.cloud import bigquery

from lib.bigquery_client import get_bigquery_client
from lib.cache import registrar_ao_invalidar

log = logging.getLogger("resumo")

PROJECT = os.environ.get("BIGQUERY_PROJECT_ID")
DATASET = os.environ.get("BIGQUERY_DATASET", "base_bancos")
TABELA_PRINCIPAL = os.environ.get("BIGQUERY_TABLE", "base_consolidada")
DATE_COLUMN = os.environ.get("BIGQUERY_DATE_COLUMN", "data_pagamento")

RESUMO_MESES = int(os.environ.get("RESUMO_MESES", "36"))
RESUMO_TTL_MIN = float(os.environ.get("RESUMO_TTL_MIN", "30"))
# trava de segurança para o plano de 512 MB: acima disso o resumo não é
# guardado e as telas seguem consultando o BigQuery direto
RESUMO_MAX_LINHAS = int(os.environ.get("RESUMO_MAX_LINHAS", "1500000"))

SEM_BANCO = "(sem banco)"
SEM_CONVENIO = "(sem convênio)"
SEM_PRODUTO = "(sem produto)"


class Resumo:
    def __init__(self, df, primeiro_mes, geracao):
        self.df = df
        self.primeiro_mes = primeiro_mes
        self.geracao = geracao
        self.carregado_em = time.time()

    # ── cobertura ────────────────────────────────────────────────────────
    def cobre(self, meses):
        return all(m >= self.primeiro_mes for m in meses)

    # ── consultas ────────────────────────────────────────────────────────
    def _filtrar(self, banco, meses, produto=None, nome_convenio=None):
        df = self.df
        mascara = df["mes"].isin(meses)
        if banco:
            mascara &= df["banco_chave"] == banco.upper()
        if produto:
            mascara &= df["produto_filtro"] == produto
        if nome_convenio:
            mascara &= df["convenio_busca"].str.contains(nome_convenio.lower(), regex=False, na=False)
        return df[mascara]

    def meses(self):
        return sorted(self.df["mes"].dropna().unique().tolist(), reverse=True)

    def total(self, banco, meses):
        return float(self._filtrar(banco, meses)["total"].sum())

    def por_dia(self, banco, meses):
        serie = self._filtrar(banco, meses).groupby("dia", observed=True)["total"].sum().sort_index()
        return [{"dia": dia, "total": float(total)} for dia, total in serie.items()]

    def hierarquico(self, banco, meses, produto=None, nome_convenio=None):
        filtrado = self._filtrar(banco, meses, produto=produto, nome_convenio=nome_convenio)
        serie = filtrado.groupby(
            ["banco_exibicao", "convenio_exibicao", "produto_exibicao"], observed=True
        )["total"].sum()
        return [(b, c, p, float(v)) for (b, c, p), v in serie.items()]


RESUMO_ESPERA_FALHA_MIN = 5

_estado = {"resumo": None, "geracao": 0, "sem_resumo": None}
_lock_estado = threading.Lock()
_lock_carga = threading.Lock()


def _primeiro_mes_janela():
    hoje = date.today()
    total = hoje.year * 12 + (hoje.month - 1) - (RESUMO_MESES - 1)
    return date(total // 12, total % 12 + 1, 1)


def _consultar(primeiro_dia):
    from lib.manutencao import garantir_colunas_map

    garantir_colunas_map()

    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_PRINCIPAL}`"
    query = f"""
        SELECT
          FORMAT_DATE('%Y-%m-%d', {DATE_COLUMN}) AS dia,
          banco,
          convenio,
          map_convenio,
          produto,
          map_produto,
          SUM(vlr_liquido) AS total
        FROM {tabela}
        WHERE {DATE_COLUMN} >= @inicio
        GROUP BY dia, banco, convenio, map_convenio, produto, map_produto
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("inicio", "DATE", primeiro_dia)]
    )
    return client.query(query, job_config=job_config).result().to_dataframe(create_bqstorage_client=False)


def montar_dataframe(bruto):
    """Converte o resultado agrupado do BigQuery nas colunas que as
    consultas usam — mesmas regras de IFNULL das consultas originais."""
    df = pd.DataFrame(index=bruto.index)
    df["dia"] = bruto["dia"].astype("string")
    df["mes"] = df["dia"].str.slice(0, 7)
    df["banco_chave"] = bruto["banco"].astype("string").str.upper()
    df["banco_exibicao"] = bruto["banco"].astype("string").fillna(SEM_BANCO)
    convenio_tratado = bruto["map_convenio"].astype("string").fillna(bruto["convenio"].astype("string"))
    df["convenio_exibicao"] = convenio_tratado.fillna(SEM_CONVENIO)
    df["convenio_busca"] = bruto["convenio"].astype("string").fillna("").str.lower()
    produto_tratado = bruto["map_produto"].astype("string").fillna(bruto["produto"].astype("string"))
    df["produto_filtro"] = produto_tratado
    df["produto_exibicao"] = produto_tratado.fillna(SEM_PRODUTO)
    df["total"] = pd.to_numeric(bruto["total"], errors="coerce").fillna(0.0).astype("float64")

    df = df[df["dia"].notna()]
    for col in ["dia", "mes", "banco_chave", "banco_exibicao", "convenio_exibicao",
                "convenio_busca", "produto_filtro", "produto_exibicao"]:
        df[col] = df[col].astype("category")
    return df.reset_index(drop=True)


def _carregar(geracao):
    primeiro_dia = _primeiro_mes_janela()
    inicio = time.time()
    bruto = _consultar(primeiro_dia)
    if len(bruto) > RESUMO_MAX_LINHAS:
        log.warning("RESUMO ignorado: %s linhas (limite %s) — telas seguem no BigQuery direto",
                    len(bruto), RESUMO_MAX_LINHAS)
        return None
    resumo = Resumo(montar_dataframe(bruto), primeiro_dia.strftime("%Y-%m"), geracao)
    log.info("RESUMO carregado: %s linhas desde %s em %.1fs",
             len(resumo.df), resumo.primeiro_mes, time.time() - inicio)
    return resumo


def _recarregar():
    """Recarrega até o resumo bater com a geração atual (uma gravação que
    chegue no meio da carga dispara mais uma volta)."""
    with _lock_carga:
        while True:
            with _lock_estado:
                geracao = _estado["geracao"]
                atual = _estado["resumo"]
            if atual is not None and atual.geracao == geracao and not _vencido(atual):
                return atual
            try:
                resumo = _carregar(geracao)
            except Exception:
                with _lock_estado:
                    _estado["sem_resumo"] = (geracao, time.time())
                raise
            with _lock_estado:
                _estado["resumo"] = resumo
                _estado["sem_resumo"] = (geracao, time.time()) if resumo is None else None
                if resumo is None or _estado["geracao"] == geracao:
                    return resumo


def _vencido(resumo):
    return (time.time() - resumo.carregado_em) > RESUMO_TTL_MIN * 60


def _recarregar_em_segundo_plano():
    def alvo():
        try:
            _recarregar()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao recarregar o resumo em segundo plano")

    threading.Thread(target=alvo, daemon=True, name="resumo-recarga").start()


def obter():
    """Devolve o resumo pronto para uso, ou None se não der para montá-lo
    (aí quem chama consulta o BigQuery direto)."""
    with _lock_estado:
        resumo = _estado["resumo"]
        geracao = _estado["geracao"]
        sem_resumo = _estado["sem_resumo"]

    # a última tentativa falhou (ou passou do limite de linhas) para esta
    # mesma geração: não tenta de novo a cada requisição — só depois de
    # uma gravação ou de RESUMO_ESPERA_FALHA_MIN minutos
    if sem_resumo and sem_resumo[0] == geracao and (time.time() - sem_resumo[1]) < RESUMO_ESPERA_FALHA_MIN * 60:
        return None

    if resumo is not None and resumo.geracao == geracao:
        if _vencido(resumo) and not _lock_carga.locked():
            _recarregar_em_segundo_plano()
        return resumo

    try:
        return _recarregar()
    except Exception:  # noqa: BLE001
        log.exception("Falha ao carregar o resumo — usando consultas diretas ao BigQuery")
        return None


def marcar_desatualizado():
    with _lock_estado:
        _estado["geracao"] += 1
    _recarregar_em_segundo_plano()


registrar_ao_invalidar(marcar_desatualizado)
