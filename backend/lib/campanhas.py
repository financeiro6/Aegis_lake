import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
from google.cloud import bigquery

from lib.bigquery_client import get_bigquery_client, erro_e_de_billing
from lib.cache import cached, invalidar_tudo

PROJECT = os.environ.get("BIGQUERY_PROJECT_ID")
DATASET = os.environ.get("BIGQUERY_DATASET", "base_bancos")
TABELA_CAMPANHAS = os.environ.get("BIGQUERY_CAMPANHAS_TABLE", "campanhas")
TABELA_CRITERIOS = os.environ.get("BIGQUERY_CRITERIOS_TABLE", "campanhas_criterios")

# ─────────────────────────────────────────────────────────────────────────
# Campanhas
# ─────────────────────────────────────────────────────────────────────────
CAMPOS_CAMPANHA = ["banco", "campanha", "data_inicio", "data_fim", "status", "base_producao"]
CAMPOS_CAMPANHA_NUMERO = []
CAMPOS_CAMPANHA_DATA = ["data_inicio", "data_fim"]

STATUS_CAMPANHA_VALIDOS = ["Vigente", "Finalizada", "Em Apuração"]
STATUS_CAMPANHA_PADRAO = "Vigente"

BASE_PRODUCAO_VALIDOS = ["liquido", "bruto"]
BASE_PRODUCAO_PADRAO = "liquido"

