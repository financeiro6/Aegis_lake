import os
import re
import threading
import unicodedata
from datetime import datetime

import pandas as pd
from google.cloud import bigquery

from lib.bancos_config import CONFIG_BANCOS
from lib.bigquery_client import get_bigquery_client, erro_e_de_billing
from lib.cache import cached, invalidar_tudo

PROJECT = os.environ.get("BIGQUERY_PROJECT_ID")
DATASET = os.environ.get("BIGQUERY_DATASET", "base_bancos")
TABELA_MAPEAMENTO = os.environ.get("BIGQUERY_MAPEAMENTO_TABLE", "config_mapeamento")

_tabela_garantida = False
_tabela_lock = threading.Lock()
# Gravações na tabela de mapeamento em fila: com o gunicorn em threads,
# dois salvamentos ao mesmo tempo poderiam se atropelar no
# CREATE OR REPLACE TABLE (sem billing) e um apagar o que o outro gravou.
_escrita_lock = threading.Lock()

SCHEMA_MAPEAMENTO = [
    bigquery.SchemaField("banco_tipo", "STRING"),
    bigquery.SchemaField("banco_nome", "STRING"),
    bigquery.SchemaField("config_nome", "STRING"),
    bigquery.SchemaField("campo_destino", "STRING"),
    bigquery.SchemaField("campo_origem", "STRING"),
    bigquery.SchemaField("atualizado_em", "TIMESTAMP"),
]

# Campos que fazem sentido ter um "de-para" (vêm de uma coluna do arquivo).
# "banco" fica de fora porque é um dado (o nome real do banco, repetível —
# um banco pode ter mais de uma configuração de importação); quem
# identifica de forma única cada configuração é "config_nome".
CAMPOS_MAPEAVEIS = [
    "data_pagamento",
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
]

# ─────────────────────────────────────────────────────────────────────────
# Regras de negócio (compartilhadas com a validação de importação em
# lib/importador.py, pra nunca ficarem dessincronizadas):
#   - ade e data_pagamento: sempre obrigatórios
#   - vlr_liquido / vlr_bruto: pelo menos um dos dois precisa estar mapeado
#   - cod_corretor / cod_master: pelo menos um dos dois precisa estar mapeado
# ─────────────────────────────────────────────────────────────────────────
CAMPOS_SEMPRE_OBRIGATORIOS = ["ade", "data_pagamento"]
GRUPOS_ALTERNATIVOS_OBRIGATORIOS = [
    ("vlr_liquido", "vlr_bruto"),
    ("cod_corretor", "cod_master"),
]


def validar_linha_completa(campos):
    """campos: {campo_destino: valor_da_coluna_de_origem_ou_vazio}.
    Retorna uma lista de mensagens de erro (vazia se estiver tudo certo)."""
    erros = []
    for campo in CAMPOS_SEMPRE_OBRIGATORIOS:
        if not (campos.get(campo) or "").strip():
            erros.append(f'O campo obrigatório "{campo}" precisa de uma coluna de origem.')

    for campo_a, campo_b in GRUPOS_ALTERNATIVOS_OBRIGATORIOS:
        if not (campos.get(campo_a) or "").strip() and not (campos.get(campo_b) or "").strip():
            erros.append(f'Preencha ao menos um entre "{campo_a}" e "{campo_b}".')

    return erros


def gerar_banco_tipo(config_nome):
    """Gera um identificador técnico (banco_tipo) a partir do nome da
    CONFIGURAÇÃO (não do banco — um banco pode ter mais de uma config),
    no mesmo padrão dos configs existentes
    (ex: 'Facta - Cartão' -> 'config_facta_cartao')."""
    texto = unicodedata.normalize("NFKD", config_nome).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "_", texto).strip("_").lower()
    return f"config_{texto}" if texto else "config_nova"


def garantir_tabela_mapeamento():
    """Confere/cria o dataset, a tabela e migra o schema se preciso — mas
    só de verdade UMA VEZ por processo (guardado em _tabela_garantida).
    Antes disso rodava em toda chamada (inclusive em cada cache MISS),
    então qualquer lentidão/erro nessa checagem repetia infinitamente."""
    global _tabela_garantida
    if _tabela_garantida:
        return

    with _tabela_lock:
        if _tabela_garantida:
            return

        client = get_bigquery_client()
        client.create_dataset(f"{PROJECT}.{DATASET}", exists_ok=True)
        table_id = f"{PROJECT}.{DATASET}.{TABELA_MAPEAMENTO}"
        table = bigquery.Table(table_id, schema=SCHEMA_MAPEAMENTO)
        client.create_table(table, exists_ok=True)
        _migrar_schema_se_necessario(client, table_id)

        _tabela_garantida = True


