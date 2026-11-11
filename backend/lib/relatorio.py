import calendar
import os

from google.cloud import bigquery

from lib.bigquery_client import get_bigquery_client

PROJECT = os.environ.get("BIGQUERY_PROJECT_ID")
DATASET = os.environ.get("BIGQUERY_DATASET", "base_bancos")
TABELA_PRINCIPAL = os.environ.get("BIGQUERY_TABLE", "base_consolidada")


def limites_do_intervalo(mes_inicio, mes_fim):
    """Converte um intervalo De/Até em meses (ex: '2026-05' até '2026-07')
    nas datas exatas de início e fim (primeiro e último dia do intervalo)."""
    ano_i, m_i = (int(p) for p in mes_inicio.split("-"))
    ano_f, m_f = (int(p) for p in mes_fim.split("-"))

    if (ano_i, m_i) > (ano_f, m_f):
        ano_i, m_i, ano_f, m_f = ano_f, m_f, ano_i, m_i

    data_inicio = f"{ano_i:04d}-{m_i:02d}-01"
    ultimo_dia = calendar.monthrange(ano_f, m_f)[1]
    data_fim = f"{ano_f:04d}-{m_f:02d}-{ultimo_dia:02d}"
    return data_inicio, data_fim


COLUNAS_RELATORIO = [
    "data_pagamento",
    "banco",
    "ade",
    "convenio",
    "produto",
    "vlr_liquido",
    "vlr_bruto",
    "prazo",
    "cod_tabela",
    "tabela",
    "data_digitacao",
    "usuario",
    "cod_corretor",
    "cod_master",
    "cod_indicado",
    "arquivo_origem",
]

COLUNAS_DATA = ["data_pagamento", "data_digitacao"]


def _query_params(banco, data_inicio, data_fim, cod_master, cod_indicado):
    return [
        bigquery.ScalarQueryParameter("banco", "STRING", banco.upper() if banco else None),
        bigquery.ScalarQueryParameter("data_inicio", "DATE", data_inicio or None),
        bigquery.ScalarQueryParameter("data_fim", "DATE", data_fim or None),
        bigquery.ScalarQueryParameter("cod_master", "STRING", cod_master or None),
        bigquery.ScalarQueryParameter("cod_indicado", "STRING", cod_indicado or None),
    ]


def _where_sql():
    return """
        WHERE (@banco IS NULL OR UPPER(banco) = @banco)
          AND (@data_inicio IS NULL OR DATE(data_pagamento) >= @data_inicio)
          AND (@data_fim IS NULL OR DATE(data_pagamento) <= @data_fim)
          AND (@cod_master IS NULL OR cod_master = @cod_master)
          AND (@cod_indicado IS NULL OR cod_indicado = @cod_indicado)
    """


def contar_relatorio(banco, data_inicio, data_fim, cod_master, cod_indicado):
    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_PRINCIPAL}`"
    query = f"SELECT COUNT(*) AS total FROM {tabela} {_where_sql()}"

    job_config = bigquery.QueryJobConfig(
        query_parameters=_query_params(banco, data_inicio, data_fim, cod_master, cod_indicado)
    )
    rows = list(client.query(query, job_config=job_config).result())
    return int(rows[0]["total"] or 0) if rows else 0


def gerar_relatorio_df(banco, data_inicio, data_fim, cod_master, cod_indicado):
    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_PRINCIPAL}`"
    colunas = ", ".join(COLUNAS_RELATORIO)

    query = f"""
        SELECT {colunas}
        FROM {tabela}
        {_where_sql()}
        ORDER BY data_pagamento DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=_query_params(banco, data_inicio, data_fim, cod_master, cod_indicado)
    )
    return client.query(query, job_config=job_config).result().to_dataframe(create_bqstorage_client=False)