SCHEMA_CAMPANHAS = [
    bigquery.SchemaField("id", "STRING"),
    bigquery.SchemaField("banco", "STRING"),
    bigquery.SchemaField("campanha", "STRING"),
    bigquery.SchemaField("data_inicio", "DATE"),
    bigquery.SchemaField("data_fim", "DATE"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("faixas_metas", "STRING"),  # JSON: [{"faixa": 1000, "meta": 50}, ...]
    # Qual coluna da base consolidada conta como produção pro atingimento
    # de meta por padrão nessa campanha: "liquido" (vlr_liquido) ou
    # "bruto" (vlr_bruto). Cada critério pode ter o seu próprio valor
    # (ver SCHEMA_CRITERIOS.valor_base) — esse aqui é só o padrão da
    # campanha como um todo.
    bigquery.SchemaField("base_producao", "STRING"),
    # Filtro opcional de QUAL produção conta pro atingimento de meta dessa
    # campanha. Cada um é uma lista de valores (JSON) — vazio/ausente
    # significa "não filtra por esse critério" (considera tudo). Os
    # valores vêm das colunas de tratamento map_indicado/map_convenio/
    # map_produto (calculadas em Manutenção → Cruzar dados), que ainda
    # não estão totalmente povoadas — a rota já fica pronta pra quando
    # estiverem.
    bigquery.SchemaField("filtro_map_indicado", "STRING"),
    bigquery.SchemaField("filtro_map_convenio", "STRING"),
    bigquery.SchemaField("filtro_map_produto", "STRING"),
    bigquery.SchemaField("criado_em", "TIMESTAMP"),
    bigquery.SchemaField("criado_por", "STRING"),
]

# ─────────────────────────────────────────────────────────────────────────
# Critérios
# ─────────────────────────────────────────────────────────────────────────
CAMPOS_CRITERIO = [
    "campanha_id", "banco", "campanha", "convenio", "produto", "base_producao_criterio",
    "tabela", "descr_tabela", "prazo_min", "prazo_max", "valor_min", "valor_max",
    "data_inicio", "data_fim", "status", "perc_especial",
]
# "base_producao_criterio" é a escolha de qual coluna da base consolidada
# esse critério considera como produção: "liquido" (vlr_liquido) ou
# "bruto" (vlr_bruto) — NUNCA foi um número digitado. O nome do campo é
# esse (e não "valor_base", como em versões antigas do código) porque a
# tabela real no BigQuery já tinha uma coluna "valor_base" do tipo
# FLOAT64 de um design anterior (quando esse campo era mesmo numérico) —
# e como _migrar_schema_generico só ADICIONA coluna nova, nunca muda o
# tipo de uma já existente, gravar uma string ali quebrava com "Could
# not convert 'liquido' ... tried to convert to double". Usar um nome de
# coluna novo evita esse conflito de tipo sem precisar de nenhuma
# migração manual na tabela.
# "base_producao_criterio" nunca foi um número digitado — sempre foi a
# escolha de qual coluna da base consolidada esse critério considera
# como produção ("liquido"/vlr_liquido ou "bruto"/vlr_bruto).
CAMPOS_CRITERIO_NUMERO = ["prazo_min", "prazo_max", "valor_min", "valor_max", "perc_especial"]
CAMPOS_CRITERIO_DATA = ["data_inicio", "data_fim"]

SCHEMA_CRITERIOS = [
    bigquery.SchemaField("id", "STRING"),
    bigquery.SchemaField("campanha_id", "STRING"),
    bigquery.SchemaField("banco", "STRING"),
    bigquery.SchemaField("campanha", "STRING"),
    bigquery.SchemaField("convenio", "STRING"),
    bigquery.SchemaField("produto", "STRING"),
    bigquery.SchemaField("base_producao_criterio", "STRING"),
    bigquery.SchemaField("tabela", "STRING"),
    bigquery.SchemaField("descr_tabela", "STRING"),
    bigquery.SchemaField("prazo_min", "FLOAT64"),
    bigquery.SchemaField("prazo_max", "FLOAT64"),
    bigquery.SchemaField("valor_min", "FLOAT64"),
    bigquery.SchemaField("valor_max", "FLOAT64"),
    bigquery.SchemaField("data_inicio", "DATE"),
    bigquery.SchemaField("data_fim", "DATE"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("perc_especial", "FLOAT64"),
    bigquery.SchemaField("criado_em", "TIMESTAMP"),
    bigquery.SchemaField("criado_por", "STRING"),
]

STATUS_CAMPANHA_BLOQUEIA_EDICAO = ["Finalizada", "Em Apuração"]

# ─────────────────────────────────────────────────────────────────────────
# Auditoria de critérios (histórico de alterações)
# ─────────────────────────────────────────────────────────────────────────
TABELA_AUDITORIA = os.environ.get("BIGQUERY_AUDITORIA_CRITERIOS_TABLE", "campanhas_criterios_auditoria")

SCHEMA_AUDITORIA = [
    bigquery.SchemaField("id", "STRING"),
    bigquery.SchemaField("criterio_id", "STRING"),
    bigquery.SchemaField("campanha_id", "STRING"),
    bigquery.SchemaField("acao", "STRING"),  # "criado" | "editado" | "excluido"
    bigquery.SchemaField("dados", "STRING"),  # JSON com o estado salvo
    bigquery.SchemaField("usuario", "STRING"),
    bigquery.SchemaField("timestamp", "TIMESTAMP"),
]

_tabelas_garantidas = set()
_lock = threading.Lock()


def _migrar_schema_generico(client, table_id, schema_completo):
    """Adiciona colunas novas via ALTER TABLE se a tabela já existia sem
    elas (mesmo padrão usado em lib/usuarios.py e lib/setup.py)."""
    tabela = client.get_table(table_id)
    colunas_atuais = {campo.name for campo in tabela.schema}
    campos_novos = [c for c in schema_completo if c.name not in colunas_atuais]
    if not campos_novos:
        return
    tabela.schema = list(tabela.schema) + campos_novos
    client.update_table(tabela, ["schema"])


def _garantir_tabela(nome_tabela, schema):
    """Confere/cria a tabela — só de verdade uma vez por processo (mesmo
    padrão usado em lib/mapeamento.py, lib/setup.py, lib/indicados.py)."""
    if nome_tabela in _tabelas_garantidas:
        return
    with _lock:
        if nome_tabela in _tabelas_garantidas:
            return
        client = get_bigquery_client()
        client.create_dataset(f"{PROJECT}.{DATASET}", exists_ok=True)
        table_id = f"{PROJECT}.{DATASET}.{nome_tabela}"
        table = bigquery.Table(table_id, schema=schema)
        client.create_table(table, exists_ok=True)
        _migrar_schema_generico(client, table_id, schema)
        _tabelas_garantidas.add(nome_tabela)


def _remover_por_id(nome_tabela, id_):
    """DELETE com fallback via CREATE OR REPLACE TABLE (DDL) caso o
    projeto não tenha billing habilitado."""
    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{nome_tabela}"
    tabela = f"`{table_id}`"

    try:
        query = f"DELETE FROM {tabela} WHERE id = @id"
        job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", id_)])
        client.query(query, job_config=job_config).result()
    except Exception as exc:  # noqa: BLE001
        if not erro_e_de_billing(exc):
            raise
        rebuild_query = f"CREATE OR REPLACE TABLE {tabela} AS SELECT * FROM {tabela} WHERE id != @id"
        job_config2 = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", id_)])
        client.query(rebuild_query, job_config=job_config2).result()


def _preparar_linha(dados, campos, campos_numero, campos_data=None):
    """Monta a linha a partir do que veio do formulário. Campos de data
    (ex: "2026-08-01", como vem de <input type="date">) precisam virar
    datetime.date de verdade antes de ir pro BigQuery — passar a string
    crua funciona nos testes locais (o simulador não valida tipo), mas
    falha na carga real: load_table_from_dataframe serializa a coluna
    como STRING quando os valores são str, e isso não bate com o tipo
    DATE da tabela de destino."""
    campos_data = campos_data or []
    linha = {}
    for campo in campos:
        valor = dados.get(campo)
        if campo in campos_numero:
            linha[campo] = float(valor) if valor not in (None, "") else None
        elif campo in campos_data:
            linha[campo] = _para_date(valor)
        else:
            linha[campo] = (valor or "").strip() if isinstance(valor, str) else valor
    return linha


def _para_date(valor):
    """Converte "2026-08-01" (string do <input type="date">) em
    datetime.date. Aceita None/"" (campo de data opcional, tipo Data fim
    de um critério) e também já aceita um date/datetime pronto (não
    quebra se já vier convertido)."""
    if valor in (None, ""):
        return None
    if isinstance(valor, date):
        return valor
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, str):
        return datetime.strptime(valor.strip()[:10], "%Y-%m-%d").date()
    return valor


# ─────────────────────────────────────────────────────────────────────────
# API pública: campanhas
# ─────────────────────────────────────────────────────────────────────────
CAMPOS_FILTRO_PRODUCAO = ["filtro_map_indicado", "filtro_map_convenio", "filtro_map_produto"]


@cached()
def listar_campanhas():
    _garantir_tabela(TABELA_CAMPANHAS, SCHEMA_CAMPANHAS)
    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_CAMPANHAS}`"
    colunas = ", ".join(["id"] + CAMPOS_CAMPANHA + ["faixas_metas"] + CAMPOS_FILTRO_PRODUCAO + ["criado_em", "criado_por"])
    rows = client.query(f"SELECT {colunas} FROM {tabela} ORDER BY banco, campanha").result()

    resultado = []
    for row in rows:
        item = dict(row)
        bruto = item.get("faixas_metas")
        try:
            item["faixas_metas"] = json.loads(bruto) if bruto else []
        except (TypeError, ValueError):
            item["faixas_metas"] = []

        for campo in CAMPOS_FILTRO_PRODUCAO:
            bruto_filtro = item.get(campo)
            try:
                item[campo] = json.loads(bruto_filtro) if bruto_filtro else []
            except (TypeError, ValueError):
                item[campo] = []

        resultado.append(item)
    return resultado


def salvar_campanha(dados, criado_por, id_existente=None):
    """Cria uma campanha nova (id_existente=None) ou edita uma já
    existente (substitui a linha antiga pelo id). Campanha nova sempre
    nasce com status "Vigente", a menos que outro status válido seja
    explicitamente informado."""
    _garantir_tabela(TABELA_CAMPANHAS, SCHEMA_CAMPANHAS)
    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{TABELA_CAMPANHAS}"

    id_final = id_existente or uuid.uuid4().hex
    if id_existente:
        _remover_por_id(TABELA_CAMPANHAS, id_existente)

    linha = _preparar_linha(dados, CAMPOS_CAMPANHA, CAMPOS_CAMPANHA_NUMERO, CAMPOS_CAMPANHA_DATA)

    if linha.get("status") not in STATUS_CAMPANHA_VALIDOS:
        linha["status"] = STATUS_CAMPANHA_PADRAO

    if linha.get("base_producao") not in BASE_PRODUCAO_VALIDOS:
        linha["base_producao"] = BASE_PRODUCAO_PADRAO

    faixas_metas = dados.get("faixas_metas") or []
    linha["faixas_metas"] = json.dumps(faixas_metas)

    for campo in CAMPOS_FILTRO_PRODUCAO:
        valores = dados.get(campo) or []
        linha[campo] = json.dumps(valores)

    linha.update({"id": id_final, "criado_em": datetime.utcnow(), "criado_por": criado_por})

    df = pd.DataFrame([linha])
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()

    invalidar_tudo()
    return id_final


def atualizar_status_campanha(id_, novo_status, atualizado_por):
    """Muda só o status de uma campanha já existente, preservando todo o
    resto (regrava a linha inteira, já que é assim que o upsert funciona)."""
    if novo_status not in STATUS_CAMPANHA_VALIDOS:
        raise ValueError(f'Status inválido: "{novo_status}". Use um de: {", ".join(STATUS_CAMPANHA_VALIDOS)}.')

    campanhas = listar_campanhas()
    alvo = next((c for c in campanhas if c["id"] == id_), None)
    if not alvo:
        raise ValueError("Campanha não encontrada.")

    dados = {campo: alvo.get(campo) for campo in CAMPOS_CAMPANHA}
    dados["status"] = novo_status
    dados["faixas_metas"] = alvo.get("faixas_metas") or []

    return salvar_campanha(dados, criado_por=atualizado_por, id_existente=id_)


def excluir_campanha(id_):
    _garantir_tabela(TABELA_CAMPANHAS, SCHEMA_CAMPANHAS)
    _remover_por_id(TABELA_CAMPANHAS, id_)
    invalidar_tudo()


def renovar_campanha(id_, nova_data_inicio, nova_data_fim, criado_por):
    """Renova uma campanha: cria uma campanha NOVA (nova linha, novo id),
    copiando banco/nome/faixas-metas/filtros da campanha original, mas
    com o novo período de apuração informado — e também copia (clona)
    todos os critérios já cadastrados na campanha original para essa
    campanha nova, já que uma renovação normalmente segue as mesmas
    regras de apuração de antes. A campanha original não é alterada."""
    campanhas = listar_campanhas()
    original = next((c for c in campanhas if c["id"] == id_), None)
    if not original:
        raise ValueError("Campanha não encontrada.")

    novo_di = _para_date(nova_data_inicio)
    novo_df = _para_date(nova_data_fim)
    if not novo_di or not novo_df or novo_di > novo_df:
        raise ValueError("Período da nova campanha inválido: data de início precisa ser anterior ou igual à data de fim.")

    dados_nova_campanha = {campo: original.get(campo) for campo in CAMPOS_CAMPANHA}
    dados_nova_campanha["data_inicio"] = novo_di
    dados_nova_campanha["data_fim"] = novo_df
    dados_nova_campanha["status"] = STATUS_CAMPANHA_PADRAO
    dados_nova_campanha["faixas_metas"] = original.get("faixas_metas") or []
    for campo in CAMPOS_FILTRO_PRODUCAO:
        dados_nova_campanha[campo] = original.get(campo) or []

    novo_id = salvar_campanha(dados_nova_campanha, criado_por=criado_por)

    criterios_originais = [c for c in listar_criterios() if c.get("campanha_id") == id_]
    for criterio in criterios_originais:
        dados_criterio = {campo: criterio.get(campo) for campo in CAMPOS_CRITERIO}
        dados_criterio["campanha_id"] = novo_id
        salvar_criterio(dados_criterio, criado_por=criado_por)

    return novo_id


# ─────────────────────────────────────────────────────────────────────────
# API pública: critérios
# ─────────────────────────────────────────────────────────────────────────
def _registrar_auditoria(criterio_id, campanha_id, acao, dados, usuario):
    _garantir_tabela(TABELA_AUDITORIA, SCHEMA_AUDITORIA)
    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{TABELA_AUDITORIA}"

    linha = pd.DataFrame([{
        "id": uuid.uuid4().hex,
        "criterio_id": criterio_id,
        "campanha_id": campanha_id,
        "acao": acao,
        "dados": json.dumps(dados, default=str),
        "usuario": usuario,
        "timestamp": datetime.utcnow(),
    }])
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(linha, table_id, job_config=job_config).result()


@cached()
def listar_auditoria_criterios(campanha_id=None):
    _garantir_tabela(TABELA_AUDITORIA, SCHEMA_AUDITORIA)
    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_AUDITORIA}`"
    query = f"""
        SELECT id, criterio_id, campanha_id, acao, dados, usuario, timestamp
        FROM {tabela}
        WHERE (@campanha_id IS NULL OR campanha_id = @campanha_id)
        ORDER BY timestamp DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("campanha_id", "STRING", campanha_id)]
    )
    rows = client.query(query, job_config=job_config).result()
    resultado = []
    for row in rows:
        item = dict(row)
        try:
            item["dados"] = json.loads(item["dados"]) if item.get("dados") else {}
        except (TypeError, ValueError):
            item["dados"] = {}
        resultado.append(item)
    return resultado


def _validar_campanha_editavel(campanha_id):
    """Regra de negócio: não é possível criar/editar/excluir um critério de
    uma campanha marcada como Finalizada ou Em Apuração."""
    if not campanha_id:
        return
    campanhas = listar_campanhas()
    alvo = next((c for c in campanhas if c["id"] == campanha_id), None)
    if alvo and alvo.get("status") in STATUS_CAMPANHA_BLOQUEIA_EDICAO:
        raise ValueError(
            f'Não é possível editar critérios de uma campanha "{alvo.get("status")}". '
            "Mude o status da campanha antes, se realmente precisar editar."
        )


@cached()
def listar_criterios():
    _garantir_tabela(TABELA_CRITERIOS, SCHEMA_CRITERIOS)
    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_CRITERIOS}`"
    colunas = ", ".join(["id"] + CAMPOS_CRITERIO + ["criado_em", "criado_por"])
    rows = client.query(f"SELECT {colunas} FROM {tabela} ORDER BY banco, campanha").result()
    return [dict(row) for row in rows]


def salvar_criterio(dados, criado_por, id_existente=None):
    campanha_id = dados.get("campanha_id")
    _validar_campanha_editavel(campanha_id)

    _garantir_tabela(TABELA_CRITERIOS, SCHEMA_CRITERIOS)
    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{TABELA_CRITERIOS}"

    id_final = id_existente or uuid.uuid4().hex
    if id_existente:
        _remover_por_id(TABELA_CRITERIOS, id_existente)

    linha = _preparar_linha(dados, CAMPOS_CRITERIO, CAMPOS_CRITERIO_NUMERO, CAMPOS_CRITERIO_DATA)

    if linha.get("base_producao_criterio") not in BASE_PRODUCAO_VALIDOS:
        # Se o critério não escolheu explicitamente, herda o padrão da
        # campanha (e se nem a campanha tiver, cai no padrão geral).
        campanha_relacionada = next((c for c in listar_campanhas() if c["id"] == campanha_id), None)
        linha["base_producao_criterio"] = (campanha_relacionada or {}).get("base_producao") or BASE_PRODUCAO_PADRAO

    linha.update({"id": id_final, "criado_em": datetime.utcnow(), "criado_por": criado_por})

    df = pd.DataFrame([linha])
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()

    _registrar_auditoria(id_final, campanha_id, "editado" if id_existente else "criado", linha, criado_por)

    invalidar_tudo()
    return id_final


def excluir_criterio(id_, excluido_por=None):
    criterios = listar_criterios()
    alvo = next((c for c in criterios if c["id"] == id_), None)
    campanha_id = alvo.get("campanha_id") if alvo else None
    _validar_campanha_editavel(campanha_id)

    _garantir_tabela(TABELA_CRITERIOS, SCHEMA_CRITERIOS)
    _remover_por_id(TABELA_CRITERIOS, id_)

    _registrar_auditoria(id_, campanha_id, "excluido", alvo or {}, excluido_por)

    invalidar_tudo()


# ─────────────────────────────────────────────────────────────────────────
# Atingimento de meta (liga a campanha à produção real da base consolidada)
# ─────────────────────────────────────────────────────────────────────────
def _filtros_producao_campanha(campanha, data_inicio, data_fim):
    """Condições SQL + parâmetros que definem QUAL produção conta para a
    campanha: banco + período + filtros opcionais de map_indicado/
    map_convenio/map_produto. Usado pela Visão geral e pelo relatório de
    apuração — os dois precisam olhar exatamente a mesma produção, senão
    a faixa atingida sai diferente em cada um."""
    from lib.dashboard import DATE_COLUMN

    condicoes = [f"UPPER(banco) = @banco", f"{DATE_COLUMN} BETWEEN @data_inicio AND @data_fim"]
    params = [
        bigquery.ScalarQueryParameter("banco", "STRING", (campanha.get("banco") or "").upper()),
        bigquery.ScalarQueryParameter("data_inicio", "DATE", data_inicio),
        bigquery.ScalarQueryParameter("data_fim", "DATE", data_fim),
    ]

    mapa_filtros = {
        "filtro_map_indicado": "map_indicado",
        "filtro_map_convenio": "map_convenio",
        "filtro_map_produto": "map_produto",
    }
    for i, (campo_filtro, coluna) in enumerate(mapa_filtros.items()):
        valores = campanha.get(campo_filtro) or []
        if valores:
            nome_param = f"filtro_{i}"
            condicoes.append(f"{coluna} IN UNNEST(@{nome_param})")
            params.append(bigquery.ArrayQueryParameter(nome_param, "STRING", valores))

    return " AND ".join(condicoes), params


@cached()
def _detalhes_producao_campanha(campanha, data_inicio, data_fim):
    """Busca as linhas de produção da campanha (banco + período + filtros
    opcionais de map_indicado/map_convenio/map_produto), já com os campos
    necessários pra casar cada linha com um critério (mesma lógica do
    relatório de apuração) e pra calcular a projeção (data de cada
    linha)."""
    from lib.dashboard import PROJECT as P_DASH, DATASET as D_DASH, TABELA_PRINCIPAL, DATE_COLUMN
    from lib.manutencao import garantir_colunas_map

    garantir_colunas_map()

    client = get_bigquery_client()
    tabela = f"`{P_DASH}.{D_DASH}.{TABELA_PRINCIPAL}`"

    where_sql, params = _filtros_producao_campanha(campanha, data_inicio, data_fim)
    query = f"""
        SELECT
            {DATE_COLUMN} AS data_pagamento,
            convenio, produto, map_convenio, map_produto,
            cod_tabela, tabela, vlr_liquido, vlr_bruto, prazo
        FROM {tabela}
        WHERE {where_sql}
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = client.query(query, job_config=job_config).result()
    return [dict(row) for row in rows]


