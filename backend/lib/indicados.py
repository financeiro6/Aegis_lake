import os
import threading
import uuid
from datetime import datetime

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from lib.bigquery_client import get_bigquery_client, erro_e_de_billing
from lib.cache import cached, invalidar_tudo

PROJECT = os.environ.get("BIGQUERY_PROJECT_ID")
DATASET = os.environ.get("BIGQUERY_DATASET", "base_bancos")
TABELA_INDICADOS = os.environ.get("BIGQUERY_LOOKUP_TABLE", "cod_indicados")

_tabela_garantida = False
_tabela_lock = threading.Lock()

SCHEMA_INDICADOS = [
    bigquery.SchemaField("id", "STRING"),
    bigquery.SchemaField("criado_em", "TIMESTAMP"),
    bigquery.SchemaField("banco", "STRING"),
    bigquery.SchemaField("cod_loja", "STRING"),
    bigquery.SchemaField("nome", "STRING"),
    bigquery.SchemaField("usuario", "STRING"),
]


def garantir_tabela_indicados():
    """Confere/cria o dataset e a tabela — só de verdade UMA VEZ por
    processo, pra não repetir essas chamadas de setup em toda consulta."""
    global _tabela_garantida
    if _tabela_garantida:
        return

    with _tabela_lock:
        if _tabela_garantida:
            return

        client = get_bigquery_client()
        client.create_dataset(f"{PROJECT}.{DATASET}", exists_ok=True)
        table_id = f"{PROJECT}.{DATASET}.{TABELA_INDICADOS}"
        table = bigquery.Table(table_id, schema=SCHEMA_INDICADOS)
        client.create_table(table, exists_ok=True)

        _tabela_garantida = True


def adicionar_indicado(banco, cod_loja, nome, usuario):
    garantir_tabela_indicados()
    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{TABELA_INDICADOS}"

    linha = pd.DataFrame([{
        "id": uuid.uuid4().hex,
        "criado_em": datetime.utcnow(),
        "banco": banco.strip().upper(),
        "cod_loja": (cod_loja or "").strip() or None,
        "nome": (nome or "").strip() or None,
        "usuario": usuario.strip(),
    }])

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(linha, table_id, job_config=job_config)
    job.result()

    invalidar_tudo()


@cached()
def listar_indicados(busca=None, limite=200):
    garantir_tabela_indicados()
    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_INDICADOS}`"

    query = f"""
        SELECT id, criado_em, banco, cod_loja, nome, usuario
        FROM {tabela}
        WHERE (@busca IS NULL
               OR LOWER(banco) LIKE CONCAT('%', LOWER(@busca), '%')
               OR LOWER(IFNULL(cod_loja, '')) LIKE CONCAT('%', LOWER(@busca), '%')
               OR LOWER(IFNULL(nome, '')) LIKE CONCAT('%', LOWER(@busca), '%')
               OR LOWER(usuario) LIKE CONCAT('%', LOWER(@busca), '%'))
        ORDER BY criado_em DESC
        LIMIT @limite
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("busca", "STRING", busca or None),
            bigquery.ScalarQueryParameter("limite", "INT64", limite),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return [dict(row) for row in rows]


def excluir_indicado(id_):
    client = get_bigquery_client()
    tabela_id = f"{PROJECT}.{DATASET}.{TABELA_INDICADOS}"
    tabela = f"`{tabela_id}`"

    try:
        query = f"DELETE FROM {tabela} WHERE id = @id"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", id_)]
        )
        client.query(query, job_config=job_config).result()

    except Exception as exc:  # noqa: BLE001
        if not erro_e_de_billing(exc):
            raise

        # fallback sem DML (projeto sem billing habilitado)
        rebuild_query = f"""
            CREATE OR REPLACE TABLE `{tabela_id}` AS
            SELECT * FROM {tabela}
            WHERE id != @id
        """
        job_config_rebuild = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", id_)]
        )
        client.query(rebuild_query, job_config=job_config_rebuild).result()

    invalidar_tudo()


def editar_indicado(id_, nome, usuario):
    """Edita um indicado já existente — só Nome e Usuário podem mudar.
    Cód. Loja é a chave usada no cruzamento de dados (Manutenção) e em
    qualquer importação já feita com base nele, então não é editável
    por aqui (trocar o código quebraria esse vínculo silenciosamente).
    Mesmo padrão upsert do resto do sistema: apaga a linha antiga e
    grava uma nova com os mesmos id/criado_em/banco/cod_loja."""
    garantir_tabela_indicados()
    indicados = listar_indicados()
    alvo = next((i for i in indicados if i["id"] == id_), None)
    if not alvo:
        raise ValueError("Indicado não encontrado.")

    novo_nome = (nome or "").strip() or None
    novo_usuario = (usuario or "").strip()
    if not novo_usuario:
        raise ValueError("Informe o usuário.")
    if not alvo.get("cod_loja") and not novo_nome:
        raise ValueError("Preencha Cód. Loja ou Nome.")

    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{TABELA_INDICADOS}"

    excluir_indicado(id_)

    linha = pd.DataFrame([{
        "id": alvo["id"],
        "criado_em": alvo.get("criado_em") or datetime.utcnow(),
        "banco": alvo["banco"],
        "cod_loja": alvo.get("cod_loja"),
        "nome": novo_nome,
        "usuario": novo_usuario,
    }])

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(linha, table_id, job_config=job_config)
    job.result()

    invalidar_tudo()


def carregar_todos_indicados():
    """Usado pelo importador (lib/importador.py) para fazer o lookup
    cod_indicado durante uma importação de produção."""
    garantir_tabela_indicados()
    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_INDICADOS}`"
    try:
        return client.query(f"SELECT * FROM {tabela}").to_dataframe(create_bqstorage_client=False)
    except NotFound:
        return pd.DataFrame()