def _migrar_schema_se_necessario(client, table_id):
    """Se a tabela já existia de antes de 'config_nome' ser introduzida,
    adiciona a coluna (ALTER TABLE — DDL, funciona mesmo sem billing) e
    preenche as linhas antigas com config_nome = banco_nome."""
    tabela = client.get_table(table_id)
    colunas_atuais = {campo.name for campo in tabela.schema}

    if "config_nome" in colunas_atuais:
        return

    tabela.schema = list(tabela.schema) + [bigquery.SchemaField("config_nome", "STRING")]
    client.update_table(tabela, ["schema"])

    tabela_str = f"`{table_id}`"
    try:
        client.query(f"UPDATE {tabela_str} SET config_nome = banco_nome WHERE config_nome IS NULL").result()
    except Exception as exc:  # noqa: BLE001
        if not erro_e_de_billing(exc):
            raise
        rebuild_query = f"""
            CREATE OR REPLACE TABLE {tabela_str} AS
            SELECT
              banco_tipo,
              banco_nome,
              COALESCE(config_nome, banco_nome) AS config_nome,
              campo_destino,
              campo_origem,
              atualizado_em
            FROM {tabela_str}
        """
        client.query(rebuild_query).result()


def _tabela_esta_vazia(client):
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_MAPEAMENTO}`"
    linhas = list(client.query(f"SELECT COUNT(*) AS total FROM {tabela}").result())
    return int(linhas[0]["total"] or 0) == 0


def _seed_inicial(client):
    """Na primeira vez que a tabela é usada, popula com o que já estava no
    config_bancos.json — assim nada que já funcionava se perde. Por
    enquanto, config_nome nasce igual a banco_nome (1 config por banco);
    quando um banco precisar de uma segunda configuração, dá pra editar o
    config_nome dela pela tela de Parâmetros."""
    linhas = []
    agora = datetime.utcnow()
    for banco_tipo, config in CONFIG_BANCOS.items():
        banco_nome = (config.get("fixos", {}).get("banco") or banco_tipo).strip()
        for campo_destino, campo_origem in config.get("mapeamento", {}).items():
            if not campo_origem:
                continue
            linhas.append({
                "banco_tipo": banco_tipo,
                "banco_nome": banco_nome,
                "config_nome": banco_nome,
                "campo_destino": campo_destino,
                "campo_origem": campo_origem,
                "atualizado_em": agora,
            })

    if not linhas:
        return

    df = pd.DataFrame(linhas)
    table_id = f"{PROJECT}.{DATASET}.{TABELA_MAPEAMENTO}"
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()


def _client_com_seed():
    garantir_tabela_mapeamento()
    client = get_bigquery_client()
    if _tabela_esta_vazia(client):
        _seed_inicial(client)
    return client


@cached()
def listar_mapeamento(banco_tipo=None):
    client = _client_com_seed()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_MAPEAMENTO}`"

    query = f"""
        SELECT banco_tipo, banco_nome, config_nome, campo_destino, campo_origem, atualizado_em
        FROM {tabela}
        WHERE (@banco_tipo IS NULL OR banco_tipo = @banco_tipo)
        ORDER BY banco_nome, campo_destino
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("banco_tipo", "STRING", banco_tipo or None)]
    )
    rows = client.query(query, job_config=job_config).result()
    return [dict(row) for row in rows]


def obter_mapeamento_por_banco(banco_tipo):
    """Usado pelo importador (lib/importador.py): retorna
    {campo_destino: campo_origem} vindo do BigQuery (editável pela tela de
    Parâmetros). Se não houver nada gravado ainda pra esse banco, cai de
    volta no config_bancos.json como segurança."""
    registros = listar_mapeamento(banco_tipo=banco_tipo)
    if not registros:
        return CONFIG_BANCOS.get(banco_tipo, {}).get("mapeamento", {})
    return {r["campo_destino"]: r["campo_origem"] for r in registros}


def definir_mapeamento(banco_tipo, campo_destino, campo_origem, banco_nome=None, config_nome=None):
    """Adiciona um novo mapeamento ou edita (substitui) um já existente."""
    client = _client_com_seed()
    if banco_nome is None:
        banco_nome = (CONFIG_BANCOS.get(banco_tipo, {}).get("fixos", {}).get("banco") or banco_tipo).strip()
    if config_nome is None:
        config_nome = obter_config_nome(banco_tipo) or banco_nome

    with _escrita_lock:
        _remover_linha(client, banco_tipo, campo_destino)
        _inserir_linhas(client, [{
            "banco_tipo": banco_tipo,
            "banco_nome": banco_nome,
            "config_nome": config_nome,
            "campo_destino": campo_destino,
            "campo_origem": campo_origem,
            "atualizado_em": datetime.utcnow(),
        }])

    invalidar_tudo()


def excluir_mapeamento(banco_tipo, campo_destino):
    client = _client_com_seed()
    with _escrita_lock:
        _remover_linha(client, banco_tipo, campo_destino)
    invalidar_tudo()


def configs_existentes():
    """(banco_tipo, config_nome) distintos — cada linha é uma configuração
    de importação. Um mesmo banco pode aparecer em mais de uma config."""
    registros = listar_mapeamento()
    vistos = {}
    for r in registros:
        vistos.setdefault(r["banco_tipo"], r.get("config_nome") or r["banco_nome"])
    for banco_tipo, config in CONFIG_BANCOS.items():
        nome = (config.get("fixos", {}).get("banco") or banco_tipo).strip()
        vistos.setdefault(banco_tipo, nome)
    return sorted(vistos.items(), key=lambda item: item[1])


def bancos_nomes_distintos():
    """Nomes de banco (dado real, usado nos filtros de Visualizar/
    Relatório/Dashboard/Indicados) — pode ter menos itens que
    configs_existentes(), já que um banco pode ter mais de uma config."""
    registros = listar_mapeamento()
    nomes = {r["banco_nome"] for r in registros if r.get("banco_nome")}
    for config in CONFIG_BANCOS.values():
        nome = (config.get("fixos", {}).get("banco") or "").strip()
        if nome:
            nomes.add(nome)
    return sorted(nomes)


def banco_ja_existe(banco_tipo):
    return banco_tipo in dict(configs_existentes())


def montar_grid_mapeamento():
    """Monta a grade horizontal: uma linha por configuração, uma coluna por
    campo mapeável — é o que alimenta a tela de Parâmetros. Cada linha
    mostra tanto o banco (dado real) quanto a config (identificador único
    daquela configuração de importação)."""
    registros = listar_mapeamento()
    por_config = {}
    for r in registros:
        entry = por_config.setdefault(r["banco_tipo"], {
            "banco_nome": r["banco_nome"],
            "config_nome": r.get("config_nome") or r["banco_nome"],
            "campos": {},
        })
        entry["campos"][r["campo_destino"]] = r["campo_origem"]

    grid = []
    for banco_tipo, config_nome_fallback in configs_existentes():
        dados = por_config.get(banco_tipo, {})
        banco_nome = dados.get("banco_nome", config_nome_fallback)
        config_nome = dados.get("config_nome", config_nome_fallback)
        campos_config = dados.get("campos", {})
        grid.append({
            "banco_tipo": banco_tipo,
            "banco_nome": banco_nome,
            "config_nome": config_nome,
            "campos": {campo: campos_config.get(campo, "") for campo in CAMPOS_MAPEAVEIS},
        })
    return grid


def definir_linha_completa(banco_tipo, banco_nome, config_nome, campos):
    """Salva (adiciona ou edita) todos os campos de uma configuração de uma
    vez só — usado pelo formulário horizontal da tela de Parâmetros. Valida
    as regras de negócio antes de gravar qualquer coisa. Campo deixado
    vazio remove o mapeamento (se existir); campo preenchido grava/atualiza.

    São só DUAS operações na tabela (remove a configuração inteira e insere
    as linhas preenchidas), e não duas por campo: o BigQuery limita as
    alterações por tabela (~5 a cada 10 s) e gravar campo a campo estourava
    esse limite e deixava o salvamento lento."""
    erros = validar_linha_completa(campos)
    if erros:
        return erros

    agora = datetime.utcnow()
    linhas = [
        {
            "banco_tipo": banco_tipo,
            "banco_nome": banco_nome,
            "config_nome": config_nome,
            "campo_destino": campo_destino,
            "campo_origem": (campos.get(campo_destino) or "").strip(),
            "atualizado_em": agora,
        }
        for campo_destino in CAMPOS_MAPEAVEIS
        if (campos.get(campo_destino) or "").strip()
    ]

    client = _client_com_seed()
    with _escrita_lock:
        _remover_configuracao(client, banco_tipo)
        _inserir_linhas(client, linhas)

    invalidar_tudo()
    return []


def excluir_todos_mapeamentos_do_banco(banco_tipo):
    """Remove a configuração inteira (todos os campos) numa operação só."""
    client = _client_com_seed()
    with _escrita_lock:
        _remover_configuracao(client, banco_tipo)
    invalidar_tudo()


def _remover_configuracao(client, banco_tipo):
    """Apaga todas as linhas de um banco_tipo — DELETE, ou CREATE OR
    REPLACE TABLE quando o projeto não tem billing (mesmo padrão de
    _remover_linha)."""
    tabela_id = f"{PROJECT}.{DATASET}.{TABELA_MAPEAMENTO}"
    tabela = f"`{tabela_id}`"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("bt", "STRING", banco_tipo)]
    )
    try:
        client.query(f"DELETE FROM {tabela} WHERE banco_tipo = @bt", job_config=job_config).result()
    except Exception as exc:  # noqa: BLE001
        if not erro_e_de_billing(exc):
            raise
        rebuild_query = f"""
            CREATE OR REPLACE TABLE {tabela} AS
            SELECT * FROM {tabela}
            WHERE banco_tipo != @bt
        """
        client.query(rebuild_query, job_config=job_config).result()


def _inserir_linhas(client, linhas):
    if not linhas:
        return
    table_id = f"{PROJECT}.{DATASET}.{TABELA_MAPEAMENTO}"
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(pd.DataFrame(linhas), table_id, job_config=job_config).result()


def obter_banco_nome(banco_tipo):
    """Nome real do banco (o que vira o valor da coluna `banco` na base
    consolidada) — não confundir com obter_config_nome()."""
    registros = listar_mapeamento(banco_tipo=banco_tipo)
    if registros:
        return registros[0]["banco_nome"]
    return (CONFIG_BANCOS.get(banco_tipo, {}).get("fixos", {}).get("banco") or banco_tipo).strip()


def obter_config_nome(banco_tipo):
    """Nome da configuração (o que aparece no seletor da tela Importar)."""
    registros = listar_mapeamento(banco_tipo=banco_tipo)
    if registros:
        return registros[0].get("config_nome") or registros[0]["banco_nome"]
    return (CONFIG_BANCOS.get(banco_tipo, {}).get("fixos", {}).get("banco") or banco_tipo).strip()


def _remover_linha(client, banco_tipo, campo_destino):
    """DELETE com fallback via CREATE OR REPLACE TABLE (DDL) caso o
    projeto não tenha billing habilitado — mesmo padrão usado em
    lib/logs.py e lib/indicados.py."""
    tabela_id = f"{PROJECT}.{DATASET}.{TABELA_MAPEAMENTO}"
    tabela = f"`{tabela_id}`"

    try:
        query = f"DELETE FROM {tabela} WHERE banco_tipo = @bt AND campo_destino = @cd"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("bt", "STRING", banco_tipo),
                bigquery.ScalarQueryParameter("cd", "STRING", campo_destino),
            ]
        )
        client.query(query, job_config=job_config).result()

    except Exception as exc:  # noqa: BLE001
        if not erro_e_de_billing(exc):
            raise

        rebuild_query = f"""
            CREATE OR REPLACE TABLE `{tabela_id}` AS
            SELECT * FROM {tabela}
            WHERE NOT (banco_tipo = @bt AND campo_destino = @cd)
        """
        job_config2 = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("bt", "STRING", banco_tipo),
                bigquery.ScalarQueryParameter("cd", "STRING", campo_destino),
            ]
        )
        client.query(rebuild_query, job_config=job_config2).result()