def _avaliar_faixas(faixas_metas, producao):
    """Dado o valor já produzido, descobre em qual faixa a campanha está
    (a maior meta de produção já alcançada) e qual é a próxima a
    perseguir.

    Faixas vêm como [{"faixa": percentual_de_bonus, "meta": limite_de_producao_em_reais}, ...]:
      - "meta"  → o QUANTO precisa produzir (R$) pra entrar nessa faixa.
        É por isso que a comparação com a produção usa "meta", não
        "faixa" — comparar com "faixa" (que é um percentual, tipicamente
        bem menor que qualquer produção real) fazia qualquer produção
        "bater" a primeira faixa quase de imediato, mesmo sem a meta
        real ter sido alcançada.
      - "faixa" → o percentual de bônus pago quando aquela meta é
        atingida. Por isso "faixa_atingida" é sempre um percentual, e
        tem que voltar 0 (não a faixa mais baixa, nem None) quando
        nenhuma meta ainda foi batida.
    """
    faixas_validas = [
        f for f in (faixas_metas or [])
        if f.get("faixa") is not None and f.get("meta") is not None
    ]
    faixas_ordenadas = sorted(faixas_validas, key=lambda f: f["meta"])

    atual = None
    proxima = None
    for tier in faixas_ordenadas:
        if producao >= tier["meta"]:
            atual = tier
        elif proxima is None:
            proxima = tier

    tier_atingido = atual is not None
    faixa_atingida = atual["faixa"] if atual else 0.0
    meta_atingida = atual["meta"] if atual else 0.0
    proxima_meta = proxima["meta"] if proxima else None
    proxima_faixa = proxima["faixa"] if proxima else None

    if proxima_meta:
        percentual = (producao / proxima_meta) * 100
    elif tier_atingido:
        percentual = 100.0
    else:
        percentual = 0.0

    teto_atingido = bool(faixas_ordenadas) and tier_atingido and proxima is None

    return {
        "percentual_atingimento": round(min(percentual, 999.0), 1),
        "faixa_atingida": round(faixa_atingida, 2),
        "meta_atingida": round(meta_atingida, 2),
        "proxima_meta": proxima_meta,
        "proxima_faixa": proxima_faixa,
        "teto_atingido": teto_atingido,
        "tier_atingido": tier_atingido,
    }


def _dias_uteis_entre(data_inicio, data_fim):
    """Conta dias úteis (segunda a sexta), inclusive nas duas pontas."""
    if not data_inicio or not data_fim or data_inicio > data_fim:
        return 0
    dias = 0
    atual = data_inicio
    while atual <= data_fim:
        if atual.weekday() < 5:  # 0=segunda ... 6=domingo
            dias += 1
        atual += timedelta(days=1)
    return dias


@cached()
def calcular_cenarios_campanha(campanha_id, data_inicio=None, data_fim=None):
    """Calcula os 3 cenários financeiros de uma campanha:

    1. CENÁRIO ATUAL — o que já foi produzido e o que está previsto pra
       receber com base nisso.
    2. PROJEÇÃO — o que se espera até o fim da campanha, projetando a
       média diária de produção (calculada só sobre os dias que
       realmente tiveram produção) pelos dias úteis que restam. A data
       de referência pra "quantos dias faltam" é sempre a data mais
       recente que já tem produção importada — não a data de hoje —
       porque a importação pode estar alguns dias atrasada em relação ao
       calendário.
    3. OPORTUNIDADES — quanto falta pra próxima faixa (se existir uma
       faixa mais alta ainda não alcançada) e quanto isso valeria.

    "Valor Campanha" em qualquer um dos três cenários é a soma, linha a
    linha, do que cada proposta de produção vale conforme os critérios
    cadastrados e a faixa (percentual de bônus) que a campanha atingiu:
      - linha que NÃO bate com nenhum critério da campanha → vale 0 (só
        entra no cálculo o que está coberto por alguma regra cadastrada);
      - critério marcado "Não contabilizar" → contrato vale 0;
      - critério com % especial cadastrado → contrato vale valor × %
        especial (o % especial sempre manda, independente da faixa);
      - critério bateu mas sem % especial ("inclusivo") → contrato vale
        valor × percentual da faixa atingida pela campanha;
    e fica zerado por completo se a campanha ainda não tiver alcançado nenhuma
    faixa."""
    campanhas = listar_campanhas()
    campanha = next((c for c in campanhas if c["id"] == campanha_id), None)
    if not campanha:
        raise ValueError("Campanha não encontrada.")

    di_campanha = _para_date(campanha.get("data_inicio"))
    df_campanha = _para_date(campanha.get("data_fim"))
    di_filtro = _para_date(data_inicio)
    df_filtro = _para_date(data_fim)

    # O filtro de data da tela (quando informado) precisa ser
    # INTERSECCIONADO com o período de apuração de cada campanha, nunca
    # substituí-lo — senão todas as campanhas acabam usando a mesma
    # janela de datas (a do filtro), ignorando o período configurado em
    # cada uma e retornando a mesma produção pra campanhas diferentes.
    di = max(di_campanha, di_filtro) if (di_campanha and di_filtro) else (di_campanha or di_filtro)
    df = min(df_campanha, df_filtro) if (df_campanha and df_filtro) else (df_campanha or df_filtro)
    faixas_metas = campanha.get("faixas_metas") or []

    if not di or not df or di > df:
        return {
            "producao_atual": 0.0, "valor_campanha_atual": 0.0,
            "faixa_atingida": 0.0, "meta_atingida": 0.0,
            "producao_prevista": 0.0, "valor_campanha_previsto": 0.0,
            "faixa_prevista": 0.0, "meta_prevista_valor": 0.0, "percentual_atingimento_projecao": 0.0,
            "producao_necessaria_oportunidade": None, "proxima_faixa": None, "proxima_meta": None,
            "valor_campanha_oportunidade": None, "percentual_atingimento_oportunidade": 0.0,
            "teto_atingido": False,
        }

    linhas = _detalhes_producao_campanha(campanha, di, df)
    criterios_todos = listar_criterios()
    criterios_da_campanha = [c for c in criterios_todos if c.get("campanha_id") == campanha_id]

    coluna_valor = "vlr_bruto" if campanha.get("base_producao") == "bruto" else "vlr_liquido"

    # 1ª passada: soma a produção total e já resolve, linha a linha, qual
    # critério bate (isso não muda com a faixa da campanha, então dá pra
    # calcular uma vez só e reaproveitar depois).
    producao_atual = 0.0
    datas_com_producao = set()
    linhas_com_criterio = []
    for linha in linhas:
        valor_base_campanha = float(linha.get(coluna_valor) or 0)
        producao_atual += valor_base_campanha
        if linha.get("data_pagamento"):
            datas_com_producao.add(linha["data_pagamento"])
        criterio_encontrado = _escolher_criterio_da_linha(criterios_da_campanha, linha)
        linhas_com_criterio.append((linha, criterio_encontrado))

    # 2ª passada: separa o que cada contrato vale em duas partes, porque
    # elas seguem regras BEM diferentes:
    #   - "valor_perc_especial": contratos com % especial cadastrado —
    #     não depende de qual faixa a campanha atinge, então é o MESMO
    #     valor não importa se estamos olhando o cenário atual, a
    #     projeção ou a oportunidade;
    #   - "producao_inclusiva": a produção (ainda SEM aplicar percentual
    #     nenhum) dos contratos "inclusivos" (bateram um critério, sem %
    #     especial) — essa é a parte que precisa da % da faixa, e cada
    #     cenário (atual/projeção/oportunidade) atinge uma faixa
    #     DIFERENTE, então essa multiplicação só pode acontecer depois,
    #     usando a faixa certa de cada cenário (ver abaixo). Antes, um
    #     único "taxa_apuracao" (baseado só na faixa atual) era aplicado
    #     em cima da projeção inteira — o que zerava a projeção sempre
    #     que a faixa atual ainda não tivesse sido alcançada, mesmo
    #     quando a PRÓPRIA produção projetada já cruzava uma meta.
    valor_perc_especial_atual = 0.0
    producao_inclusiva_atual = 0.0
    if criterios_da_campanha:
        for linha, criterio_encontrado in linhas_com_criterio:
            if criterio_encontrado is None:
                continue  # nenhum critério bate essa linha -> não conta pra campanha
            if criterio_encontrado.get("status") == STATUS_CRITERIO_NAO_CONTABILIZAR:
                continue  # soma 0
            # O valor do CONTRATO usa a coluna (líquido/bruto) que O
            # CRITÉRIO que bateu escolheu — não a coluna padrão da
            # campanha. Cada critério pode ter seu próprio "Valor base";
            # ignorar isso fazia um critério cadastrado como "bruto"
            # calcular pelo líquido sempre que a campanha estivesse
            # configurada como líquido (ou vice-versa).
            coluna_valor_criterio = "vlr_bruto" if criterio_encontrado.get("base_producao_criterio") == "bruto" else "vlr_liquido"
            valor_base_linha = float(linha.get(coluna_valor_criterio) or 0)
            if criterio_encontrado.get("perc_especial"):
                valor_perc_especial_atual += valor_base_linha * (float(criterio_encontrado["perc_especial"]) / 100)
            else:
                # Critério bateu (convênio/produto/tabela) mas sem % especial
                # cadastrado ("inclusivo"): segue a regra geral da campanha —
                # a % da faixa é aplicada depois, com a faixa de cada cenário.
                producao_inclusiva_atual += valor_base_linha
    # Sem nenhum critério cadastrado pra campanha, não existe recebimento
    # possível — a produção pode até bater a meta, mas não há regra
    # nenhuma dizendo quanto pagar por ela, então tudo fica zerado.

    aval_atual = _avaliar_faixas(faixas_metas, producao_atual)
    faixa_pct_atual = (aval_atual["faixa_atingida"] or 0.0) / 100
    valor_apuracao_atual = valor_perc_especial_atual + producao_inclusiva_atual * faixa_pct_atual

    # ── 1. Cenário atual ─────────────────────────────────────────────
    valor_campanha_atual = valor_apuracao_atual if aval_atual["tier_atingido"] else 0.0

    # Daqui pra frente (projeção e oportunidade), assume-se que a MESMA
    # proporção de produção segue "especial" vs. "inclusiva" — o que muda
    # é qual faixa cada cenário atinge, então essas duas taxas (uma por
    # R$ de produção total) ficam fixas e a % da faixa entra só na hora
    # de calcular cada cenário.
    taxa_especial_por_producao = (valor_perc_especial_atual / producao_atual) if producao_atual > 0 else 0.0
    proporcao_inclusiva = (producao_inclusiva_atual / producao_atual) if producao_atual > 0 else 0.0

    # ── 2. Projeção até o fim da campanha ────────────────────────────
    # A projeção sempre mira o fim REAL da campanha (df_campanha), não o
    # fim do filtro de data da tela — senão, com o filtro padrão da tela
    # (normalmente "mês atual"), a projeção ficava artificialmente
    # cortada no fim do mês em vez de ir até o fim de verdade da
    # campanha, fazendo campanhas com fins diferentes parecerem com
    # projeções parecidas demais.
    df_projecao = df_campanha or df
    data_referencia = max(datas_com_producao) if datas_com_producao else di
    dias_com_producao_qtd = len(datas_com_producao)
    media_diaria = (producao_atual / dias_com_producao_qtd) if dias_com_producao_qtd > 0 else 0.0
    dias_uteis_restantes = _dias_uteis_entre(data_referencia + timedelta(days=1), df_projecao) if data_referencia < df_projecao else 0
    producao_prevista = producao_atual + (media_diaria * dias_uteis_restantes)

    aval_prevista = _avaliar_faixas(faixas_metas, producao_prevista)
    faixa_pct_prevista = (aval_prevista["faixa_atingida"] or 0.0) / 100
    valor_apuracao_previsto = (
        producao_prevista * taxa_especial_por_producao + producao_prevista * proporcao_inclusiva * faixa_pct_prevista
    )
    valor_campanha_previsto = valor_apuracao_previsto if aval_prevista["tier_atingido"] else 0.0

    # ── 3. Oportunidades (baseado no que já foi produzido, não na projeção) ──
    # "proxima_meta" é o valor de produção (R$) que falta alcançar; é
    # contra ela (não contra "proxima_faixa", que é só o percentual de
    # bônus daquela faixa) que a produção precisa ser comparada. E o
    # valor dessa oportunidade usa a % da PRÓXIMA faixa (a que se quer
    # alcançar) na parte inclusiva — não a faixa atual (que ainda nem foi
    # alcançada) nem a faixa da projeção (que pode ser uma faixa diferente).
    proxima_faixa = aval_atual["proxima_faixa"]
    proxima_meta = aval_atual["proxima_meta"]
    proxima_faixa_pct = (proxima_faixa or 0.0) / 100
    producao_necessaria = (proxima_meta - producao_atual) if proxima_meta is not None else None
    valor_campanha_oportunidade = (
        proxima_meta * taxa_especial_por_producao + proxima_meta * proporcao_inclusiva * proxima_faixa_pct
    ) if proxima_meta is not None else None

    return {
        "producao_atual": round(producao_atual, 2),
        "valor_campanha_atual": round(valor_campanha_atual, 2),
        "faixa_atingida": aval_atual["faixa_atingida"],
        "meta_atingida": round(aval_atual["meta_atingida"], 2),

        "producao_prevista": round(producao_prevista, 2),
        "valor_campanha_previsto": round(valor_campanha_previsto, 2),
        "faixa_prevista": aval_prevista["faixa_atingida"],
        "meta_prevista_valor": round(aval_prevista["meta_atingida"], 2),
        "percentual_atingimento_projecao": aval_prevista["percentual_atingimento"],

        "producao_necessaria_oportunidade": round(producao_necessaria, 2) if producao_necessaria is not None else None,
        "proxima_faixa": proxima_faixa,
        "proxima_meta": proxima_meta,
        "valor_campanha_oportunidade": round(valor_campanha_oportunidade, 2) if valor_campanha_oportunidade is not None else None,
        "percentual_atingimento_oportunidade": aval_atual["percentual_atingimento"],
        "teto_atingido": aval_atual["teto_atingido"],
    }


def listar_campanhas_com_atingimento(banco=None, data_inicio=None, data_fim=None, busca_campanha=None):
    """Lista as campanhas (com filtro opcional de banco/campanha) já
    calculando, pra cada uma, os 3 cenários financeiros (ver
    calcular_cenarios_campanha).

    Quando data_inicio/data_fim são informados, só entram campanhas cujo
    período de apuração TEM ALGUMA SOBREPOSIÇÃO com esse filtro — uma
    campanha de março não deve aparecer quando o filtro pede junho, por
    exemplo, mesmo que ela ainda exista no cadastro."""
    campanhas = listar_campanhas()

    if banco:
        campanhas = [c for c in campanhas if (c.get("banco") or "").upper() == banco.upper()]
    if busca_campanha:
        termo = busca_campanha.lower()
        campanhas = [c for c in campanhas if termo in (c.get("campanha") or "").lower()]

    di_filtro = _para_date(data_inicio)
    df_filtro = _para_date(data_fim)
    if di_filtro or df_filtro:
        def _sobrepoe(c):
            di_c = _para_date(c.get("data_inicio"))
            df_c = _para_date(c.get("data_fim"))
            if di_c and df_filtro and di_c > df_filtro:
                return False
            if df_c and di_filtro and df_c < di_filtro:
                return False
            return True

        campanhas = [c for c in campanhas if _sobrepoe(c)]

    resultado = []
    for campanha in campanhas:
        cenarios = calcular_cenarios_campanha(campanha["id"], data_inicio, data_fim)
        resultado.append({**campanha, **cenarios})

    return resultado


# ─────────────────────────────────────────────────────────────────────────
# Relatório de apuração por proposta (download em Excel, no Cadastro de
# campanha)
# ─────────────────────────────────────────────────────────────────────────
STATUS_CRITERIO_NAO_CONTABILIZAR = "nao_contabilizar"

# Valor gravado pelo formulário quando a versão anterior da tela oferecia
# uma opção "(vazio)" que restringia o critério só a linhas SEM
# map_convenio/map_produto ainda. Pela regra de negócio, "vazio" tem que
# se comportar exatamente como não escolher nada (não filtra por esse
# campo — considera toda a produção, valendo-se só de Tabela/Cód. Tabela
# se o critério tiver isso definido). Mantemos essa constante só pra
# critérios já salvos com esse valor continuarem funcionando (tratados
# como "sem filtro"), sem precisar de uma migração manual na base.
VALOR_VAZIO_LEGADO = "__vazio__"


def _criterio_aplica(criterio, linha):
    """Um critério "aplica" numa linha de produção se TODOS os filtros que
    ele definir baterem: convênio, produto, tabela, prazo (min/max), valor
    (min/max) e vigência (data_inicio/data_fim do critério contra a data
    de pagamento da linha). Um filtro que o critério deixou em branco
    simplesmente não restringe nada (considera toda a produção nesse
    quesito) — só os que foram preenchidos é que precisam bater.

    Convênio e Produto do critério vêm das colunas TRATADAS (map_convenio/
    map_produto — ver Manutenção → Cruzar dados), não da coluna bruta da
    base consolidada, já que são essas que aparecem nas listas de seleção
    de Convênio/Produto no cadastro do critério. Por isso a comparação
    usa map_convenio/map_produto da linha, caindo pro valor bruto
    (convenio/produto) só enquanto essas colunas tratadas ainda não
    estiverem populadas pra aquela linha.

    Convênio/Produto em branco (ou = VALOR_VAZIO_LEGADO) significa "não
    filtra por esse campo" — considera toda a produção nesse quesito."""
    convenio_criterio = criterio.get("convenio")
    if convenio_criterio and convenio_criterio != VALOR_VAZIO_LEGADO:
        convenio_linha = linha.get("map_convenio") or linha.get("convenio")
        if (convenio_linha or "").strip().upper() != convenio_criterio.strip().upper():
            return False
    produto_criterio = criterio.get("produto")
    if produto_criterio and produto_criterio != VALOR_VAZIO_LEGADO:
        produto_linha = linha.get("map_produto") or linha.get("produto")
        if (produto_linha or "").strip().upper() != produto_criterio.strip().upper():
            return False
    if criterio.get("tabela"):
        tabela_linha = str(linha.get("cod_tabela") or linha.get("tabela") or "").strip()
        if tabela_linha != str(criterio["tabela"]).strip():
            return False

    if criterio.get("prazo_min") is not None or criterio.get("prazo_max") is not None:
        prazo_linha = linha.get("prazo")
        if prazo_linha is None:
            return False  # critério exige prazo, mas a linha não tem prazo informado
        prazo_linha = float(prazo_linha)
        if criterio.get("prazo_min") is not None and prazo_linha < float(criterio["prazo_min"]):
            return False
        if criterio.get("prazo_max") is not None and prazo_linha > float(criterio["prazo_max"]):
            return False

    if criterio.get("valor_min") is not None or criterio.get("valor_max") is not None:
        # Compara contra a MESMA coluna de valor (líquido/bruto) que esse
        # critério usa pra calcular o próprio valor de apuração.
        coluna_valor_criterio = "vlr_bruto" if criterio.get("base_producao_criterio") == "bruto" else "vlr_liquido"
        valor_linha = linha.get(coluna_valor_criterio)
        if valor_linha is None:
            return False
        valor_linha = float(valor_linha)
        if criterio.get("valor_min") is not None and valor_linha < float(criterio["valor_min"]):
            return False
        if criterio.get("valor_max") is not None and valor_linha > float(criterio["valor_max"]):
            return False

    if criterio.get("data_inicio") or criterio.get("data_fim"):
        data_linha = linha.get("data_pagamento")
        if data_linha is None:
            return False
        data_linha = data_linha.date() if hasattr(data_linha, "date") and not isinstance(data_linha, date) else data_linha
        di_criterio = _para_date(criterio.get("data_inicio"))
        df_criterio = _para_date(criterio.get("data_fim"))
        if di_criterio and data_linha < di_criterio:
            return False
        if df_criterio and data_linha > df_criterio:
            return False

    return True


def _especificidade_criterio(criterio):
    """Quantos filtros esse critério realmente restringe — usado como
    desempate quando mais de um critério do MESMO nível de prioridade
    bate na mesma linha (ex: dois critérios com % especial, um mais
    amplo e um mais específico dentro do mesmo grupo)."""
    campos_preenchidos = [
        bool(criterio.get("convenio")) and criterio.get("convenio") != VALOR_VAZIO_LEGADO,
        bool(criterio.get("produto")) and criterio.get("produto") != VALOR_VAZIO_LEGADO,
        bool(criterio.get("tabela")),
        criterio.get("prazo_min") is not None,
        criterio.get("prazo_max") is not None,
        criterio.get("valor_min") is not None,
        criterio.get("valor_max") is not None,
        bool(criterio.get("data_inicio")),
        bool(criterio.get("data_fim")),
    ]
    return sum(1 for c in campos_preenchidos if c)


def _prioridade_criterio(criterio):
    """Regra de negócio de prioridade entre critérios que batem na MESMA
    linha: % especial > Não contabilizar > Incluído por regra (inclusivo).
    Um critério com % especial cadastrado sempre manda, mesmo que outro
    critério mais amplo (só "inclusivo") também bata na mesma linha — e um
    "Não contabilizar" específico exclui a linha mesmo que ela também se
    enquadre num critério inclusivo mais amplo do mesmo grupo."""
    if criterio.get("perc_especial"):
        return 3
    if criterio.get("status") == STATUS_CRITERIO_NAO_CONTABILIZAR:
        return 2
    return 1


def _escolher_criterio_da_linha(criterios_da_campanha, linha):
    """Entre TODOS os critérios da campanha que baterem nessa linha,
    escolhe um só, pela prioridade da regra de negócio (ver
    _prioridade_criterio); em caso de empate no mesmo nível, vence o mais
    específico (_especificidade_criterio); e se ainda empatar, o
    cadastrado mais recentemente (desempate determinístico de último
    caso — não é a regra principal). Se nenhum critério bater, None."""
    candidatos = [c for c in criterios_da_campanha if _criterio_aplica(c, linha)]
    if not candidatos:
        return None
    candidatos.sort(
        key=lambda c: (_prioridade_criterio(c), _especificidade_criterio(c), c.get("criado_em") or datetime.min),
        reverse=True,
    )
    return candidatos[0]


def gerar_relatorio_apuracao(campanha_id):
    """Toda a produção do banco da campanha, no período da campanha —
    com uma coluna a mais (valor_apuracao) calculada linha a linha, com
    a mesma regra usada na Visão geral (ver calcular_cenarios_campanha):
      - se NENHUM critério da campanha bater com a linha → valor_apuracao = 0
        (a linha simplesmente não está coberta por nenhuma regra dessa
        campanha, então não conta pra ela);
      - se algum critério bater e estiver marcado "Não contabilizar" →
        valor_apuracao = 0
      - se algum critério bater e tiver % especial definido →
        valor_apuracao = valor da linha (líquido/bruto conforme a
        campanha) * (perc_especial / 100)
      - se algum critério bater sem % especial ("inclusivo") →
        valor_apuracao = valor da linha * (percentual da faixa que a
        campanha atingiu no período, com base na produção total);
      - se a campanha não alcançou nenhuma faixa → valor_apuracao = 0 em
        todas as linhas, inclusive as de % especial."""
    from lib.dashboard import PROJECT as P_DASH, DATASET as D_DASH, TABELA_PRINCIPAL, DATE_COLUMN
    from lib.manutencao import garantir_colunas_map

    garantir_colunas_map()

    campanhas = listar_campanhas()
    campanha = next((c for c in campanhas if c["id"] == campanha_id), None)
    if not campanha:
        raise ValueError("Campanha não encontrada.")

    criterios_todos = listar_criterios()
    criterios_da_campanha = [c for c in criterios_todos if c.get("campanha_id") == campanha_id]
    faixas_metas = campanha.get("faixas_metas") or []

    coluna_valor = "vlr_bruto" if campanha.get("base_producao") == "bruto" else "vlr_liquido"

    client = get_bigquery_client()
    tabela = f"`{P_DASH}.{D_DASH}.{TABELA_PRINCIPAL}`"

    colunas = [
        "data_pagamento", "ade", "banco", "convenio", "produto", "map_convenio", "map_produto",
        "cod_tabela", "tabela", "vlr_liquido", "vlr_bruto", "prazo", "usuario", "cod_corretor", "cod_master", "cod_indicado",
    ]
    # Mesma produção da Visão geral: banco + período + filtros de
    # indicado/convênio/produto da campanha. Antes o relatório ignorava
    # esses filtros e somava a produção inteira do banco — a faixa saía
    # mais alta que a da tela e campanha que não bateu a meta mínima
    # aparecia com valor no relatório.
    where_sql, params = _filtros_producao_campanha(
        campanha, _para_date(campanha.get("data_inicio")), _para_date(campanha.get("data_fim"))
    )
    query = f"""
        SELECT {", ".join(colunas)}
        FROM {tabela}
        WHERE {where_sql}
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    linhas = list(client.query(query, job_config=job_config).result())

    # 1ª passada: soma a produção total do período pra saber qual faixa
    # (percentual de bônus) a campanha atingiu — precisa disso antes de
    # calcular o valor_apuracao de cada contrato individualmente.
    producao_total = sum(float(dict(linha).get(coluna_valor) or 0) for linha in linhas)
    aval = _avaliar_faixas(faixas_metas, producao_total)
    faixa_pct = (aval["faixa_atingida"] or 0.0) / 100 if aval["tier_atingido"] else 0.0
    # Sem critério nenhum cadastrado pra campanha, não há recebimento.
    sem_criterios = len(criterios_da_campanha) == 0

    resultado = []
    for linha in linhas:
        linha_dict = dict(linha)

        criterio_encontrado = _escolher_criterio_da_linha(criterios_da_campanha, linha_dict)

        if not aval["tier_atingido"]:
            # Campanha não alcançou nenhuma faixa → nada é pago, nem os
            # contratos com % especial (mesma regra do cenário atual).
            valor_apuracao = 0.0
        elif sem_criterios or criterio_encontrado is None:
            valor_apuracao = 0.0
        elif criterio_encontrado.get("status") == STATUS_CRITERIO_NAO_CONTABILIZAR:
            valor_apuracao = 0.0
        else:
            # Mesma regra do cenário atual: usa a coluna (líquido/bruto)
            # que O CRITÉRIO escolheu como "Valor base", não a coluna
            # padrão da campanha.
            coluna_valor_criterio = "vlr_bruto" if criterio_encontrado.get("base_producao_criterio") == "bruto" else "vlr_liquido"
            valor_base_linha = float(linha_dict.get(coluna_valor_criterio) or 0)
            if criterio_encontrado.get("perc_especial"):
                valor_apuracao = valor_base_linha * (float(criterio_encontrado["perc_especial"]) / 100)
            else:
                valor_apuracao = valor_base_linha * faixa_pct

        linha_dict["valor_apuracao"] = round(valor_apuracao, 2)
        resultado.append(linha_dict)

    return campanha, resultado
